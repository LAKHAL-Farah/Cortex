# ADR-0006: Pooled quantile-regression forecasting, with a persistence fallback

**Status:** Accepted
**Related code:** `services/api/app/services/forecast_features.py`,
`services/api/app/services/forecast_trainer.py`,
`services/api/app/services/forecast_service.py`,
`services/api/app/routers/forecast.py`
**Related notebook:** `notebooks/forecast_benchmark.ipynb`

## Context

The original forecaster fit one ARIMA(2,1,2) or Holt model per `(hostname, metric)`, with as
few as `MIN_POINTS_REQUIRED = 20` five-minute points (100 minutes) to train on, and then called
`.forecast(8640)` to recursively extrapolate 30 days (8640 five-minute steps) out from that. Two
things compounded:

- **Recursive multi-step extrapolation from a differenced model has no mechanism keeping it
  near a sane range.** ARIMA(·,1,·) forecasts a learned drift term forward; 8640 steps of that
  drift, fit on ~100 minutes of noisy data, is what produced things like "expected CPU 120%" --
  it's not a bug in any single line, it's what that model class does when asked to extrapolate
  that far from that little data.
- **Nothing clipped the output.** Even a well-behaved forecast for a percentage metric needs a
  final `clip(0, 100)` before being shown to anyone; the old code had none.
- **No confidence interval was ever computed**, despite the product requirement calling for one
  -- the frontend only ever had three point values (`tomorrow` / `7_days` / `30_days`) to show.
- **Per-host models can't work around thin data.** A brand-new node, or one that's only been up
  for a few hours, will never have 20+ points of *its own* history early on -- and every node
  starts that way at some point.

## Decision

**Forecasting moved to two tiers, both living in `forecast_service.get_forecast()`, chosen per
request based on how much recent data the requested host actually has:**

1. **`ml_quantile` (preferred):** one `HistGradientBoostingRegressor` per quantile (p10/p50/p90),
   trained **pooled across every host** for a given metric (`cpu_percent` / `memory_percent` /
   `disk_percent` each get one model per quantile, not one per host). Forecasting is **direct**:
   the model predicts the value at `t + horizon_hours` straight from features known at `t`, with
   `horizon_hours` passed in as a feature -- there is no recursive step-by-step rollout, so
   there's nothing to compound. See `forecast_features.py` for the full feature set (lags,
   rolling stats, cyclical hour-of-day/day-of-week at both the anchor and the target time, a
   `host_level` offset, and the horizon itself).

2. **`fallback_persistence`:** used whenever a host doesn't have enough *recent* density to trust
   short-lag features (see `MIN_SERVE_DENSITY_1H` in `forecast_service.py`), or no pooled model
   exists yet for that metric at all. This just holds the last observed value flat and widens a
   confidence interval with `z * hourly_vol * sqrt(horizon)`, where `hourly_vol` is either
   estimated from the host's own recent hour-to-hour deltas or, failing that, a conservative
   per-metric prior (`DEFAULT_HOURLY_VOL`). It will never invent a value the host hasn't actually
   reported.

**Every prediction from either tier is clipped to `[0, 100]` and quantile-ordered
(`_clip_and_order`) before being returned** -- this, not model choice, is the actual fix for
"120% CPU": whatever a model outputs, the last thing that happens before the API responds is a
clip and a sanity sort of the three quantiles (independently-fit quantile regressors can "cross"
near the edges of the range).

**Pooling across hosts is the direct answer to "our biggest problem is data availability."** A
single host with 4 days of history is thin; three hosts with 4-10 days each, pooled, is a dataset
a gradient-boosted model can actually learn a daily/weekly pattern from. `HistGradientBoosting
Regressor` was also specifically chosen (over e.g. plain `GradientBoostingRegressor` or XGBoost)
because it handles `NaN` features natively via a learned per-split "missing goes left/right"
direction -- a host with gaps or without a full 7 days of lag history doesn't need to be dropped
from training or prediction, it just has some `NaN` lag features and the model routes around
them. See the notebook for the comparison against alternatives that motivated this pick.

