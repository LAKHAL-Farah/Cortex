"""
services/api/app/services/forecast_features.py

Feature engineering shared by forecast_trainer.py (offline training) and
forecast_service.py (online serving). Keeping this in one module guarantees
train/serve parity -- the single most common way forecasting pipelines quietly
break is the serving code building a slightly different feature vector than
the one the model was fit on.

Design (see docs/architecture/adr-0006-forecast-pooled-quantile-model.md for
the full rationale):

- Metrics are resampled onto a regular 5-minute grid per (hostname, metric).
  Short gaps (<=15 min) are linearly interpolated; longer gaps are left as
  NaN rather than fabricated.
- Forecasting is *direct*, not recursive: a single model predicts the value
  at `t + horizon_hours` directly from features known at `t`, with
  `horizon_hours` itself passed in as a feature. This avoids the error
  compounding you get from feeding a 1-step model's own output back in for
  hundreds of steps (which is how the old ARIMA(2,1,2) forecaster produced
  things like "120% CPU" 30 days out -- a differenced model extrapolating a
  learned drift term for 8640 steps with nothing keeping it near [0, 100]).
- Training rows are pooled *across hosts* for a given metric. A single node
  rarely has enough history on its own to learn a daily/weekly pattern
  reliably; pooling every host's history into one model per metric turns
  "3 nodes with 4 days each" into one dataset with a real sample size, and
  a `host_level` feature lets the pooled model still express per-host
  offsets (a node that idles at 70% vs one that idles at 5%).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STEP_MINUTES = 5
STEPS_PER_HOUR = 60 // STEP_MINUTES  # 12

# How far back each lag/rolling feature looks, in hours.
LAG_HOURS = [1, 3, 6, 24, 168]
ROLL_HOURS = [1, 6, 24]

# Horizons sampled when building the *training* set. Log-ish spaced so the
# model sees short-range (high signal) and long-range (high uncertainty)
# examples without paying for 168 separate horizon values per anchor.
TRAIN_HORIZONS_HOURS = [1, 2, 4, 6, 9, 12, 18, 24, 36, 48, 72, 96, 120, 144, 168]

# Horizons actually returned by the API: hourly resolution for the first day
# (matches the "next 24h" part of the spec), daily resolution out to 7 days
# (matches "next ... 7 days") -- keeps the payload/chart to ~30 points
# instead of 168.
SERVE_HORIZONS_HOURS = list(range(1, 25)) + [48, 72, 96, 120, 144, 168]

FEATURE_COLUMNS = [
    "value_now",
    "lag_1h", "lag_3h", "lag_6h", "lag_24h", "lag_168h",
    "roll_mean_1h", "roll_std_1h",
    "roll_mean_6h", "roll_std_6h",
    "roll_mean_24h", "roll_std_24h",
    "slope_24h",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "host_level",
    "horizon_hours", "log_horizon",
    "target_hour_sin", "target_hour_cos", "target_dow_sin", "target_dow_cos",
]


def to_regular_grid(df: pd.DataFrame) -> pd.Series:
    """df has columns timestamp,value for a single (hostname, metric), any order.
    Returns a Series indexed on a complete 5-minute grid spanning the observed
    range. Gaps of <=15 minutes are linearly interpolated (typical scrape
    jitter); longer gaps are left as NaN so the model doesn't get fed
    fabricated data for real outages/downtime."""
    s = df.set_index("timestamp")["value"].sort_index()
    s = s[~s.index.duplicated(keep="last")]
    if s.empty:
        return s
    full_index = pd.date_range(s.index.min(), s.index.max(), freq=f"{STEP_MINUTES}min")
    s = s.reindex(full_index)
    s = s.interpolate(method="linear", limit=3, limit_area="inside")
    return s


def compute_host_level(grid: pd.Series) -> float:
    """Cheap per-host offset: this host's overall mean for the metric. Lets a
    pooled global model still distinguish 'a node that idles at 70%' from
    'a node that idles at 5%' without needing per-host models."""
    mean = grid.mean(skipna=True)
    return float(mean) if pd.notna(mean) else float("nan")


def _cyclical(value: float, period: float) -> tuple[float, float]:
    angle = 2 * np.pi * (value / period)
    return float(np.sin(angle)), float(np.cos(angle))


def last_valid_position(grid: pd.Series) -> int | None:
    """Position of the most recent non-NaN point, i.e. 'now' for feature
    purposes. None if the series has no data at all."""
    valid = grid.notna()
    if not valid.any():
        return None
    return int(np.flatnonzero(valid.to_numpy())[-1])


def _static_features(grid: pd.Series, anchor_pos: int, host_level: float) -> dict | None:
    """Causal features computed using only grid[:anchor_pos + 1] -- nothing
    from the future leaks in. Returns None if there isn't even a current
    value to anchor on."""
    value_now = grid.iloc[anchor_pos]
    if pd.isna(value_now):
        return None

    ts = grid.index[anchor_pos]
    feats: dict = {"value_now": float(value_now)}

    for h in LAG_HOURS:
        pos = anchor_pos - h * STEPS_PER_HOUR
        val = grid.iloc[pos] if pos >= 0 else np.nan
        feats[f"lag_{h}h"] = float(val) if pd.notna(val) else np.nan

    for h in ROLL_HOURS:
        start = max(0, anchor_pos - h * STEPS_PER_HOUR + 1)
        window = grid.iloc[start:anchor_pos + 1]
        feats[f"roll_mean_{h}h"] = float(window.mean()) if window.notna().any() else np.nan
        feats[f"roll_std_{h}h"] = float(window.std()) if window.notna().sum() > 1 else np.nan

    lag_24 = feats["lag_24h"]
    feats["slope_24h"] = (value_now - lag_24) / 24.0 if pd.notna(lag_24) else np.nan

    hs, hc = _cyclical(ts.hour + ts.minute / 60.0, 24)
    ds, dc = _cyclical(float(ts.dayofweek), 7)
    feats["hour_sin"], feats["hour_cos"] = hs, hc
    feats["dow_sin"], feats["dow_cos"] = ds, dc
    feats["host_level"] = host_level
    return feats


def _target_time_features(target_ts: pd.Timestamp, horizon_hours: float) -> dict:
    ths, thc = _cyclical(target_ts.hour + target_ts.minute / 60.0, 24)
    tds, tdc = _cyclical(float(target_ts.dayofweek), 7)
    return {
        "horizon_hours": float(horizon_hours),
        "log_horizon": float(np.log1p(horizon_hours)),
        "target_hour_sin": ths,
        "target_hour_cos": thc,
        "target_dow_sin": tds,
        "target_dow_cos": tdc,
    }


def build_feature_row(
    grid: pd.Series, anchor_pos: int, horizon_hours: float, host_level: float
) -> dict | None:
    """Feature row for *serving*: predict horizon_hours ahead of anchor_pos.
    The target is in the future and unknown -- only its calendar position
    (hour-of-day / day-of-week) is used as a feature."""
    feats = _static_features(grid, anchor_pos, host_level)
    if feats is None:
        return None
    target_ts = grid.index[anchor_pos] + pd.Timedelta(hours=horizon_hours)
    feats.update(_target_time_features(target_ts, horizon_hours))
    return feats


def make_training_rows(
    grid: pd.Series, host_level: float, anchor_stride: int = 3
) -> tuple[list[dict], list[float]]:
    """Walks anchors across the grid with the given stride and, for each one,
    emits one training row per horizon in TRAIN_HORIZONS_HOURS whose target is
    actually observed (both features and target must be real data -- no
    imputed targets). `anchor_stride` controls how many overlapping anchors we
    take (3 = every 15 min) to keep the pooled dataset from exploding without
    starving it of examples."""
    n = len(grid)
    rows: list[dict] = []
    targets: list[float] = []

    for anchor_pos in range(0, n, anchor_stride):
        static = _static_features(grid, anchor_pos, host_level)
        if static is None:
            continue
        for h in TRAIN_HORIZONS_HOURS:
            target_pos = anchor_pos + h * STEPS_PER_HOUR
            if target_pos >= n:
                break  # horizons are increasing -- later ones are out of range too
            target_val = grid.iloc[target_pos]
            if pd.isna(target_val):
                continue
            feats = dict(static)
            feats.update(_target_time_features(grid.index[target_pos], h))
            rows.append(feats)
            targets.append(float(target_val))

    return rows, targets


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Turns a list of feature dicts into a DataFrame with a fixed, ordered
    column set -- guards against silently training/predicting on columns in
    the wrong order if a dict ever has extra/missing keys."""
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)
