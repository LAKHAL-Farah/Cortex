"""
services/api/app/services/baseline_builder.py

Populates the `baselines` table that anomaly_detector.score_current_value()
reads for its "robust_zscore" path, and (story 3.8) the `role_baselines`
table for the intermediate "what's normal for this role" tier -- used when
a host doesn't have enough of its own history yet but still deserves a
context-aware comparison instead of falling straight through to EWMA.

Why this file didn't exist: the 1.6 draft doc explicitly deferred baseline
computation to a separate "1.8 baseline job" (see anomaly_detection_comparison
.ipynb, section 4: "mirrors the `baselines` table from 1.8"). anomaly_detector.py
was written defensively against that -- score_current_value() already falls
back to EWMA cleanly when `baselines` has no matching (or too-thin) row -- but
nothing in 1.6 ever actually wrote to `baselines`. That's the entire reason
every AnomalyFlag came back with "method": "ewma_fallback": the table was
never wrong, it was just always empty.

This job closes that gap by computing baselines directly from Prometheus's
own history (via query_range) instead of a separate ingestion pipeline, using
the same median/MAD approach the notebook recommended over naive mean/stddev
(section 7: a single uncleaned incident in the window barely moves MAD, but
can triple stddev).

Expected behavior on a freshly-started sandbox: Prometheus itself has little
retained history right after startup, so most (weekday, hour) slots will still
have fewer than MIN_BASELINE_SAMPLES rows and anomaly_detector will correctly
keep using the EWMA fallback for those -- that's not a bug, it's the same
cold-start behavior the notebook's section 8 called out. As the sandbox keeps
running (and Prometheus's own retention window fills in), slots pick up
samples and score_current_value() switches them over to robust_zscore on its
own; no code changes needed at that point.
"""

import logging
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .. import crud, models
from . import prometheus_client
from .anomaly_detector import resolve_hostname

logger = logging.getLogger(__name__)

METRICS = {
    "cpu_usage": '100 - (avg by(instance, node) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
    "ram_usage": '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
}

# How far back to look when rebuilding baselines. Prometheus will simply
# return less data than this if its own retention window is shorter (e.g. a
# brand new sandbox) -- that's fine, it just means slots stay thin for longer.
LOOKBACK_DAYS = 21

# Resolution for the history pull. At 5-minute steps, a single occurrence of
# a given (weekday, hour) contributes up to 12 samples -- enough on its own to
# clear MIN_BASELINE_SAMPLES (10) in anomaly_detector.py without needing weeks
# of history, while still keeping the query_range response small over 21 days.
STEP = "5m"

# A slot needs at least this many raw points before median/MAD mean anything;
# well below anomaly_detector.MIN_BASELINE_SAMPLES on purpose, since it's fine
# to store a thin/unproven slot -- it's score_current_value() that decides
# whether to trust it, so both modules should not have to agree on one magic
# number.
MIN_POINTS_TO_STORE = 2

# Same idea, for the per-role slots (story 3.8). Kept as its own constant
# rather than reusing MIN_POINTS_TO_STORE -- a role slot pools points across
# every host of that role, so in practice it clears this floor much sooner;
# a separate name just makes that a deliberate choice, not a coincidence.
MIN_POINTS_TO_STORE_ROLE = 2


def _median_absolute_deviation(values: list[float], median: float) -> float:
    return statistics.median([abs(v - median) for v in values])


