# ADR-0001: Baseline model for pattern-mining (weekday × hour)

**Status:** Accepted (already implemented — this ADR documents an existing decision)
**Related code:** `services/api/app/services/baseline_builder.py`, `services/api/app/models.py`
(`Baseline`), `services/api/app/services/anomaly_detector.py` (`score_current_value`)
**Related tests:** `services/api/tests/test_baseline_builder.py`

## Context

Anomaly detection (`anomaly_detector.score_current_value()`) needs to know what's "normal" for
a given node at a given weekday/hour, so a CPU spike on a Monday morning isn't scored the same
way as the same value at 3am on a Sunday. That baseline is built by
`baseline_builder.compute_baselines()`, which pulls up to 21 days of history per metric from
Prometheus (`query_range`, 5-minute step) and groups points by `(hostname, weekday, hour)`.

This ADR exists because the code already made the decision below (see the docstring at the top
of `baseline_builder.py`), but `docs/architecture/` had nothing recording *why* — this backfills
that.

## Decision

**Each `(hostname, metric_name, weekday, hour)` slot is summarized with median and MAD (median
absolute deviation, scaled by 1.4826), not mean and standard deviation.** Both are actually
stored (`Baseline.mean`/`stddev` alongside `Baseline.median`/`mad`), but `score_current_value()`
only trusts the median/MAD pair for its `robust_zscore` path.

## Rationale

- **Short window, thin cycles.** A 21-day lookback only gives ~3 occurrences of each weekday.
  One contaminated day (a batch job, a deploy, a noisy neighbor) is enough to visibly skew a
  mean-based estimate; the median mostly ignores it.
- **Division-by-zero safety already had to be handled anyway.** `score_current_value()` refuses
  to trust a slot where `mad == 0` (a perfectly flat slot) — the same discipline a stddev-based
  estimator would need, so choosing the robust variant added no extra complexity.
- **Graceful cold start.** A slot with fewer than `MIN_BASELINE_SAMPLES` (10) points — including
  a freshly started sandbox with almost no Prometheus retention yet — correctly falls back to an
  EWMA estimate instead of a robust_zscore call. Storing both mean/stddev and median/mad doesn't
  cost anything extra and keeps the door open for comparison/debugging without committing to
  mean/stddev as the source of truth.

## Consequences

- `baselines.mean`/`baselines.stddev` are informational only; do not use them for anomaly
  scoring — `median`/`mad` are the values `score_current_value()` actually reads.
- `GET /api/v1/baselines/{hostname}` (new) returns all four fields so a client can compare them,
  but a dashboard rendering "the baseline curve" should plot `median`, not `mean`.
- `MIN_POINTS_TO_STORE = 2` in `baseline_builder.py` is deliberately looser than
  `anomaly_detector.MIN_BASELINE_SAMPLES = 10` — a thin, unproven slot is still worth storing and
  showing; it's `score_current_value()`'s job to decide whether to trust it, not
  `baseline_builder.py`'s.

## Revisit when

- The lookback window grows enough (multiple months) that trend-aware methods (e.g. STL
  decomposition, capacity-growth tracking) become worth the added complexity — median/MAD only
  captures the weekly rhythm, not a slow upward trend.
