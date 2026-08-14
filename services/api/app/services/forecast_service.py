"""
services/api/app/services/forecast_service.py

Serves forecasts built from the models forecast_trainer.py produces. See that
module's docstring and docs/architecture/adr-0006-forecast-pooled-quantile-model.md
for the two-tier design (pooled ML quantile model vs. persistence fallback).

Every prediction, from either tier, goes through the same final clip-and-order
step (`_clip_and_order`) before being returned -- this is what actually fixes
the "expected CPU 120%" bug: no forecast, however it was produced, can leave
this module outside [0, 100] or with an inverted (lower > upper) interval.
"""

from __future__ import annotations

import logging
import os
import threading

import joblib
import numpy as np
import pandas as pd

from .forecast_features import (
    FEATURE_COLUMNS,
    SERVE_HORIZONS_HOURS,
    STEPS_PER_HOUR,
    build_feature_row,
    compute_host_level,
    last_valid_position,
    to_regular_grid,
)
from .forecast_trainer import DATASET_PATH, DEFAULT_HOURLY_VOL, MODELS_DIR

logger = logging.getLogger(__name__)

METRIC_BOUNDS = {
    "cpu_percent": (0.0, 100.0),
    "memory_percent": (0.0, 100.0),
    "disk_percent": (0.0, 100.0),
}

Z_80 = 1.2816  # z-score for an 80% interval (p10 / p90)

# History window loaded per request: covers the longest lag feature (7 days)
# with headroom, plus enough recent points to draw a meaningful "actual"
# trace next to the forecast on the chart.
HISTORY_DAYS = 9
ACTUAL_HISTORY_HOURS = 7 * 24

# A served forecast needs at least this many of the last hour's 12 five-minute
# slots populated before we trust value_now/short-lag features enough to use
# the ML tier -- otherwise a host that just came back from an outage (or just
# joined) falls through to the fallback tier instead of extrapolating from a
# couple of stale/interpolated points.
MIN_SERVE_DENSITY_1H = 6

_dataset_cache_lock = threading.Lock()
_dataset_cache: dict = {"mtime": None, "df": None}

_file_cache_lock = threading.Lock()
_file_cache: dict = {}  # path -> (mtime, loaded_object)


def _load_dataset() -> pd.DataFrame | None:
    if not os.path.isfile(DATASET_PATH):
        return None
    mtime = os.path.getmtime(DATASET_PATH)
    with _dataset_cache_lock:
        if _dataset_cache["mtime"] != mtime:
            _dataset_cache["df"] = pd.read_csv(DATASET_PATH, parse_dates=["timestamp"])
            _dataset_cache["mtime"] = mtime
        return _dataset_cache["df"]


def _load_cached(path: str):
    if not os.path.isfile(path):
        return None
    mtime = os.path.getmtime(path)
    with _file_cache_lock:
        cached = _file_cache.get(path)
        if cached is None or cached[0] != mtime:
            _file_cache[path] = (mtime, joblib.load(path))
        return _file_cache[path][1]


def _load_grid(hostname: str, metric_name: str) -> pd.Series | None:
    df = _load_dataset()
    if df is None:
        return None
    mask = (df["hostname"] == hostname) & (df["metric"] == metric_name)
    host_df = df.loc[mask, ["timestamp", "value"]]
    if host_df.empty:
        return None
    cutoff = host_df["timestamp"].max() - pd.Timedelta(days=HISTORY_DAYS)
    host_df = host_df[host_df["timestamp"] >= cutoff]
    return to_regular_grid(host_df)


def _load_model_bundle(metric_name: str) -> dict | None:
    return _load_cached(os.path.join(MODELS_DIR, f"{metric_name}_global_quantile.pkl"))


def _load_fallback_stats(metric_name: str, hostname: str) -> dict | None:
    all_stats = _load_cached(os.path.join(MODELS_DIR, f"{metric_name}_fallback_stats.pkl"))
    return (all_stats or {}).get(hostname)


def _has_enough_density(grid: pd.Series, anchor_pos: int) -> bool:
    start = max(0, anchor_pos - 11)
    window = grid.iloc[start:anchor_pos + 1]
    return int(window.notna().sum()) >= MIN_SERVE_DENSITY_1H


def _clip_and_order(q10: np.ndarray, q50: np.ndarray, q90: np.ndarray, bounds: tuple[float, float]):
    lo, hi = bounds
    q10 = np.clip(q10, lo, hi)
    q50 = np.clip(q50, lo, hi)
    q90 = np.clip(q90, lo, hi)
    # Independently-fit quantile models can "cross" (e.g. p10 > p50 near the
    # 0/100 edges where all three saturate together) -- sort per-point rather
    # than serve an inverted interval.
    stacked = np.sort(np.stack([q10, q50, q90], axis=0), axis=0)
    return stacked[0], stacked[1], stacked[2]


