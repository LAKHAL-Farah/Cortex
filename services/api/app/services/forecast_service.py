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
    MAX_HORIZON_DAYS,
    MIN_HORIZON_DAYS,
    STEPS_PER_HOUR,
    build_feature_row,
    build_serve_horizons,
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

# Default forecast horizon (2.8: selectable up to MAX_HORIZON_DAYS = 90).
DEFAULT_HORIZON_DAYS = 7

# Ceiling used for a model bundle trained before 2.8 (no
# max_supported_horizon_hours key yet) -- matches the old fixed 7-day
# SERVE_HORIZONS_HOURS list exactly, so an un-retrained bundle keeps behaving
# exactly as it did before this feature existed.
LEGACY_ML_MAX_HORIZON_HOURS = 168.0

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


def _forecast_ml(grid: pd.Series, anchor_pos: int, host_level: float, bundle: dict, bounds, horizons):
    rows = [build_feature_row(grid, anchor_pos, h, host_level) for h in horizons]
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


def _forecast_fallback(grid: pd.Series, anchor_pos: int, hourly_vol: float, bounds, horizons):
    """Seasonal-persistence tier. Note this is what 2.8's long-horizon
    extension also uses (see get_forecast) for any horizon past whatever the
    ML bundle actually has training support for -- `spread` grows with
    `sqrt(horizon)` unbounded, so it keeps widening the way "we genuinely
    know less the further out we look" should look, right up until
    `_clip_and_order` saturates it at the full [0, 100] range for very long
    horizons on a volatile metric. That saturation is the honest answer for
    "what's CPU doing in 90 days", not a bug to tighten."""
    last_value = float(grid.iloc[anchor_pos])

    def _median_for(h):
        ref = _seasonal_reference(grid, anchor_pos, h)
        return last_value if ref is None else ref

    medians = np.array([_median_for(h) for h in horizons], dtype=float)
    horizons_arr = np.array(horizons, dtype=float)
    spread = Z_80 * hourly_vol * np.sqrt(horizons_arr)
    return _clip_and_order(medians - spread, medians, medians + spread, bounds)


def _forecast_long_horizon_extension(
    grid: pd.Series,
    anchor_pos: int,
    hourly_vol: float,
    bounds,
    horizons,
    boundary_hours: float,
    boundary_median: float,
    boundary_half_width: float,
):
    """Continues a forecast trajectory past `boundary_hours` -- the longest
    horizon the ML tier actually had training support for -- using the same
    seasonal-persistence median the fallback tier uses, but growing the
    confidence interval *from where the ML tier left off*
    (`boundary_half_width`) rather than restarting the sqrt(horizon) growth
    from zero. Without this, the band would jump straight from the ML
    tier's (typically narrow) width to the fallback tier's full sqrt(h)
    width at the very next served point -- technically wider, but a visual
    cliff rather than the progressively widening band 2.8 is asking for.
    Still saturates to the full metric range for horizons far enough out
    that `boundary_half_width + z*vol*sqrt(extra_hours)` exceeds it -- that
    saturation is the honest answer, not something to soften further."""

    def _median_for(h):
        ref = _seasonal_reference(grid, anchor_pos, h)
        return boundary_median if ref is None else ref

    medians = np.array([_median_for(h) for h in horizons], dtype=float)
    extra_hours = np.array([h - boundary_hours for h in horizons], dtype=float)
    spread = boundary_half_width + Z_80 * hourly_vol * np.sqrt(np.clip(extra_hours, 0, None))
    return _clip_and_order(medians - spread, medians, medians + spread, bounds)


def _actual_series(grid: pd.Series) -> list[dict]:
    """Hourly-resampled recent actuals, for plotting alongside the forecast."""
    if grid.empty:
        return []
    cutoff = grid.index.max() - pd.Timedelta(hours=ACTUAL_HISTORY_HOURS)
    recent = grid[grid.index >= cutoff]
    hourly = recent.resample("1h").mean().dropna()
    return [{"timestamp": ts.isoformat(), "value": round(float(v), 2)} for ts, v in hourly.items()]


