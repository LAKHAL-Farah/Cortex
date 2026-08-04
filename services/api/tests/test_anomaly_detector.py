"""
services/api/tests/test_anomaly_detector.py

Unit tests for score_current_value() / detect_anomalies() using an in-memory
SQLite DB and a mocked Prometheus response -- no live Prometheus or Postgres
needed. Run with: pytest services/api/tests/test_anomaly_detector.py -v

These are the tests referenced in the notebook's conclusion: they pin down
severity behavior at known z-scores so a future threshold retune (which the
1.6 doc already expects to happen once real history accumulates) can't
silently change behavior without a test failing first.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.services.anomaly_detector import (
    score_current_value,
    severity_from_zscore,
    detect_anomalies,
    MIN_BASELINE_SAMPLES,
    MIN_BASELINE_DAYS,
    MIN_MAD_FLOOR,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def make_baseline(db, hostname="host1", metric_name="cpu_usage", weekday=2, hour=13,
                   median=25.0, mad=1.5, sample_count=36, distinct_days=None):
    # Default distinct_days comfortably clears MIN_BASELINE_DAYS so existing
    # "well populated baseline" tests don't have to know about day-coverage
    # gating unless that's specifically what they're testing.
    if distinct_days is None:
        distinct_days = max(MIN_BASELINE_DAYS, 7)
    b = models.Baseline(
        hostname=hostname, metric_name=metric_name, weekday=weekday, hour=hour,
        mean=median, stddev=mad, median=median, mad=mad, sample_count=sample_count,
        distinct_days=distinct_days,
    )
    db.add(b)
    db.commit()
    return b


# --- severity thresholds ---

def test_baseline_model_exists():
    """Guards against the class silently disappearing from models.py again
    (this exact bug happened once already: models.Baseline was referenced by
    anomaly_detector.py but missing from models.py, causing an AttributeError
    at runtime that no test caught)."""
    assert hasattr(models, "Baseline")
    assert models.Baseline.__tablename__ == "baselines"


@pytest.mark.parametrize("z, expected", [
    (0.5, "normal"),
    (1.99, "normal"),
    (2.0, "medium"),
    (2.9, "medium"),
    (3.0, "high"),
    (3.9, "high"),
    (4.0, "critical"),
    (10.0, "critical"),
    (-4.5, "critical"),  # negative z (value far BELOW baseline) is still an anomaly
])
def test_severity_from_zscore(z, expected):
    assert severity_from_zscore(z) == expected


# --- robust z-score path (sufficient, clean baseline) ---

def test_uses_robust_zscore_when_baseline_is_well_populated(db):
    make_baseline(db, median=25.0, mad=1.5, sample_count=36)
    # value 4 MAD-scaled-std above median -> should be "critical"
    current_value = 25.0 + 4.1 * (1.4826 * 1.5)
    z, severity, method, baseline_n = score_current_value(
        db, "host1", "cpu_usage", current_value, weekday=2, hour=13
    )
    assert method == "robust_zscore"
    assert baseline_n == 36
    assert severity == "critical"


def test_normal_value_is_not_flagged(db):
    make_baseline(db, median=25.0, mad=1.5, sample_count=36)
    z, severity, method, _ = score_current_value(db, "host1", "cpu_usage", 25.5, weekday=2, hour=13)
    assert severity == "normal"


# --- baseline contamination resistance (the notebook's key finding) ---

def test_robust_zscore_resists_a_single_bad_historical_day(db):
    # A naive mean/std baseline corrupted by one bad day would have a much larger
    # stddev; median/MAD barely move. Simulate the "clean" robust stats directly
    # (as computed by the 1.8 baseline job with MAD) and confirm a real anomaly is
    # still clearly flagged, unlike what happened with naive z-score in the notebook.
    make_baseline(db, median=25.6, mad=1.42, sample_count=36)  # matches notebook's clean slot
    current_value = 25.6 + 18  # the "moderate_sustained" injection from the notebook
    z, severity, method, _ = score_current_value(db, "host1", "cpu_usage", current_value, weekday=2, hour=13)
    assert severity in ("high", "critical")


# --- fallback path: missing or thin baseline ---

def test_falls_back_to_ewma_when_no_baseline_exists(db):
    # No Baseline row at all for this slot.
    z, severity, method, baseline_n = score_current_value(
        db, "new-host", "cpu_usage", 50.0, weekday=2, hour=13
    )
    assert method == "ewma_fallback"
    assert baseline_n is None
    # first-ever observation seeds the EWMA mean with itself -> z == 0, "normal"
    assert severity == "normal"


def test_falls_back_to_ewma_when_baseline_too_thin(db):
    make_baseline(db, sample_count=MIN_BASELINE_SAMPLES - 1)  # below the trust threshold
    z, severity, method, baseline_n = score_current_value(
        db, "host1", "cpu_usage", 25.0, weekday=2, hour=13
    )
    assert method == "ewma_fallback"


def test_falls_back_to_ewma_when_baseline_spans_too_few_days(db):
    """Enough raw points (sample_count clears MIN_BASELINE_SAMPLES) but they
    all came from fewer than MIN_BASELINE_DAYS distinct calendar days -- e.g.
    one hour's worth of 5-minute-step points right after a fresh deploy.
    That's not a real day-to-day spread yet, so this must still fall back to
    EWMA instead of trusting a median/MAD built from one autocorrelated hour."""
    make_baseline(db, sample_count=MIN_BASELINE_SAMPLES + 8, distinct_days=1)
    z, severity, method, baseline_n = score_current_value(
        db, "host1", "cpu_usage", 25.0, weekday=2, hour=13
    )
    assert method == "ewma_fallback"


def test_mad_floor_prevents_absurd_zscore_on_near_zero_mad(db):
    """A well-populated, multi-day baseline slot can still legitimately have
    a tiny MAD (a genuinely very stable hour). Without a floor, a modest real
    swing divided by a near-zero MAD produces an absurd, meaningless z-score
    (hundreds of "sigma") instead of a sane severity."""
    make_baseline(db, median=74.5, mad=0.005, sample_count=40, distinct_days=10)
    current_value = 80.0  # a real but modest jump, not an 800-sigma event
    z, severity, method, _ = score_current_value(
        db, "host1", "cpu_usage", current_value, weekday=2, hour=13
    )
    assert method == "robust_zscore"
    assert abs(z) < 20  # sane bound; without the floor this would be in the thousands


def test_ewma_state_persists_and_adapts_across_calls(db):
    # First call seeds the EWMA at the observed value (z=0). Second call at the
    # SAME value should also be ~normal once the state has a nonzero variance
    # from intervening ticks -- here we just check state actually persists.
    score_current_value(db, "host1", "cpu_usage", 20.0, weekday=2, hour=13)
    state = db.query(models.EwmaState).filter_by(hostname="host1", metric_name="cpu_usage").first()
    assert state is not None
    assert state.mean == pytest.approx(20.0)

    # A big jump afterward should register as anomalous relative to the learned mean.
    z, severity, method, _ = score_current_value(db, "host1", "cpu_usage", 90.0, weekday=2, hour=13)
    assert severity != "normal"


# --- end-to-end detect_anomalies() with a mocked Prometheus response ---

def test_detect_anomalies_writes_anomaly_flag(db, monkeypatch):
    # No matching baseline slot for "now" -> exercises the EWMA fallback path
    # end-to-end, which is exactly the sandbox-day-one scenario from the notebook.
    import app.services.anomaly_detector as mod

    def fake_fetch_instant(query):
        return [{"metric": {"instance": "compute1-sim:9100"}, "value": [0, "95.0"]}]

    monkeypatch.setattr(mod, "fetch_instant", fake_fetch_instant)
    monkeypatch.setattr(mod, "METRICS", {"cpu_usage": "dummy"})

    mod.detect_anomalies(db)

    flag = db.query(models.AnomalyFlag).filter_by(hostname="compute1-sim").first()
    assert flag is not None
    assert flag.current_value == 95.0


# --- hostname resolution: real hostname, not the scrape target's bare IP ---

def test_resolve_hostname_prefers_node_label(db):
    """The 'node' label (real Node.hostname, attached by prometheus_sd.py)
    should win over parsing an IP out of 'instance' -- previously the IP was
    the *only* thing used, so alerts always displayed an IP instead of a
    hostname."""
    from app.services.anomaly_detector import resolve_hostname

    hostname = resolve_hostname(db, {"instance": "10.0.1.21:9100", "node": "compute1-sim"})
    assert hostname == "compute1-sim"


def test_resolve_hostname_falls_back_to_nodes_table_by_ip(db):
    """When 'node' is missing (e.g. an older/aggregated series), fall back to
    looking the IP up in the nodes table before giving up and using the bare
    IP -- this is the 'hostname associated with it from the nodes table'."""
    from app.services.anomaly_detector import resolve_hostname

    db.add(models.Node(hostname="compute1-sim", ip_address="10.0.1.21", role="compute"))
    db.commit()

    hostname = resolve_hostname(db, {"instance": "10.0.1.21:9100"})
    assert hostname == "compute1-sim"


def test_resolve_hostname_falls_back_to_ip_when_unregistered(db):
    from app.services.anomaly_detector import resolve_hostname

    hostname = resolve_hostname(db, {"instance": "10.0.1.99:9100"})
    assert hostname == "10.0.1.99"


# --- anomaly history (AnomalyEvent) ---

def test_detect_anomalies_opens_and_resolves_history_event(db, monkeypatch):
    """A host/metric crossing into an anomalous severity should open an
    AnomalyEvent, and dropping back to normal should resolve it -- so past
    anomalies stay visible on the History page instead of just disappearing
    the way the AnomalyFlag-only upsert did."""
    import app.services.anomaly_detector as mod

    now = datetime.utcnow()
    make_baseline(db, hostname="host1", metric_name="cpu_usage", weekday=now.weekday(), hour=now.hour,
                  median=25.0, mad=1.5, sample_count=36)

    values = iter([25.0 + 5 * (1.4826 * 1.5), 25.5])  # first: critical spike, then: back to normal

    def fake_fetch_instant(query):
        return [{"metric": {"instance": "host1:9100", "node": "host1"}, "value": [0, str(next(values))]}]

    monkeypatch.setattr(mod, "fetch_instant", fake_fetch_instant)
    monkeypatch.setattr(mod, "METRICS", {"cpu_usage": "dummy"})

    mod.detect_anomalies(db)
    event = db.query(models.AnomalyEvent).filter_by(hostname="host1", metric_name="cpu_usage").first()
    assert event is not None
    assert event.resolved_at is None
    assert event.severity == "critical"

    mod.detect_anomalies(db)
    db.refresh(event)
    assert event.resolved_at is not None

    # Still queryable afterward -- this is the point of the history table.
    all_events = db.query(models.AnomalyEvent).filter_by(hostname="host1").all()
    assert len(all_events) == 1
