"""
services/api/app/services/anomaly_detector.py

Changes vs. the 1.6 draft, based on notebook comparison
(anomaly_detection_comparison.ipynb):

1. Uses median/MAD ("robust z-score") instead of mean/stddev. Same formula shape,
   same thresholds (2/3/4) -- but resistant to a past incident quietly polluting
   the baseline window (see notebook section 7).
2. No longer silently `continue`s when a (weekday, hour) baseline slot is missing
   or too thin (< MIN_BASELINE_SAMPLES). Falls back to a persisted EWMA estimate
   instead, so the anomaly badge doesn't just disappear during early rollout /
   the sandbox test (see notebook section 8).
3. Records which method produced the score and how much data backed it, on the
   AnomalyFlag row itself, so "confident anomaly" vs "thin-data anomaly" is
   visible later without re-deriving it.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .. import crud, models
from . import prometheus_client

logger = logging.getLogger(__name__)

METRICS = {
    # Grouped by (instance, node) instead of just (instance): "node" is the
    # real hostname label prometheus_sd.py attaches from the nodes table
    # (see regenerate_file_sd()). Grouping only by "instance" dropped it,
    # which is why every flag ended up labeled with a bare IP -- see
    # resolve_hostname() below.
    "cpu_usage": '100 - (avg by(instance, node) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
    "ram_usage": '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
}

# Anomaly severities only ever ratchet up while an AnomalyEvent stays open,
# so a later "medium" tick doesn't overwrite an earlier "critical" peak.
_SEVERITY_RANK = {"normal": 0, "medium": 1, "high": 2, "critical": 3}


def resolve_hostname(db: Session, series_metric: dict) -> str:
    """Best-effort real hostname for a Prometheus series.

    Prefers the "node" label (the actual Node.hostname, attached to every
    scrape target by prometheus_sd.regenerate_file_sd()). Falls back to
    looking the target IP up in the nodes table directly, and only as a
    last resort falls back to the bare IP parsed out of "instance" -- which
    used to be the *only* thing this looked at, so anomalies always showed
    an IP address instead of a hostname.
    """
    node_label = series_metric.get("node")
    if node_label:
        return node_label

    instance_label = series_metric.get("instance", "unknown")
    ip = instance_label.split(":")[0]
    node = crud.get_node_by_ip(db, ip)
    return node.hostname if node else ip

# Severity thresholds, in (robust) standard deviations. Unchanged from the 1.6 draft --
# the notebook's threshold sweep (section 9) confirmed 2/3/4 gives a reasonable
# precision/recall trade-off for this data; revisit once real history accumulates.
THRESHOLDS = {"medium": 2, "high": 3, "critical": 4}

# Scales MAD to be comparable to stddev under a normal distribution.
MAD_SCALE = 1.4826

# A (weekday, hour) baseline slot needs at least this many historical samples
# before we trust it. Below this, fall back to the EWMA estimate instead of
# skipping the host/metric entirely.
MIN_BASELINE_SAMPLES = 10

# EWMA smoothing factor for the fallback path. Lower = slower to adapt, less
# sensitive to noise. This value updates roughly over ~1-2 hours of 1-minute ticks.
EWMA_ALPHA = 0.02


def fetch_instant(promql_query: str) -> list[dict]:
    """Query Prometheus for the current value (no history).

    Delegates to prometheus_client.query(), which honors the PROMETHEUS_URL
    env var (see docker-compose.sandbox.yml). The previous version of this
    function hit a hardcoded "http://prometheus:9090" directly, which only
    happened to work here because that default matches the sandbox's service
    name -- it silently ignored any PROMETHEUS_URL override in other envs.
    """
    return prometheus_client.query(promql_query)


def severity_from_zscore(z: float) -> str:
    abs_z = abs(z)
    if abs_z >= THRESHOLDS["critical"]:
        return "critical"
    if abs_z >= THRESHOLDS["high"]:
        return "high"
    if abs_z >= THRESHOLDS["medium"]:
        return "medium"
    return "normal"


def _get_or_init_ewma(db: Session, hostname: str, metric_name: str, seed_value: float) -> "models.EwmaState":
    state = (
        db.query(models.EwmaState)
        .filter_by(hostname=hostname, metric_name=metric_name)
        .first()
    )
    if state is None:
        # Seed variance with a small non-zero prior (~5% of the seed value) instead
        # of 0. A freshly-seeded state with var=0 would score EVERY next observation
        # as z=0 ("normal") regardless of how extreme it is, until enough ticks pass
        # to build up real spread -- a cold-start blind spot that matters exactly
        # when it's most dangerous (right after a host first comes online).
        prior_std = max(abs(seed_value), 1.0) * 0.05
        state = models.EwmaState(
            hostname=hostname, metric_name=metric_name,
            mean=seed_value, var=prior_std ** 2, updated_at=datetime.utcnow(),
        )
        db.add(state)
    return state


def _update_ewma(state: "models.EwmaState", value: float, alpha: float = EWMA_ALPHA) -> None:
    delta = value - state.mean
    state.mean += alpha * delta
    state.var = (1 - alpha) * (state.var + alpha * delta * delta)
    state.updated_at = datetime.utcnow()


def score_current_value(db: Session, hostname: str, metric_name: str, current_value: float,
                         weekday: int, hour: int) -> tuple[float, str, str, int | None]:
    """
    Returns (z_score, severity, method, baseline_n).
    method is "robust_zscore" when a sufficiently-populated baseline slot exists,
    else "ewma_fallback".
    """
    baseline = (
        db.query(models.Baseline)
        .filter_by(hostname=hostname, metric_name=metric_name, weekday=weekday, hour=hour)
        .first()
    )

    use_baseline = (
        baseline is not None
        and getattr(baseline, "sample_count", None) not in (None,)
        and baseline.sample_count >= MIN_BASELINE_SAMPLES
        and getattr(baseline, "mad", None)
        and baseline.mad > 0
    )

    if use_baseline:
        z = (current_value - baseline.median) / (MAD_SCALE * baseline.mad)
        return z, severity_from_zscore(z), "robust_zscore", baseline.sample_count

    # Fallback: EWMA, no (weekday, hour) table needed.
    state = _get_or_init_ewma(db, hostname, metric_name, seed_value=current_value)
    std = state.var ** 0.5
    z = (current_value - state.mean) / std if std > 0 else 0.0
    _update_ewma(state, current_value)  # keep learning; a real anomaly nudges it, expected tradeoff of a fallback
    return z, severity_from_zscore(z), "ewma_fallback", None


def detect_anomalies(db: Session) -> None:
    now = datetime.utcnow()
    weekday = now.weekday()
    hour = now.hour

    for metric_name, query in METRICS.items():
        current_results = fetch_instant(query)

        for series in current_results:
            hostname = resolve_hostname(db, series["metric"])
            current_value = float(series["value"][1])

            z_score, severity, method, baseline_n = score_current_value(
                db, hostname, metric_name, current_value, weekday, hour
            )

            existing = (
                db.query(models.AnomalyFlag)
                .filter_by(hostname=hostname, metric_name=metric_name)
                .first()
            )
            if existing:
                existing.current_value = current_value
                existing.z_score = z_score
                existing.severity = severity
                existing.method = method
                existing.baseline_n = baseline_n
                existing.detected_at = now
            else:
                db.add(models.AnomalyFlag(
                    hostname=hostname, metric_name=metric_name,
                    current_value=current_value, z_score=z_score,
                    severity=severity, method=method, baseline_n=baseline_n,
                    detected_at=now,
                ))

            # History: keep one open AnomalyEvent per (hostname, metric_name)
            # episode so past anomalies remain visible after they resolve,
            # instead of only ever showing the current state like AnomalyFlag.
            open_event = (
                db.query(models.AnomalyEvent)
                .filter_by(hostname=hostname, metric_name=metric_name, resolved_at=None)
                .first()
            )
            if severity != "normal":
                if open_event is None:
                    db.add(models.AnomalyEvent(
                        hostname=hostname, metric_name=metric_name,
                        current_value=current_value, z_score=z_score,
                        severity=severity, method=method, baseline_n=baseline_n,
                        started_at=now,
                    ))
                elif _SEVERITY_RANK[severity] >= _SEVERITY_RANK[open_event.severity]:
                    # Record the peak reached during this episode, not just
                    # whatever the last tick happened to be.
                    open_event.current_value = current_value
                    open_event.z_score = z_score
                    open_event.severity = severity
                    open_event.method = method
                    open_event.baseline_n = baseline_n
            elif open_event is not None:
                open_event.resolved_at = now

        db.commit()
        logger.info("Anomaly detection pass done for %s", metric_name)