def get_forecast(hostname: str, metric_name: str, horizon_days: float | int = DEFAULT_HORIZON_DAYS) -> dict | None:
    """`hostname` is the identifier forecast_dataset_builder wrote into the
    CSV (the Prometheus `instance` label's host portion) -- the router
    translates the logical hostname the API/UI use into that identifier
    before calling this, same as before this rewrite.

    `horizon_days` (2.8) is clamped to [MIN_HORIZON_DAYS, MAX_HORIZON_DAYS]
    (1-90) and expanded into a horizon set by build_serve_horizons(). Points
    within whatever this metric's ML bundle actually has training support for
    (`bundle["max_supported_horizon_hours"]`) are served by the pooled
    quantile model same as before; anything past that -- which, for a fresh
    deployment, may be most or all of a 30/90-day request -- is served by the
    same seasonal-persistence math the fallback tier uses, whose interval
    keeps widening with the horizon rather than flattening out the way a tree
    regressor's extrapolation would beyond the range it was fit on. Each
    returned point's `extrapolated` flag says which happened."""
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

    horizon_days = max(MIN_HORIZON_DAYS, min(MAX_HORIZON_DAYS, horizon_days))
    horizons = build_serve_horizons(horizon_days)

    host_level = compute_host_level(grid)
    bundle = _load_model_bundle(metric_name)

    if bundle is not None and _has_enough_density(grid, anchor_pos):
        ml_max = float(bundle.get("max_supported_horizon_hours", LEGACY_ML_MAX_HORIZON_HOURS))
        ml_horizons = [h for h in horizons if h <= ml_max]
        long_horizons = [h for h in horizons if h > ml_max]

        if ml_horizons:
            q10_ml, q50_ml, q90_ml = _forecast_ml(grid, anchor_pos, host_level, bundle, bounds, ml_horizons)
        else:
            q10_ml = q50_ml = q90_ml = np.array([])

        if long_horizons:
            stats = _load_fallback_stats(metric_name, hostname)
            hourly_vol = (stats or {}).get("hourly_vol") or DEFAULT_HOURLY_VOL[metric_name]
            if len(q50_ml):
                boundary_hours = float(ml_horizons[-1])
                boundary_median = float(q50_ml[-1])
                boundary_half_width = float(q90_ml[-1] - q50_ml[-1])
            else:
                # No ML horizons at all (bundle exists but supports nothing
                # in this request's range) -- start the growth from "now",
                # same as the pure-fallback branch below.
                boundary_hours = 0.0
                boundary_median = float(grid.iloc[anchor_pos])
                boundary_half_width = 0.0
            q10_lh, q50_lh, q90_lh = _forecast_long_horizon_extension(
                grid, anchor_pos, hourly_vol, bounds, long_horizons,
                boundary_hours, boundary_median, boundary_half_width,
            )
        else:
            q10_lh = q50_lh = q90_lh = np.array([])

        # ml_horizons/long_horizons are both sub-slices of the same sorted
        # `horizons` list, split at ml_max -- concatenating stays sorted.
        horizons_served = ml_horizons + long_horizons
        q10 = np.concatenate([q10_ml, q10_lh])
        q50 = np.concatenate([q50_ml, q50_lh])
        q90 = np.concatenate([q90_ml, q90_lh])
        extrapolated_flags = [False] * len(ml_horizons) + [True] * len(long_horizons)
        # A bundle can technically have zero rows of support even at its
        # shortest horizon (e.g. right after MIN_TRAINING_ROWS was cleared by
        # rows concentrated at other horizons) -- don't claim "ml_quantile"
        # for a response that didn't actually use it anywhere.
        model_type = "ml_quantile" if ml_horizons else "fallback_seasonal_persistence"
    else:
        stats = _load_fallback_stats(metric_name, hostname)
        hourly_vol = (stats or {}).get("hourly_vol") or DEFAULT_HOURLY_VOL[metric_name]
        q10, q50, q90 = _forecast_fallback(grid, anchor_pos, hourly_vol, bounds, horizons)
        model_type = "fallback_seasonal_persistence"
        horizons_served = horizons
        extrapolated_flags = [False] * len(horizons)  # whole response is already the honest fallback tier

    now = grid.index[anchor_pos]
    forecast_points = [
        {
            "horizon_hours": h,
            "timestamp": (now + pd.Timedelta(hours=h)).isoformat(),
            "predicted": round(float(mid), 2),
            "lower": round(float(lo), 2),
            "upper": round(float(hi), 2),
            "extrapolated": bool(ex),
        }
        for h, lo, mid, hi, ex in zip(horizons_served, q10, q50, q90, extrapolated_flags)
    ]

    return {
        "hostname": hostname,
        "metric": metric_name,
        "model_type": model_type,
        "generated_at": now.isoformat(),
        "n_points_used": int(grid.notna().sum()),
        "horizon_days": int(horizon_days),
        "max_horizon_hours": max(horizons_served) if horizons_served else 0,
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


def get_threshold_warning(
    hostname: str,
    metric_name: str,
    threshold: float | None = None,
    horizon_days: float | int | None = None,
) -> dict | None:
    """Convenience wrapper combining get_forecast + estimate_threshold_eta for
    a single (hostname, metric) -- used by both the per-resource router
    endpoint and the fleet-wide warnings scan below. `hostname` follows the
    same convention as get_forecast: the dataset/model identifier (i.e. the
    node's ip_address), not the logical hostname -- callers translate.

    `horizon_days` (2.8) lets a caller search for a breach further out than
    the default 7-day forecast -- e.g. "will this hit 90% within 90 days"
    rather than just within a week. Left as None (rather than defaulting to
    7) so a call site that doesn't care about it, like the fleet-wide scan
    below, doesn't need to think about it either."""
    forecast = get_forecast(hostname, metric_name) if horizon_days is None else get_forecast(
        hostname, metric_name, horizon_days=horizon_days
    )
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