def _forecast_ml(grid: pd.Series, anchor_pos: int, host_level: float, bundle: dict, bounds):
    rows = [build_feature_row(grid, anchor_pos, h, host_level) for h in SERVE_HORIZONS_HOURS]
    X = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    q10 = bundle["models"]["q10"].predict(X)
    q50 = bundle["models"]["q50"].predict(X)
    q90 = bundle["models"]["q90"].predict(X)
    return _clip_and_order(q10, q50, q90, bounds)


def _seasonal_reference(grid: pd.Series, anchor_pos: int, horizon_hours: int) -> float | None:
    """Same-hour-N-days-ago reference for the fallback tier: walks the target
    time back in whole 24h steps until it lands at or before 'now', so
    'tomorrow at 3pm' is compared against 3pm today rather than just held
    flat. Returns None (caller falls back to plain persistence) when the host
    doesn't have that much history yet -- this is what actually keeps the
    fallback tier honest for a brand-new host, rather than reaching for data
    that doesn't exist.

    forecast_benchmark.ipynb backtested this directly against plain
    persistence (its `seasonal_naive_24h` candidate): for hosts with at least
    a day of history, using the previous day's value at the same time of day
    clearly beat holding the last value flat, which is why the fallback tier
    uses this instead of pure persistence."""
    days_back = int(np.ceil(horizon_hours / 24))
    ref_offset_hours = horizon_hours - 24 * days_back  # always <= 0 (causal)
    ref_pos = anchor_pos + int(round(ref_offset_hours * STEPS_PER_HOUR))
    if ref_pos < 0:
        return None
    ref_val = grid.iloc[ref_pos]
    return float(ref_val) if pd.notna(ref_val) else None


def _forecast_fallback(grid: pd.Series, anchor_pos: int, hourly_vol: float, bounds):
    last_value = float(grid.iloc[anchor_pos])

    def _median_for(h):
        ref = _seasonal_reference(grid, anchor_pos, h)
        return last_value if ref is None else ref

    medians = np.array([_median_for(h) for h in SERVE_HORIZONS_HOURS], dtype=float)
    horizons = np.array(SERVE_HORIZONS_HOURS, dtype=float)
    spread = Z_80 * hourly_vol * np.sqrt(horizons)
    return _clip_and_order(medians - spread, medians, medians + spread, bounds)


def _actual_series(grid: pd.Series) -> list[dict]:
    """Hourly-resampled recent actuals, for plotting alongside the forecast."""
    if grid.empty:
        return []
    cutoff = grid.index.max() - pd.Timedelta(hours=ACTUAL_HISTORY_HOURS)
    recent = grid[grid.index >= cutoff]
    hourly = recent.resample("1h").mean().dropna()
    return [{"timestamp": ts.isoformat(), "value": round(float(v), 2)} for ts, v in hourly.items()]


def get_forecast(hostname: str, metric_name: str) -> dict | None:
    """`hostname` is the identifier forecast_dataset_builder wrote into the
    CSV (the Prometheus `instance` label's host portion) -- the router
    translates the logical hostname the API/UI use into that identifier
    before calling this, same as before this rewrite."""
    bounds = METRIC_BOUNDS.get(metric_name)
    if bounds is None:
        return None

    grid = _load_grid(hostname, metric_name)
    if grid is None or grid.empty:
        logger.warning("Aucune donnée pour %s / %s", hostname, metric_name)
        return None

    anchor_pos = last_valid_position(grid)
    if anchor_pos is None:
        return None

    host_level = compute_host_level(grid)
    bundle = _load_model_bundle(metric_name)

    if bundle is not None and _has_enough_density(grid, anchor_pos):
        q10, q50, q90 = _forecast_ml(grid, anchor_pos, host_level, bundle, bounds)
        model_type = "ml_quantile"
    else:
        stats = _load_fallback_stats(metric_name, hostname)
        hourly_vol = (stats or {}).get("hourly_vol") or DEFAULT_HOURLY_VOL[metric_name]
        q10, q50, q90 = _forecast_fallback(grid, anchor_pos, hourly_vol, bounds)
        model_type = "fallback_seasonal_persistence"

    now = grid.index[anchor_pos]
    forecast_points = [
        {
            "horizon_hours": h,
            "timestamp": (now + pd.Timedelta(hours=h)).isoformat(),
            "predicted": round(float(mid), 2),
            "lower": round(float(lo), 2),
            "upper": round(float(hi), 2),
        }
        for h, lo, mid, hi in zip(SERVE_HORIZONS_HOURS, q10, q50, q90)
    ]

    return {
        "hostname": hostname,
        "metric": metric_name,
        "model_type": model_type,
        "generated_at": now.isoformat(),
        "n_points_used": int(grid.notna().sum()),
        "forecast": forecast_points,
        "actual": _actual_series(grid),
    }
