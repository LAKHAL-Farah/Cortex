"""Tests for routers/openstack_expert.py -- the "save this as a catalog
entry" feedback loop (v1.0, adr-0010). Real Postgres via SessionLocal for
seeding a resolved AnomalyEvent and cleaning up created rows, same pattern
tests/test_security_router.py and tests/test_anomalies_router.py use;
auth via real created accounts + real JWTs (test_security_router.py's
pattern), not the module-level dependency_overrides trick
test_topology_router.py uses, since these tests care about the
viewer-vs-admin distinction.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import crud, models
from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    """See test_security_router.py's identical fixture."""
    had_override = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had_override:
        app.dependency_overrides[get_current_user] = saved


def _make_user_headers(role: str = "viewer"):
    db = SessionLocal()
    try:
        user = crud.create_user(
            db,
            username=f"expert-router-test-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("irrelevant-for-this-test"),
            role=role,
        )
        token = create_access_token(user)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


def _seed_resolved_anomaly_event(hostname: str, metric_name: str, note: str = "restarted the service"):
    db = SessionLocal()
    try:
        event = models.AnomalyEvent(
            hostname=hostname, metric_name=metric_name, current_value=97.5, z_score=4.2,
            severity="critical", started_at=datetime.utcnow() - timedelta(minutes=10),
            resolved_at=datetime.utcnow(), resolution_type="manual", resolution_note=note,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event.id
    finally:
        db.close()


def _cleanup_entry(symptom_id: str):
    db = SessionLocal()
    try:
        row = crud.get_catalog_entry_by_symptom_id(db, symptom_id)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------- auth --

def test_draft_from_incident_requires_auth():
    resp = client.post(
        "/api/v1/openstack-expert/catalog-entries/draft-from-incident",
        json={"hostname": "compute-09", "metric_name": "cpu_usage"},
    )
    assert resp.status_code == 401


def test_publish_requires_admin_role():
    headers = _make_user_headers(role="viewer")
    resp = client.post(f"/api/v1/openstack-expert/catalog-entries/{uuid.uuid4()}/publish", headers=headers)
    # 403 (not admin) must win even though the id doesn't exist -- the
    # role check happens before the row is even looked up.
    assert resp.status_code == 403


def test_archive_requires_admin_role():
    headers = _make_user_headers(role="viewer")
    resp = client.post(f"/api/v1/openstack-expert/catalog-entries/{uuid.uuid4()}/archive", headers=headers)
    assert resp.status_code == 403


# ----------------------------------------------------- draft-from-incident --

def test_draft_from_incident_404s_with_no_resolved_incident():
    headers = _make_user_headers()
    resp = client.post(
        "/api/v1/openstack-expert/catalog-entries/draft-from-incident",
        json={"hostname": "no-such-host", "metric_name": "cpu_usage"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_draft_from_incident_prefills_from_resolution_note():
    hostname = f"pytest-host-{uuid.uuid4().hex[:6]}"
    _seed_resolved_anomaly_event(hostname, "cpu_usage", note="turned out to be a runaway backup job")
    headers = _make_user_headers()

    resp = client.post(
        "/api/v1/openstack-expert/catalog-entries/draft-from-incident",
        json={"hostname": hostname, "metric_name": "cpu_usage"},
        headers=headers,
    )

    assert resp.status_code == 201
    body = resp.json()
    try:
        assert body["status"] == "draft"
        assert body["what_it_means"] == "turned out to be a runaway backup job"
        assert body["source_hostname"] == hostname
        assert body["source_metric_name"] == "cpu_usage"
        assert body["category"] == "host"
        assert body["confirm_commands"] == []
    finally:
        _cleanup_entry(body["symptom_id"])


# ------------------------------------------------------------------- CRUD --

def test_create_blank_draft():
    headers = _make_user_headers()
    symptom_id = f"pytest-blank-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/openstack-expert/catalog-entries",
        json={"symptom_id": symptom_id, "title": "Blank draft", "category": "compute"},
        headers=headers,
    )
    try:
        assert resp.status_code == 201
        assert resp.json()["status"] == "draft"
    finally:
        _cleanup_entry(symptom_id)


def test_create_rejects_duplicate_symptom_id():
    headers = _make_user_headers()
    symptom_id = f"pytest-dup-{uuid.uuid4().hex[:8]}"
    payload = {"symptom_id": symptom_id, "title": "First", "category": "compute"}
    try:
        first = client.post("/api/v1/openstack-expert/catalog-entries", json=payload, headers=headers)
        assert first.status_code == 201
        second = client.post("/api/v1/openstack-expert/catalog-entries", json=payload, headers=headers)
        assert second.status_code == 409
    finally:
        _cleanup_entry(symptom_id)


def test_list_and_get_and_update_draft():
    headers = _make_user_headers()
    symptom_id = f"pytest-crud-{uuid.uuid4().hex[:8]}"
    try:
        created = client.post(
            "/api/v1/openstack-expert/catalog-entries",
            json={"symptom_id": symptom_id, "title": "CRUD test", "category": "compute"},
            headers=headers,
        ).json()
        entry_id = created["id"]

        listed = client.get("/api/v1/openstack-expert/catalog-entries?status=draft", headers=headers)
        assert listed.status_code == 200
        assert symptom_id in [e["symptom_id"] for e in listed.json()]

        got = client.get(f"/api/v1/openstack-expert/catalog-entries/{entry_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["symptom_id"] == symptom_id

        updated = client.put(
            f"/api/v1/openstack-expert/catalog-entries/{entry_id}",
            json={
                "what_it_means": "Updated explanation.",
                "confirm_commands": [{"command": "echo hi", "description": "check", "read_only": True}],
            },
            headers=headers,
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["what_it_means"] == "Updated explanation."
        assert body["confirm_commands"] == [{"command": "echo hi", "description": "check", "read_only": True}]
        # title wasn't in the PUT body -- must be unchanged (partial update).
        assert body["title"] == "CRUD test"
    finally:
        _cleanup_entry(symptom_id)


def test_get_missing_entry_404s():
    resp = client.get(f"/api/v1/openstack-expert/catalog-entries/{uuid.uuid4()}", headers=_make_user_headers())
    assert resp.status_code == 404


def test_update_a_published_entry_is_rejected():
    headers = _make_user_headers(role="admin")
    symptom_id = f"pytest-immutable-{uuid.uuid4().hex[:8]}"
    try:
        created = client.post(
            "/api/v1/openstack-expert/catalog-entries",
            json={"symptom_id": symptom_id, "title": "T", "category": "compute"},
            headers=headers,
        ).json()
        entry_id = created["id"]
        client.put(
            f"/api/v1/openstack-expert/catalog-entries/{entry_id}",
            json={
                "what_it_means": "x",
                "confirm_commands": [{"command": "a", "description": "d", "read_only": True}],
                "remediation_commands": [{"command": "b", "description": "d", "read_only": False}],
            },
            headers=headers,
        )
        publish_resp = client.post(f"/api/v1/openstack-expert/catalog-entries/{entry_id}/publish", headers=headers)
        assert publish_resp.status_code == 200

        resp = client.put(
            f"/api/v1/openstack-expert/catalog-entries/{entry_id}",
            json={"title": "should not be allowed"},
            headers=headers,
        )
        assert resp.status_code == 409
    finally:
        _cleanup_entry(symptom_id)


# --------------------------------------------------------------- publish --

def test_publish_an_incomplete_draft_returns_422_with_all_errors():
    headers = _make_user_headers(role="admin")
    symptom_id = f"pytest-incomplete-{uuid.uuid4().hex[:8]}"
    try:
        created = client.post(
            "/api/v1/openstack-expert/catalog-entries",
            json={"symptom_id": symptom_id, "title": "Incomplete", "category": "compute"},
            headers=headers,
        ).json()
        resp = client.post(f"/api/v1/openstack-expert/catalog-entries/{created['id']}/publish", headers=headers)
        assert resp.status_code == 422
        errors = resp.json()["detail"]["errors"]
        assert any("confirm_command" in e for e in errors)
        assert any("remediation_command" in e for e in errors)
        assert any("what_it_means" in e for e in errors)
    finally:
        _cleanup_entry(symptom_id)


def test_publish_a_complete_draft_succeeds_and_sets_published_at():
    headers = _make_user_headers(role="admin")
    symptom_id = f"pytest-complete-{uuid.uuid4().hex[:8]}"
    try:
        created = client.post(
            "/api/v1/openstack-expert/catalog-entries",
            json={"symptom_id": symptom_id, "title": "Complete", "category": "compute"},
            headers=headers,
        ).json()
        entry_id = created["id"]
        client.put(
            f"/api/v1/openstack-expert/catalog-entries/{entry_id}",
            json={
                "what_it_means": "Something specific happened.",
                "confirm_commands": [{"command": "openstack x show", "description": "check", "read_only": True}],
                "remediation_commands": [{"command": "systemctl restart x", "description": "fix", "read_only": False}],
            },
            headers=headers,
        )

        resp = client.post(f"/api/v1/openstack-expert/catalog-entries/{entry_id}/publish", headers=headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "published"
        assert body["published_at"] is not None
    finally:
        _cleanup_entry(symptom_id)


def test_publish_an_already_published_entry_conflicts():
    headers = _make_user_headers(role="admin")
    symptom_id = f"pytest-republish-{uuid.uuid4().hex[:8]}"
    try:
        created = client.post(
            "/api/v1/openstack-expert/catalog-entries",
            json={"symptom_id": symptom_id, "title": "T", "category": "compute"},
            headers=headers,
        ).json()
        entry_id = created["id"]
        client.put(
            f"/api/v1/openstack-expert/catalog-entries/{entry_id}",
            json={
                "what_it_means": "x",
                "confirm_commands": [{"command": "a", "description": "d", "read_only": True}],
                "remediation_commands": [{"command": "b", "description": "d", "read_only": False}],
            },
            headers=headers,
        )
        client.post(f"/api/v1/openstack-expert/catalog-entries/{entry_id}/publish", headers=headers)
        resp = client.post(f"/api/v1/openstack-expert/catalog-entries/{entry_id}/publish", headers=headers)
        assert resp.status_code == 409
    finally:
        _cleanup_entry(symptom_id)


def test_archive_a_published_entry():
    headers = _make_user_headers(role="admin")
    symptom_id = f"pytest-archive-{uuid.uuid4().hex[:8]}"
    try:
        created = client.post(
            "/api/v1/openstack-expert/catalog-entries",
            json={"symptom_id": symptom_id, "title": "T", "category": "compute"},
            headers=headers,
        ).json()
        entry_id = created["id"]
        client.put(
            f"/api/v1/openstack-expert/catalog-entries/{entry_id}",
            json={
                "what_it_means": "x",
                "confirm_commands": [{"command": "a", "description": "d", "read_only": True}],
                "remediation_commands": [{"command": "b", "description": "d", "read_only": False}],
            },
            headers=headers,
        )
        client.post(f"/api/v1/openstack-expert/catalog-entries/{entry_id}/publish", headers=headers)

        resp = client.post(f"/api/v1/openstack-expert/catalog-entries/{entry_id}/archive", headers=headers)

        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"
    finally:
        _cleanup_entry(symptom_id)
