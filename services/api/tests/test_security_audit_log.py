"""
services/api/tests/test_security_audit_log.py

Phase Sec-6: GET /api/v1/security/audit-log -- "who asked a security
question, what role they had, and whether the answer was redacted",
using the same security_rbac logic already gating chat/dashboard answers.

Seeds rows directly via crud.create_agent_trace rather than driving the
full POST /orchestrate graph -- this endpoint's own logic (the
security_involved filter, the redacted computation, the admin gate) is
what's under test here, not the orchestrator or the RBAC helpers
themselves (see test_security_rbac.py / test_security_router.py for
those).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app.main import app
from app import crud

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    """Same guard test_security_router.py uses -- see that module's own
    docstring on why this is needed (test_topology_router.py leaks a
    dependency_override with no teardown)."""
    had_override = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had_override:
        app.dependency_overrides[get_current_user] = saved


def _make_user(role: str = "viewer"):
    db = SessionLocal()
    try:
        user = crud.create_user(
            db,
            username=f"audit-log-test-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("irrelevant-for-this-test"),
            role=role,
        )
        token = create_access_token(user)
        return user, {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def _seed_trace(*, user_id, user_role, security_involved, user_query="is X exposed?"):
    db = SessionLocal()
    try:
        return crud.create_agent_trace(
            db,
            trace_id=uuid.uuid4(),
            user_query=user_query,
            intent="security",
            target_agent="security" if security_involved else "monitoring",
            critic_verdict_status=None,
            degraded=False,
            steps=[],
            final_answer="some finding" if security_involved else "cpu is fine",
            duration_ms=42.0,
            user_id=user_id,
            user_role=user_role,
            security_involved=security_involved,
        )
    finally:
        db.close()


def test_audit_log_requires_authentication():
    resp = client.get("/api/v1/security/audit-log")
    assert resp.status_code == 401


def test_audit_log_requires_admin():
    _, viewer_headers = _make_user("viewer")
    resp = client.get("/api/v1/security/audit-log", headers=viewer_headers)
    assert resp.status_code == 403


def test_audit_log_lists_security_involved_traces_only():
    admin_user, admin_headers = _make_user("admin")
    asker, _ = _make_user("viewer")

    _seed_trace(user_id=asker.id, user_role="viewer", security_involved=True, user_query="is sandbox-vm-2 exposed?")
    _seed_trace(user_id=asker.id, user_role="viewer", security_involved=False, user_query="what's the cpu on compute1-sim?")

    resp = client.get("/api/v1/security/audit-log", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()

    queries = [e["user_query"] for e in body["entries"]]
    assert "is sandbox-vm-2 exposed?" in queries
    assert "what's the cpu on compute1-sim?" not in queries


def test_audit_log_marks_redacted_for_non_admin_asker_and_not_for_admin_asker():
    _, admin_headers = _make_user("admin")
    viewer_asker, _ = _make_user("viewer")
    admin_asker, _ = _make_user("admin")

    _seed_trace(
        user_id=viewer_asker.id, user_role="viewer", security_involved=True,
        user_query="viewer-asked-security-question",
    )
    _seed_trace(
        user_id=admin_asker.id, user_role="admin", security_involved=True,
        user_query="admin-asked-security-question",
    )

    resp = client.get("/api/v1/security/audit-log", headers=admin_headers)
    assert resp.status_code == 200
    entries = {e["user_query"]: e for e in resp.json()["entries"]}

    assert entries["viewer-asked-security-question"]["redacted"] is True
    assert entries["viewer-asked-security-question"]["user_role"] == "viewer"

    assert entries["admin-asked-security-question"]["redacted"] is False
    assert entries["admin-asked-security-question"]["user_role"] == "admin"


def test_audit_log_shows_username_and_survives_callerless_rows():
    """A stateless/API caller with no session (routers/agents.py's own
    conversation_id-optional docstring) still runs Security -- user_id is
    nullable, and the audit log should surface that row with no username
    rather than erroring."""
    _, admin_headers = _make_user("admin")
    asker, _ = _make_user("viewer")

    _seed_trace(user_id=asker.id, user_role="viewer", security_involved=True, user_query="named-caller-question")
    _seed_trace(user_id=None, user_role=None, security_involved=True, user_query="callerless-question")

    resp = client.get("/api/v1/security/audit-log", headers=admin_headers)
    assert resp.status_code == 200
    entries = {e["user_query"]: e for e in resp.json()["entries"]}

    assert entries["named-caller-question"]["username"] == asker.username
    assert entries["callerless-question"]["username"] is None
    assert entries["callerless-question"]["redacted"] is True
