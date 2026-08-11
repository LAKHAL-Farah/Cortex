import logging
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .. import models
from . import prometheus_client


logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 21
STEP = "5m"
MIN_POINTS_TO_STORE = 2
ROBUST_THRESHOLD = 3.0


METRICS = {
  "cpu_usage": (
    "clamp_max("
    "clamp_min("
    "100 * ("
    "sum by(instance, node) ("
    'rate(node_cpu_seconds_total{mode!="idle"}[5m])'
    ")"
    " / "
    "sum by(instance, node) ("
    "rate(node_cpu_seconds_total[5m])"
    ")"
    "), "
    "0"
    "), "
    "100"
    ")"
),
}


def calculate_mad(
    values: list[float],
    median: float,
) -> float:
    deviations = [
        for value in values
    ]

    return statistics.median(deviations)


def resolve_node(
    db: Session,
    metric_labels: dict,
) -> models.Node | None:
        abs(value - median)
    """
    Essaie de retrouver le nœud Cortex correspondant
    aux labels retournés par Prometheus.
    """

    possible_names = [
        metric_labels.get("node"),
        metric_labels.get("hostname"),
    ]

    instance = metric_labels.get("instance")

    if instance:
        possible_names.append(
            instance.split(":")[0]
        )

    for name in possible_names:
        if not name:
            continue

        node = (
            db.query(models.Node)
            .filter(models.Node.hostname == name)
            .first()
        )

        if node:
            return node

        node = (
            db.query(models.Node)
            .filter(models.Node.ip_address == name)
            .first()
        )

        if node:
            return node

    return None


def compute_baselines(
    db: Session,
    lookback_days: int = LOOKBACK_DAYS,
) -> int:
    end_timestamp = time.time()
    start_timestamp = (
        end_timestamp - lookback_days * 86400
    )

    window_start = datetime.fromtimestamp(
        start_timestamp,
        tz=timezone.utc,
    )

    window_end = datetime.fromtimestamp(
        end_timestamp,
        tz=timezone.utc,
    )

    slots_written = 0

    for metric_name, promql in METRICS.items():
        logger.info(
            "Calcul de la baseline pour %s",
            metric_name,
        )

        try:
            series_list = prometheus_client.query_range(
            promql=promql,
            start=start_timestamp,
            end=end_timestamp,
            step=STEP,
            )

        except Exception:
            logger.exception(
                "Erreur Prometheus pour %s",
                metric_name,
            )
            continue

        buckets: dict[
            str,
            dict[tuple[int, int], list[float]],
        ] = defaultdict(
            lambda: defaultdict(list)
        )

        node_map: dict[str, models.Node] = {}

        for series in series_list:
            labels = series.get("metric", {})

            node = resolve_node(
                db=db,
                metric_labels=labels,
            )

            if node is None:
                logger.warning(
                    "Aucun nœud trouvé pour les labels %s",
                    labels,
                )
                continue

            node_key = str(node.id)
            node_map[node_key] = node

            for timestamp, raw_value in series.get(
                "values",
                [],
            ):
                try:
                    value = float(raw_value)
                    timestamp = float(timestamp)

                except (TypeError, ValueError):
                    continue

                date_value = datetime.fromtimestamp(
                    timestamp,
                    tz=timezone.utc,
                )

                weekday = date_value.weekday()
                hour = date_value.hour

                buckets[node_key][
                    (weekday, hour)
                ].append(value)

        for node_key, slot_map in buckets.items():
            node = node_map[node_key]

            for (
                weekday,
                hour,
            ), values in slot_map.items():

                if len(values) < MIN_POINTS_TO_STORE:
                    continue

                mean = statistics.fmean(values)
                stddev = statistics.pstdev(values)
                median = statistics.median(values)
                mad = calculate_mad(
                    values,
                    median,
                )

                scaled_mad = mad * 1.4826

                lower_bound = (
                    median
                    - ROBUST_THRESHOLD * scaled_mad
                )

                upper_bound = (
                    median
                    + ROBUST_THRESHOLD * scaled_mad
                )

                if metric_name in {
                    "cpu_usage",
                    "ram_usage",
                }:
                    lower_bound = max(
                        0.0,
                        lower_bound,
                    )

                    upper_bound = min(
                        100.0,
                        upper_bound,
                    )

                existing = (
                    db.query(models.MetricBaseline)
                    .filter_by(
                        node_id=node.id,
                        metric_name=metric_name,
                        weekday=weekday,
                        hour=hour,
                    )
                    .first()
                )

                if existing:
                    existing.mean = mean
                    existing.stddev = stddev
                    existing.median = median
                    existing.mad = mad
                    existing.lower_bound = lower_bound
                    existing.upper_bound = upper_bound
                    existing.sample_count = len(values)
                    existing.window_start = window_start
                    existing.window_end = window_end
                    existing.computed_at = (
                        datetime.now(timezone.utc)
                    )

                else:
                    db.add(
                        models.MetricBaseline(
                            node_id=node.id,
                            metric_name=metric_name,
                            weekday=weekday,
                            hour=hour,
                            mean=mean,
                            stddev=stddev,
                            median=median,
                            mad=mad,
                            lower_bound=lower_bound,
                            upper_bound=upper_bound,
                            sample_count=len(values),
                            window_start=window_start,
                            window_end=window_end,
                        )
                    )

                slots_written += 1

        db.commit()

        logger.info(
            "%s terminée — %s créneaux écrits",
            metric_name,
            slots_written,
        )

    return slots_written