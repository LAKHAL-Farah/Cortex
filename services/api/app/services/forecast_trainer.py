"""
services/api/app/services/forecast_trainer.py

Trains the forecasting models used by forecast_service.get_forecast().

Two tiers are produced per metric (cpu_percent / memory_percent / disk_percent),
see docs/architecture/adr-0006-forecast-pooled-quantile-model.md for the full
write-up and notebooks/forecast_benchmark.ipynb for the model comparison that
motivated this:

1. "ml_quantile" (preferred): one HistGradientBoostingRegressor per quantile
   (p10/p50/p90), trained on rows *pooled across every host* for that metric.
   Predicts the value at t+h directly from features at t (no recursive
   multi-step forecasting -> no compounding drift), and natively handles NaN
   features, so hosts with gaps or short lag history still get a prediction
   instead of being excluded. Only trained at all if there's enough pooled
   data to be worth it (MIN_TRAINING_ROWS) -- otherwise every host just uses
   the fallback tier below.

2. "fallback_persistence": last observed value, held flat, with a confidence
   interval that widens with sqrt(horizon) using an empirically estimated
   hourly volatility (falls back further to a conservative per-metric default
   if there isn't even enough history to estimate that). This is what a host
   with only a few hours of data gets. It will never invent "120% CPU" --
   the center of the interval is always a value the host actually reported,
   and the interval is clipped to a physically valid range.

Both tiers are always clipped to [0, 100] (these are all percentage metrics)
at serving time, not just at training time, because a model trained on
in-range data can still extrapolate out of range for horizons/feature
combinations it never saw.

2.8 (30/90-day horizon): TRAIN_HORIZONS_HOURS now extends out to 2160h/90d,
so the pooled model *can* learn a long-horizon quantile spread once enough
history has accumulated (forecast_dataset_builder.RETENTION_DAYS = 90 days).
Each bundle records `max_supported_horizon_hours` -- the longest horizon that
actually had `MIN_ROWS_PER_HORIZON` pooled training examples -- and
forecast_service.get_forecast() only serves the ML tier up to that point;
anything a request asks for beyond it is served by the same
seasonal-persistence math as the fallback tier, whose interval keeps widening
with sqrt(horizon) rather than silently flattening the way a tree
regressor's extrapolation would past the range it was fit on.
"""

import logging
import os
from collections import Counter
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .forecast_features import (
    FEATURE_COLUMNS,
    TRAIN_HORIZONS_HOURS,
    compute_host_level,
    make_training_rows,
    rows_to_frame,
    to_regular_grid,
)

logger = logging.getLogger(__name__)

DATASET_PATH = os.getenv("FORECAST_DATASET_PATH", "/app/forecast_dataset.csv")
MODELS_DIR = os.getenv("FORECAST_MODELS_DIR", "/app/models")

METRICS = ["cpu_percent", "memory_percent", "disk_percent"]

METRIC_BOUNDS = {
    "cpu_percent": (0.0, 100.0),
    "memory_percent": (0.0, 100.0),
    "disk_percent": (0.0, 100.0),
}

# Conservative "we genuinely have no idea" priors for the fallback tier's
# hourly volatility, used only when a host doesn't even have enough of its
# own history to estimate its own. CPU is the noisiest of the three by a wide
# margin in practice; disk usage barely moves hour to hour.
DEFAULT_HOURLY_VOL = {
    "cpu_percent": 8.0,
    "memory_percent": 3.0,
    "disk_percent": 0.5,
}

# A host needs at least this many pooled *rows* contributed across all hosts
# before we bother training the ML tier for a metric at all -- below this a
# gradient-boosted model is more likely to memorize noise than learn a
# pattern, and every host just gets the honest fallback instead.
MIN_TRAINING_ROWS = 500

# A single host needs at least this many hours of history to be included as a
# training contributor -- avoids polluting the pool with the first few noisy
# points of a freshly bootstrapped node. It can still be *served* (via the
# fallback tier, or the ML tier once it clears this bar) well before that.
MIN_HOST_HOURS_TO_CONTRIBUTE = 2.0

# A specific horizon (e.g. 2160h/90d) needs at least this many pooled rows
# actually observed at that horizon before forecast_service is allowed to
# serve an ML prediction that far out (2.8). Below this, TRAIN_HORIZONS_HOURS
# includes the horizon, but there simply wasn't enough retained history yet
# for the model to have learned it -- see max_supported_horizon_hours below,
# which is how forecast_service knows to fall back to the honest
# seasonal-persistence extrapolation for those horizons instead of serving a
# tree regressor's extrapolation past the range it was actually fit on.
MIN_ROWS_PER_HORIZON = 30

QUANTILES = {"q10": 0.1, "q50": 0.5, "q90": 0.9}


def _load_raw(dataset_path: str) -> pd.DataFrame:
    return pd.read_csv(dataset_path, parse_dates=["timestamp"])


def _fit_quantile_models(X: pd.DataFrame, y: np.ndarray) -> dict:
    models = {}
    for name, alpha in QUANTILES.items():
        model = HistGradientBoostingRegressor(
            loss="quantile",
            quantile=alpha,
            max_iter=300,
            max_depth=6,
            learning_rate=0.05,
            l2_regularization=0.1,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=15,
            random_state=0,
        )
        model.fit(X, y)
        models[name] = model
    return models


