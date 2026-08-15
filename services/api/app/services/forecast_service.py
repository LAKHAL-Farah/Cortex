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

# Default "capacity" threshold per metric used by the threshold-ETA warning
# (2.5: "X will hit threshold in ~N days"). Callers can override per request;
# these are just the sane defaults a dashboard-wide scan uses.
DEFAULT_THRESHOLDS = {
    "cpu_percent": 90.0,
    "memory_percent": 90.0,
    "disk_percent": 90.0,
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


def _interpolate_crossing_hour(h0: float, v0: float, h1: float, v1: float, threshold: float) -> float:
    """Linear interpolation of the horizon (hours) within (h0, h1] at which
    the segment from v0 to v1 crosses `threshold`. Callers only call this on
    a segment they've already established brackets the threshold (v0 <
    threshold <= v1), so this is just where along that segment, not whether
    it happens."""
    if v1 == v0:
        return h1
    frac = (threshold - v0) / (v1 - v0)
    frac = min(max(frac, 0.0), 1.0)
    return h0 + frac * (h1 - h0)


def estimate_threshold_eta(forecast: dict, threshold: float | None = None) -> dict | None:
    """First-passage-time over the median (`predicted`) trajectory `get_forecast`
    already returns: walks forward from now and reports the first horizon at
    which the metric is projected to cross `threshold`, linearly interpolated
    between the two bracketing served points for a smoother N-days estimate
    than snapping to the nearest served horizon.

    This is the answer to adr-0006's "Revisit when" callout -- a hard,
    SLA-style "will we breach X% within N days" bound -- computed as a
    first-passage-time over the existing quantile trajectory rather than a
    dedicated survival model. It rides on whatever `predicted` already means
    for this hostname/metric (ml_quantile or fallback), so it inherits that
    tier's caveats (e.g. a flat fallback trajectory will only "breach" if
    already past threshold).

    Returns None only when there's no threshold configured for this metric
    (no opinion to give). An already-breached metric, or one never projected
    to cross within the served 7-day horizon, still returns a dict --
    `will_breach` distinguishes the two so callers can filter.
    """
    metric = forecast.get("metric")
    thr = threshold if threshold is not None else DEFAULT_THRESHOLDS.get(metric)
    if thr is None:
        return None

    points = forecast.get("forecast") or []
    actual = forecast.get("actual") or []
    if actual:
        now_value = float(actual[-1]["value"])
    elif points:
        # No recent actuals resampled (unlikely, but be defensive) -- use the
        # nearest served prediction as the closest thing to "now" we have.
        now_value = float(points[0]["predicted"])
    else:
        return None

    base = {
        "threshold": float(thr),
        "current_value": round(now_value, 2),
    }

    if now_value >= thr:
        return {
            **base,
            "will_breach": True,
            "already_breached": True,
            "eta_hours": 0.0,
            "eta_days": 0.0,
            "crossing_timestamp": forecast.get("generated_at"),
        }

    prev_h, prev_v = 0.0, now_value
    for p in points:
        h, v = float(p["horizon_hours"]), float(p["predicted"])
        if v >= thr:
            eta_h = _interpolate_crossing_hour(prev_h, prev_v, h, v, thr)
            crossing_ts = pd.Timestamp(forecast["generated_at"]) + pd.Timedelta(hours=eta_h)
            return {
                **base,
                "will_breach": True,
                "already_breached": False,
                "eta_hours": round(eta_h, 1),
                "eta_days": round(eta_h / 24.0, 1),
                "crossing_timestamp": crossing_ts.isoformat(),
            }
        prev_h, prev_v = h, v

    return {
        **base,
        "will_breach": False,
        "already_breached": False,
        "eta_hours": None,
        "eta_days": None,
        "crossing_timestamp": None,
    }


def get_threshold_warning(hostname: str, metric_name: str, threshold: float | None = None) -> dict | None:
    """Convenience wrapper combining get_forecast + estimate_threshold_eta for
    a single (hostname, metric) -- used by both the per-resource router
    endpoint and the fleet-wide warnings scan below. `hostname` follows the
    same convention as get_forecast: the dataset/model identifier (i.e. the
    node's ip_address), not the logical hostname -- callers translate."""
    forecast = get_forecast(hostname, metric_name)
    if forecast is None:
        return None
    eta = estimate_threshold_eta(forecast, threshold)
    if eta is None:
        return None
    return {
        "hostname": hostname,
        "metric": metric_name,
        "model_type": forecast["model_type"],
        **eta,
    }


def list_threshold_warnings(
    nodes: list[tuple[str, str]],
    metric_names: list[str] | None = None,
    threshold: float | None = None,
) -> list[dict]:
    """Scans every (logical_hostname, dataset_hostname) pair in `nodes` across
    `metric_names` (defaults to every metric with a DEFAULT_THRESHOLDS entry)
    and returns only the warnings actually projected to breach within the
    served horizon (`will_breach=True`), soonest first. This is what backs
    the dashboard's "X will hit threshold in ~N days" banner (2.5) --
    already-fine resources are filtered out here rather than left for the
    frontend to sift through."""
    metric_names = metric_names or list(DEFAULT_THRESHOLDS.keys())
    warnings: list[dict] = []
    for logical_hostname, dataset_hostname in nodes:
        for metric_name in metric_names:
            warning = get_threshold_warning(dataset_hostname, metric_name, threshold)
            if warning is None or not warning["will_breach"]:
                continue
            warning["hostname"] = logical_hostname  # swap dataset id back for logical hostname
            warnings.append(warning)

    warnings.sort(key=lambda w: w["eta_hours"] if w["eta_hours"] is not None else float("inf"))
    return warnings
