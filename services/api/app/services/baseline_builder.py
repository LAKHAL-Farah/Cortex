"""
services/api/app/services/baseline_builder.py

Populates the `baselines` table that anomaly_detector.score_current_value()
reads for its "robust_zscore" path.

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

from .. import models
from . import prometheus_client

logger = logging.getLogger(__name__)

METRICS = {
    "cpu_usage": '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
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


def _median_absolute_deviation(values: list[float], median: float) -> float:
    return statistics.median([abs(v - median) for v in values])


def compute_baselines(db: Session, lookback_days: int = LOOKBACK_DAYS) -> int:
    """
    Rebuilds every (hostname, metric_name, weekday, hour) baseline slot from
    Prometheus history and upserts it into the `baselines` table.

    Returns the number of slots written (mainly useful for logging/tests).
    """
    end = time.time()
    start = end - lookback_days * 86400
    slots_written = 0

    for metric_name, promql in METRICS.items():
        try:
            series_list = prometheus_client.query_range(promql, start, end, STEP)
        except Exception:
            logger.exception("baseline_builder: query_range failed for %s", metric_name)
            continue

        # hostname -> (weekday, hour) -> [values]
        buckets: dict[str, dict[tuple[int, int], list[float]]] = defaultdict(lambda: defaultdict(list))

        for series in series_list:
            instance_label = series["metric"].get("instance", "unknown")
            hostname = instance_label.split(":")[0]
            for ts, raw_val in series.get("values", []):
                try:
                    value = float(raw_val)
                except (TypeError, ValueError):
                    continue
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                buckets[hostname][(dt.weekday(), dt.hour)].append(value)

        for hostname, slot_map in buckets.items():
            for (weekday, hour), values in slot_map.items():
                if len(values) < MIN_POINTS_TO_STORE:
                    continue

                median = statistics.median(values)
                mad = _median_absolute_deviation(values, median)
                mean = statistics.fmean(values)
                stddev = statistics.pstdev(values)

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
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(models.Baseline(
                        hostname=hostname, metric_name=metric_name,
                        weekday=weekday, hour=hour,
                        mean=mean, stddev=stddev, median=median, mad=mad,
                        sample_count=len(values),
                    ))
                slots_written += 1

        db.commit()
        logger.info("baseline_builder: refreshed %s (%d slots so far)", metric_name, slots_written)

    return slots_written