def _fallback_stats_for_host(grid: pd.Series, metric_name: str) -> dict:
    """Persistence-forecast stats for a single host: last known value plus an
    hourly volatility estimate (std of 1-hour differences) used to widen the
    confidence interval with the horizon. Falls back to a per-metric default
    volatility when there isn't enough history to estimate one directly."""
    last_pos = grid.last_valid_index()
    last_value = float(grid.loc[last_pos]) if last_pos is not None else None
    last_ts = last_pos.isoformat() if last_pos is not None else None

    diffs = grid.diff(periods=12).dropna()  # 12 steps = 1 hour, on the 5-min grid
    if len(diffs) >= 20:
        hourly_vol = float(diffs.std())
        if not np.isfinite(hourly_vol) or hourly_vol <= 0:
            hourly_vol = DEFAULT_HOURLY_VOL[metric_name]
    else:
        hourly_vol = DEFAULT_HOURLY_VOL[metric_name]

    return {
        "last_value": last_value,
        "last_ts": last_ts,
        "hourly_vol": hourly_vol,
        "n_points": int(grid.notna().sum()),
    }


def _save_fallback_stats(metric_name: str, stats: dict) -> None:
    os.makedirs(MODELS_DIR, exist_ok=True)
    joblib.dump(stats, os.path.join(MODELS_DIR, f"{metric_name}_fallback_stats.pkl"))


def _delete_ml_bundle(metric_name: str) -> None:
    path = os.path.join(MODELS_DIR, f"{metric_name}_global_quantile.pkl")
    if os.path.isfile(path):
        os.remove(path)


def _train_metric(metric_name: str, host_frames: dict) -> dict:
    """host_frames: {hostname: DataFrame(timestamp, value)} for this metric.
    Returns a summary dict for logging; writes the model bundle (if trained)
    and per-host fallback stats to MODELS_DIR as a side effect."""
    all_rows: list = []
    all_targets: list = []
    fallback_stats: dict = {}

    for hostname, df in host_frames.items():
        if len(df) < 3:
            continue

        grid = to_regular_grid(df)
        if grid.empty:
            continue

        host_level = compute_host_level(grid)
        fallback_stats[hostname] = _fallback_stats_for_host(grid, metric_name)

        span_hours = len(grid) / 12.0
        if span_hours < MIN_HOST_HOURS_TO_CONTRIBUTE:
            continue

        rows, targets = make_training_rows(grid, host_level)
        all_rows.extend(rows)
        all_targets.extend(targets)

    _save_fallback_stats(metric_name, fallback_stats)

    if len(all_rows) < MIN_TRAINING_ROWS:
        logger.info(
            "forecast[%s]: only %d pooled training rows (< %d) -- skipping ML tier, "
            "hosts will use the fallback forecaster",
            metric_name, len(all_rows), MIN_TRAINING_ROWS,
        )
        _delete_ml_bundle(metric_name)
        return {"metric": metric_name, "ml_trained": False, "rows": len(all_rows)}

    X = rows_to_frame(all_rows)
    y = np.asarray(all_targets)

    models = _fit_quantile_models(X, y)

    # Longest horizon with real support in the pooled training rows (2.8) --
    # forecast_service uses this, not TRAIN_HORIZONS_HOURS itself, to decide
    # how far out it can trust this bundle's ML tier before switching a given
    # request's longer horizons over to the seasonal-persistence extension.
    # A bundle with no key at all (pre-2.8) is treated by forecast_service as
    # capped at the old fixed 168h/7d ceiling, so nothing changes for it
    # until it's retrained.
    horizon_counts = Counter(round(float(row["horizon_hours"])) for row in all_rows)
    supported_horizons = [h for h, c in horizon_counts.items() if c >= MIN_ROWS_PER_HORIZON]
    max_supported_horizon_hours = float(max(supported_horizons)) if supported_horizons else float(min(TRAIN_HORIZONS_HOURS))

    bundle = {
        "type": "ml_quantile",
        "metric": metric_name,
        "feature_columns": FEATURE_COLUMNS,
        "train_horizons_hours": TRAIN_HORIZONS_HOURS,
        "max_supported_horizon_hours": max_supported_horizon_hours,
        "bounds": METRIC_BOUNDS[metric_name],
        "n_rows": len(all_rows),
        "n_hosts": len(host_frames),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "models": models,
    }
    os.makedirs(MODELS_DIR, exist_ok=True)
    joblib.dump(bundle, os.path.join(MODELS_DIR, f"{metric_name}_global_quantile.pkl"))

    logger.info(
        "forecast[%s]: trained ML quantile tier on %d rows pooled from %d hosts",
        metric_name, len(all_rows), len(host_frames),
    )
    return {"metric": metric_name, "ml_trained": True, "rows": len(all_rows), "hosts": len(host_frames)}


def train_all_models() -> None:
    if not os.path.isfile(DATASET_PATH):
        logger.warning("Dataset not found (%s), skipping training", DATASET_PATH)
        return

    raw = _load_raw(DATASET_PATH)
    if raw.empty:
        logger.warning("Dataset is empty, skipping training")
        return

    summaries = []
    for metric_name in METRICS:
        metric_df = raw[raw["metric"] == metric_name]
        if metric_df.empty:
            continue
        host_frames = {
            hostname: g[["timestamp", "value"]]
            for hostname, g in metric_df.groupby("hostname")
        }
        summaries.append(_train_metric(metric_name, host_frames))

    logger.info("Forecast training complete: %s", summaries)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    train_all_models()