## Rationale (see the notebook for the numbers)

`notebooks/forecast_benchmark.ipynb` backtests, on synthetic-but-realistic multi-host data with
daily+weekly seasonality and injected volatility differences, using walk-forward validation
(train on the past, predict a held-out future window, never the reverse):

- **Naive persistence** and **seasonal-naive** as trivial baselines.
- **Holt-Winters** (the old model class for two of the three metrics) evaluated the same way the
  old code used it, single-host.
- **Single-host `HistGradientBoostingRegressor`** (no pooling) to isolate the effect of pooling
  from the effect of switching model families.
- **Pooled `HistGradientBoostingRegressor`** (the chosen approach).
- **Pooled `XGBRegressor`** with quantile objective, as the other tree-ensemble candidate
  explicitly asked about, to check it isn't meaningfully better before taking on an extra heavy
  dependency in the API image.

Models are scored on MAE/RMSE for the median forecast and **pinball loss** (the correct scoring
rule for quantile forecasts -- MAE alone can't tell you whether your confidence interval is
well-calibrated) for p10/p90. The notebook's conclusion section has the actual numbers; the short
version is that pooling helped far more than the choice between HistGradientBoosting and XGBoost
did, which is why the shipped code takes the pooling for granted and picks
`HistGradientBoostingRegressor` for the (small) simplicity/dependency-weight win -- it ships in
scikit-learn, which was already a planned dependency, instead of adding `xgboost` to the API
image for a difference that didn't clear noise in the backtest.

## Consequences

- **API response shape changed.** `GET /api/v1/forecast/{hostname}/{metric}` no longer returns
  `{"forecast": [{"day": "tomorrow"|"7_days"|"30_days", "value": ...}]}`. It now returns hourly
  points for the first 24h and daily points out to 7 days (`SERVE_HORIZONS_HOURS` in
  `forecast_features.py`), each with `predicted`/`lower`/`upper`, plus a `model_type` field and
  an `actual` array of recent hourly-resampled real values for the "prediction vs. actual" chart.
  The frontend (`services/web/components/ForecastExplorer.tsx`,
  `services/web/lib/types.ts::ForecastResult`) was updated to match.
- **30-day forecasts were dropped.** Direct 30-day-ahead prediction from a pooled model trained
  on at most ~90 days of retained history (`RETENTION_DAYS` in `forecast_dataset_builder.py`) has
  very little support in the training data at that horizon; the product ask was specifically
  "24h-7 days", so the horizon set stops at 7 days rather than serving a confidently-wrong number
  that far out. Revisit if `RETENTION_DAYS` grows substantially.
- **Training runs hourly now** (`FORECAST_TRAINING_INTERVAL_SECONDS` in `main.py`, was daily) --
  cheap enough at this data volume, and needed so the "value_now"/short-lag features actually
  reflect the last hour rather than yesterday's training snapshot.
- **`statsmodels` was dropped** from `services/api/requirements.txt` (nothing else in the API
  used it); `scikit-learn` and `joblib` were added.
- Model artifacts changed shape: `{metric}_{hostname}.pkl` (per-host ARIMA/Holt payloads) is
  replaced by `{metric}_global_quantile.pkl` (one pooled bundle per metric, holding all three
  quantile models) and `{metric}_fallback_stats.pkl` (per-host persistence stats). Old `.pkl`
  files under `FORECAST_MODELS_DIR` are simply ignored now and can be deleted.

## Revisit when

- A given metric consistently has enough hosts/history that **per-host fine-tuning on top of the
  pooled model** (or per-host models once individually viable) would out-predict the pooled-only
  approach in a backtest -- the notebook's harness can be rerun on real data to check this
  periodically rather than assumed.
- Retention grows enough that 30-day-ahead (or longer) forecasts have real support in the
  training data and are worth re-adding.
- A capacity-planning use case needs a hard SLA-style bound ("will we breach 90% within N days")
  rather than a point/interval forecast -- that's a different question (first-passage-time over
  the existing quantile trajectories, or a dedicated survival-style model) than what this ADR
  covers.
