"""
services/api/tests/test_baseline_builder.py

Unit tests for compute_baselines(), using an in-memory SQLite DB and a mocked
Prometheus query_range() response -- no live Prometheus or Postgres needed.
Run with: pytest services/api/tests/test_baseline_builder.py -v

These pin down the exact gap that caused every AnomalyFlag to come back with
"method": "ewma_fallback": the `baselines` table was never populated by
anything in production code. compute_baselines() is what now populates it.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.services.baseline_builder import compute_baselines
from app.services.anomaly_detector import score_current_value, MIN_BASELINE_SAMPLES


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _ts(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def test_compute_baselines_writes_median_and_mad(db, monkeypatch):
    import app.services.baseline_builder as mod

    # A fixed (weekday, hour) slot -- Wednesday (2) 13:00 -- with 12 samples
    # (one 5-minute-step hour) clustered around 25, plus one outlier point
    # that should barely move the median/MAD.
    base = datetime(2026, 7, 15, 13, 0)  # a Wednesday
    values = [24.0, 25.0, 26.0, 25.0, 24.5, 25.5, 25.0, 24.0, 26.0, 25.0, 24.5, 90.0]
    points = [
        [_ts(base.replace(minute=(i * 5) % 60)), str(v)]
        for i, v in enumerate(values)
    ]

    def fake_query_range(promql, start, end, step):
        return [{"metric": {"instance": "host1:9100"}, "values": points}]

    monkeypatch.setattr(mod, "METRICS", {"cpu_usage": "dummy"})
    monkeypatch.setattr(mod.prometheus_client, "query_range", fake_query_range)

    written = compute_baselines(db)
    assert written >= 1

    baseline = (
        db.query(models.Baseline)
        .filter_by(hostname="host1", metric_name="cpu_usage", weekday=2, hour=13)
        .first()
    )
    assert baseline is not None
    assert baseline.sample_count == 12
    # Median should sit near the tight cluster, essentially unmoved by the
    # single 90.0 outlier -- this is the whole point of using median/MAD.
    assert 24.0 <= baseline.median <= 26.0
    assert baseline.mad < 5.0


def test_compute_baselines_upserts_existing_slot(db, monkeypatch):
    import app.services.baseline_builder as mod

    base = datetime(2026, 7, 15, 13, 0)
    points = [[_ts(base.replace(minute=(i * 5) % 60)), "50.0"] for i in range(12)]

    def fake_query_range(promql, start, end, step):
        return [{"metric": {"instance": "host1:9100"}, "values": points}]

    monkeypatch.setattr(mod, "METRICS", {"cpu_usage": "dummy"})
    monkeypatch.setattr(mod.prometheus_client, "query_range", fake_query_range)

    compute_baselines(db)
    compute_baselines(db)  # second pass should update, not duplicate

    rows = (
        db.query(models.Baseline)
        .filter_by(hostname="host1", metric_name="cpu_usage", weekday=2, hour=13)
        .all()
    )
    assert len(rows) == 1


def test_thin_slot_below_min_points_is_not_stored(db, monkeypatch):
    import app.services.baseline_builder as mod

    base = datetime(2026, 7, 15, 13, 0)
    points = [[_ts(base), "50.0"]]  # a single point, below MIN_POINTS_TO_STORE

    def fake_query_range(promql, start, end, step):
        return [{"metric": {"instance": "host1:9100"}, "values": points}]

    monkeypatch.setattr(mod, "METRICS", {"cpu_usage": "dummy"})
    monkeypatch.setattr(mod.prometheus_client, "query_range", fake_query_range)

    compute_baselines(db)

    assert db.query(models.Baseline).count() == 0


def test_end_to_end_switches_from_fallback_to_robust_zscore(db, monkeypatch):
    """The behavior the user actually cares about: before compute_baselines()
    has run, score_current_value() must fall back to EWMA (correct, expected
    behavior for a slot with no data yet). After it runs with enough samples,
    the exact same call should switch to robust_zscore automatically -- no
    other code changes required, since anomaly_detector.py already handles
    this switch on its own once `baselines` is populated.
    """
    import app.services.baseline_builder as mod

    weekday, hour = 2, 13
    base = datetime(2026, 7, 15, hour, 0)  # a Wednesday
    assert base.weekday() == weekday

    # Before: no baseline row exists yet -> EWMA fallback.
    z, severity, method, baseline_n = score_current_value(
        db, "host1", "cpu_usage", 25.0, weekday=weekday, hour=hour
    )
    assert method == "ewma_fallback"

    # Populate the baseline with enough clean samples. A little jitter is
    # needed -- an all-identical baseline has mad == 0, which
    # score_current_value() correctly refuses to trust (division by zero),
    # exactly like a real (weekday, hour) slot would never be perfectly flat.
    values = [25.0, 24.5, 25.5, 25.0, 24.8, 25.2, 25.0, 24.6, 25.4, 25.0]
    assert len(values) == MIN_BASELINE_SAMPLES
    points = [[_ts(base.replace(minute=(i * 5) % 60)), str(v)] for i, v in enumerate(values)]

    def fake_query_range(promql, start, end, step):
        return [{"metric": {"instance": "host1:9100"}, "values": points}]

    monkeypatch.setattr(mod, "METRICS", {"cpu_usage": "dummy"})
    monkeypatch.setattr(mod.prometheus_client, "query_range", fake_query_range)
    compute_baselines(db)

    # After: same slot now has a sufficiently-populated baseline -> robust_zscore.
    z, severity, method, baseline_n = score_current_value(
        db, "host1", "cpu_usage", 25.0, weekday=weekday, hour=hour
    )
    assert method == "robust_zscore"
    assert baseline_n == MIN_BASELINE_SAMPLES
