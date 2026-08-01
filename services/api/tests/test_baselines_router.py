import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app import models

client = TestClient(app)


def _seed_baseline_row(hostname, metric_name="cpu_usage", weekday=0, hour=9, **overrides):
    db = SessionLocal()
    try:
        row = models.Baseline(
            hostname=hostname,
            metric_name=metric_name,
            weekday=weekday,
            hour=hour,
            mean=overrides.get("mean", 70.0),
            stddev=overrides.get("stddev", 5.0),
            median=overrides.get("median", 69.5),
            mad=overrides.get("mad", 4.0),
            sample_count=overrides.get("sample_count", 12),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()


def test_get_baseline_returns_slots_ordered_by_weekday_and_hour():
    hostname = f"baseline-test-{uuid.uuid4().hex[:6]}"
    _seed_baseline_row(hostname, weekday=6, hour=3, median=8.0, mad=1.5, sample_count=10)   # Sun 3am
    _seed_baseline_row(hostname, weekday=0, hour=9, median=70.0, mad=4.0, sample_count=12)  # Mon 9am

    res = client.get(f"/api/v1/baselines/{hostname}", params={"metric_name": "cpu_usage"})
    assert res.status_code == 200

    body = res.json()
    assert len(body) == 2
    # Monday (weekday=0) should sort before Sunday (weekday=6)
    assert (body[0]["weekday"], body[0]["hour"]) == (0, 9)
    assert (body[1]["weekday"], body[1]["hour"]) == (6, 3)


def test_get_baseline_reflects_weekday_hour_contrast():
    """Mirrors the 1.8 acceptance criterion directly against the API response:
    Monday 9am's median should be visibly higher than Sunday 3am's for the same node.
    """
    hostname = f"baseline-test-{uuid.uuid4().hex[:6]}"
    _seed_baseline_row(hostname, weekday=0, hour=9, median=70.0, mad=4.0)
    _seed_baseline_row(hostname, weekday=6, hour=3, median=8.0, mad=1.5)

    body = client.get(f"/api/v1/baselines/{hostname}", params={"metric_name": "cpu_usage"}).json()
    by_slot = {(r["weekday"], r["hour"]): r["median"] for r in body}

    assert by_slot[(0, 9)] - by_slot[(6, 3)] > 20


def test_get_baseline_unknown_host_returns_empty_list():
    res = client.get("/api/v1/baselines/no-such-host", params={"metric_name": "cpu_usage"})
    assert res.status_code == 200
    assert res.json() == []


def test_get_baseline_requires_metric_name_query_param():
    res = client.get("/api/v1/baselines/some-host")
    assert res.status_code == 422