def compute_baselines(db: Session, lookback_days: int = LOOKBACK_DAYS) -> int:
    """
    Rebuilds every (hostname, metric_name, weekday, hour) baseline slot
    (`baselines` table) AND every (role, metric_name, weekday, hour) slot
    (`role_baselines` table, story 3.8) from Prometheus history, upserting
    both in the same pass since they're built from the same query_range
    results -- no reason to hit Prometheus twice.

    Returns the number of `baselines` slots written (mainly useful for
    logging/tests) -- role_baselines slots written are logged separately
    but not included in this count, to keep the existing return value's
    meaning unchanged for any caller already relying on it.
    """
    end = time.time()
    start = end - lookback_days * 86400
    slots_written = 0
    role_slots_written = 0

    # hostname -> role, resolved once per pass and cached here rather than
    # re-querying Postgres per sample -- a node's role doesn't change
    # mid-pass, and a 21-day/5m query_range can return a lot of samples.
    role_by_hostname: dict[str, str | None] = {}

    def _role_for(hostname: str) -> str | None:
        if hostname not in role_by_hostname:
            node = crud.get_node_by_hostname(db, hostname)
            role_by_hostname[hostname] = node.role if node else None
        return role_by_hostname[hostname]

    for metric_name, promql in METRICS.items():
        try:
            series_list = prometheus_client.query_range(promql, start, end, STEP)
        except Exception:
            logger.exception("baseline_builder: query_range failed for %s", metric_name)
            continue

        # hostname -> (weekday, hour) -> [values]
        buckets: dict[str, dict[tuple[int, int], list[float]]] = defaultdict(lambda: defaultdict(list))
        # hostname -> (weekday, hour) -> {calendar dates seen}. Kept separate
        # from `buckets` because what we need out of it is len(set), not the
        # values themselves.
        day_sets: dict[str, dict[tuple[int, int], set]] = defaultdict(lambda: defaultdict(set))

        # role -> (weekday, hour) -> [values], pooled across every host of
        # that role (story 3.8's "type de charge" tier). A node with no
        # resolvable role is simply left out of this pooling -- it still
        # gets its own per-host baseline above, just no role-level
        # fallback to offer it later.
        role_buckets: dict[str, dict[tuple[int, int], list[float]]] = defaultdict(lambda: defaultdict(list))
        # role -> (weekday, hour) -> {hostnames seen}. Same reasoning as
        # day_sets: raw point count alone can come from just one or two
        # hosts, which isn't yet "what's normal for this role".
        role_host_sets: dict[str, dict[tuple[int, int], set]] = defaultdict(lambda: defaultdict(set))

        for series in series_list:
            hostname = resolve_hostname(db, series["metric"])
            role = _role_for(hostname)
            for ts, raw_val in series.get("values", []):
                try:
                    value = float(raw_val)
                except (TypeError, ValueError):
                    continue
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                slot = (dt.weekday(), dt.hour)
                buckets[hostname][slot].append(value)
                day_sets[hostname][slot].add(dt.date())
                if role:
                    role_buckets[role][slot].append(value)
                    role_host_sets[role][slot].add(hostname)

        for hostname, slot_map in buckets.items():
            for (weekday, hour), values in slot_map.items():
                if len(values) < MIN_POINTS_TO_STORE:
                    continue

                median = statistics.median(values)
                mad = _median_absolute_deviation(values, median)
                mean = statistics.fmean(values)
                stddev = statistics.pstdev(values)
                distinct_days = len(day_sets[hostname][(weekday, hour)])

                existing = (
                    db.query(models.Baseline)
                    .filter_by(hostname=hostname, metric_name=metric_name, weekday=weekday, hour=hour)
                    .first()
                )
                if existing:
                    existing.mean = mean
                    existing.stddev = stddev
                    existing.median = median
                    existing.mad = mad
                    existing.sample_count = len(values)
                    existing.distinct_days = distinct_days
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(models.Baseline(
                        hostname=hostname, metric_name=metric_name,
                        weekday=weekday, hour=hour,
                        mean=mean, stddev=stddev, median=median, mad=mad,
                        sample_count=len(values), distinct_days=distinct_days,
                    ))
                slots_written += 1

        for role, slot_map in role_buckets.items():
            for (weekday, hour), values in slot_map.items():
                if len(values) < MIN_POINTS_TO_STORE_ROLE:
                    continue

                median = statistics.median(values)
                mad = _median_absolute_deviation(values, median)
                mean = statistics.fmean(values)
                stddev = statistics.pstdev(values)
                distinct_hosts = len(role_host_sets[role][(weekday, hour)])

                existing = (
                    db.query(models.RoleBaseline)
                    .filter_by(role=role, metric_name=metric_name, weekday=weekday, hour=hour)
                    .first()
                )
                if existing:
                    existing.mean = mean
                    existing.stddev = stddev
                    existing.median = median
                    existing.mad = mad
                    existing.sample_count = len(values)
                    existing.distinct_hosts = distinct_hosts
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(models.RoleBaseline(
                        role=role, metric_name=metric_name,
                        weekday=weekday, hour=hour,
                        mean=mean, stddev=stddev, median=median, mad=mad,
                        sample_count=len(values), distinct_hosts=distinct_hosts,
                    ))
                role_slots_written += 1

        db.commit()
        logger.info(
            "baseline_builder: refreshed %s (%d host slots, %d role slots so far)",
            metric_name, slots_written, role_slots_written,
        )

    return slots_written
