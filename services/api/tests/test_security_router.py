"""
services/api/tests/test_security_router.py

Phase Sec-2: routers/security.py's GET endpoints should give a dashboard
the exact same Security Agent findings, with the exact same RBAC
redaction, that POST /api/v1/agents/orchestrate already gives the chat
path -- without paying an LLM call per host per poll (`_investigate(...,
narrate=False)`).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app.main import app
from app import crud, schemas
from app.agents.nodes import security as security_agent_node

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    """Defends against a known pre-existing issue elsewhere in this test
    suite: test_topology_router.py sets
    `app.dependency_overrides[get_current_user] = ...` at *module import
    time* with no teardown. Since pytest imports every test module during
    collection before running any test in any of them, that assignment
    can leak a fixed "viewer" identity into every other test module's
    requests for the rest of the session, regardless of what
    Authorization header they actually send -- which would make the
    admin-vs-viewer assertions below meaningless (and order-dependent)
    without this. Not this module's bug to fix; this only guards this
    module's own tests against it.
    """
    had_override = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had_override:
        app.dependency_overrides[get_current_user] = saved


def _make_user_headers(role: str = "viewer") -> dict:
    db = SessionLocal()
    try:
        user = crud.create_user(
            db,
            username=f"sec-router-test-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("irrelevant-for-this-test"),
            role=role,
        )
        token = create_access_token(user)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


def _make_node(hostname: str) -> None:
    db = SessionLocal()
    try:
        if crud.get_node_by_hostname(db, hostname) is None:
            # Must fall inside a managed subnet -- see schemas.NodeBase's
            # ip_must_be_in_private_subnet validator -- so this reuses the
            # same 10.0.1.0/24 range the sandbox's compute*-sim nodes live
            # in (infra/docker-compose.sandbox.yml), picking a unique last
            # octet per test run to avoid colliding on the unique ip_address
            # constraint across tests in the same session.
            octet = int(uuid.uuid4().hex[:2], 16) % 250 + 2
            crud.create_node(db, schemas.NodeCreate(hostname=hostname, ip_address=f"10.0.1.{octet}", role="compute"))
    finally:
        db.close()


def _clean_investigate(monkeypatch, has_signal=False, degraded=False):
    """Stubs _investigate so these tests don't touch Loki/Neutron/CVE-feed/
    eBPF at all -- this router's own logic (redaction, shape, 404s) is
    what's under test here, not the sub-checks themselves (see
    test_security_agent.py / test_security_group_snapshots.py for those).
    """
    def fake_investigate(query, node, narrate=True):
        raw_data = {
            "has_signal": has_signal,
            "auth_signal": {"has_signal": False, "degraded": False, "detail": "clean"},
            "sec_group_signal": {
                "has_signal": has_signal, "degraded": False, "detail": "1 risky rule",
                "risky_rules": [{"security_group": "default", "reason": "SSH open to the world"}] if has_signal else [],
                "drift": [{"security_group": "default", "added_rules": [{"id": "r2"}], "removed_rules": []}] if has_signal else [],
            },
            "cve_signal": {"has_signal": False, "degraded": False, "detail": "clean"},
            "ebpf_signal": {"has_signal": False, "degraded": False, "detail": "clean"},
        }
        agent_result = {
            "agent": "security",
            "summary": "1 finding." if has_signal else "All clear.",
            "confidence": "config_signal" if has_signal else "clean_reading",
            "raw_data": raw_data,
        }
        failures = [{"source": "security.neutron"}] if degraded else []
        return agent_result, failures

    monkeypatch.setattr(security_agent_node, "_investigate", fake_investigate)


# --------------------------------------------------------------------------
# GET /api/v1/security/status
# --------------------------------------------------------------------------

def test_status_requires_authentication():
    res = client.get("/api/v1/security/status")
    assert res.status_code == 401


def test_status_ok_when_no_node_has_a_signal(monkeypatch):
    _make_node("sec-router-status-ok")
    _clean_investigate(monkeypatch, has_signal=False)

    res = client.get("/api/v1/security/status", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["has_signal"] is False


def test_status_degraded_when_any_node_has_a_signal(monkeypatch):
    _make_node("sec-router-status-degraded")
    _clean_investigate(monkeypatch, has_signal=True)

    res = client.get("/api/v1/security/status", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    assert res.json()["status"] == "degraded"


# --------------------------------------------------------------------------
# GET /api/v1/security/findings, /findings/{hostname}
# --------------------------------------------------------------------------

def test_findings_admin_sees_full_raw_data(monkeypatch):
    _make_node("sec-router-admin-view")
    _clean_investigate(monkeypatch, has_signal=True)

    res = client.get("/api/v1/security/findings", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    findings = res.json()["findings"]
    match = next(f for f in findings if f["hostname"] == "sec-router-admin-view")
    assert match["has_signal"] is True
    assert match["answer"] == "1 finding."
    assert match["raw_data"]["sec_group_signal"]["drift"][0]["added_rules"] == [{"id": "r2"}]


def test_findings_viewer_gets_redacted_notice_and_booleans_only(monkeypatch):
    _make_node("sec-router-viewer-view")
    _clean_investigate(monkeypatch, has_signal=True)

    res = client.get("/api/v1/security/findings", headers=_make_user_headers("viewer"))

    assert res.status_code == 200
    findings = res.json()["findings"]
    match = next(f for f in findings if f["hostname"] == "sec-router-viewer-view")
    # Redacted per services/security_rbac.py: generic notice instead of
    # the real answer, sub-signal collapsed to booleans, no rule/drift
    # specifics leaked.
    assert "restricted to admin accounts" in match["answer"]
    assert match["raw_data"]["sec_group_signal"] == {"has_signal": True, "degraded": False, "restricted": True}
    assert "drift" not in match["raw_data"]["sec_group_signal"]
    assert "risky_rules" not in match["raw_data"]["sec_group_signal"]
    # has_signal itself is not secret -- a viewer should still see that
    # *something* was flagged on this host.
    assert match["has_signal"] is True


def test_get_finding_404s_for_unknown_hostname(monkeypatch):
    _clean_investigate(monkeypatch)
    res = client.get("/api/v1/security/findings/no-such-host-at-all", headers=_make_user_headers("admin"))
    assert res.status_code == 404


def test_get_finding_matches_one_entry_of_the_list(monkeypatch):
    _make_node("sec-router-single-lookup")
    _clean_investigate(monkeypatch, has_signal=False)

    res = client.get("/api/v1/security/findings/sec-router-single-lookup", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    assert res.json()["hostname"] == "sec-router-single-lookup"
    assert res.json()["has_signal"] is False


# --------------------------------------------------------------------------
# GET /api/v1/security/groups/{hostname}
# --------------------------------------------------------------------------

def test_get_security_groups_404s_for_unknown_hostname():
    res = client.get("/api/v1/security/groups/no-such-host-at-all", headers=_make_user_headers("admin"))
    assert res.status_code == 404


def test_get_security_groups_admin_sees_drift_and_risky_rules(monkeypatch):
    _make_node("sec-router-groups-admin")
    monkeypatch.setattr(security_agent_node, "_check_sec_group_diff", lambda node: {
        "has_signal": True, "degraded": False, "detail": "1 risky rule; drift detected",
        "data": {"security_groups": []}, "risky_rules": [{"reason": "SSH open to the world"}],
        "drift": [{"security_group": "default", "added_rules": [{"id": "r2"}], "removed_rules": []}],
    })

    res = client.get("/api/v1/security/groups/sec-router-groups-admin", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    body = res.json()
    assert body["drift"][0]["added_rules"] == [{"id": "r2"}]
    assert body["risky_rules"] == [{"reason": "SSH open to the world"}]


def test_get_security_groups_viewer_gets_booleans_only(monkeypatch):
    _make_node("sec-router-groups-viewer")
    monkeypatch.setattr(security_agent_node, "_check_sec_group_diff", lambda node: {
        "has_signal": True, "degraded": False, "detail": "1 risky rule; drift detected",
        "data": {"security_groups": []}, "risky_rules": [{"reason": "SSH open to the world"}],
        "drift": [{"security_group": "default", "added_rules": [{"id": "r2"}], "removed_rules": []}],
    })

    res = client.get("/api/v1/security/groups/sec-router-groups-viewer", headers=_make_user_headers("viewer"))

    assert res.status_code == 200
    assert res.json() == {"has_signal": True, "degraded": False, "restricted": True}


# --------------------------------------------------------------------------
# GET /api/v1/security/exposed-ports/{hostname} -- Phase Sec-5a, Node scope
# --------------------------------------------------------------------------

def test_get_exposed_ports_404s_for_unknown_hostname():
    res = client.get("/api/v1/security/exposed-ports/no-such-host-at-all", headers=_make_user_headers("admin"))
    assert res.status_code == 404


def test_get_exposed_ports_admin_sees_confirmed_mismatches(monkeypatch):
    _make_node("sec-router-exposed-admin")
    monkeypatch.setattr(security_agent_node, "_check_exposed_ports", lambda node, sec_group: {
        "has_signal": True, "degraded": False, "detail": "1 confirmed exposed port",
        "mismatches": [{"port": 22, "protocol": "tcp", "process": "sshd", "security_group": "default", "reason": "SSH open to the world"}],
        "listening_ports": [{"port": 22, "protocol": "tcp", "process": "sshd"}],
    })

    res = client.get("/api/v1/security/exposed-ports/sec-router-exposed-admin", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    body = res.json()
    assert body["has_signal"] is True
    assert body["mismatches"][0]["port"] == 22


def test_get_exposed_ports_viewer_gets_booleans_only(monkeypatch):
    _make_node("sec-router-exposed-viewer")
    monkeypatch.setattr(security_agent_node, "_check_exposed_ports", lambda node, sec_group: {
        "has_signal": True, "degraded": False, "detail": "1 confirmed exposed port",
        "mismatches": [{"port": 22, "protocol": "tcp", "process": "sshd", "security_group": "default", "reason": "SSH open to the world"}],
        "listening_ports": [{"port": 22, "protocol": "tcp", "process": "sshd"}],
    })

    res = client.get("/api/v1/security/exposed-ports/sec-router-exposed-viewer", headers=_make_user_headers("viewer"))

    assert res.status_code == 200
    body = res.json()
    assert body["has_signal"] is True
    assert body["degraded"] is False
    assert body["restricted"] is True
    assert "mismatches" not in body
    assert "listening_ports" not in body


# --------------------------------------------------------------------------
# GET /api/v1/security/instance-exposed-ports/{instance_id} -- Phase Sec-5b,
# Instance scope
# --------------------------------------------------------------------------

def test_instance_exposed_ports_requires_authentication():
    res = client.get("/api/v1/security/instance-exposed-ports/sandbox-vm-1")
    assert res.status_code == 401


def test_instance_exposed_ports_admin_sees_confirmed_reachability(monkeypatch):
    from app.services import instance_exposure

    monkeypatch.setattr(instance_exposure, "get_instance_exposure_targets", lambda instance_id, conn=None: {
        "instance_id": instance_id, "instance_name": "sandbox-vm-1", "reachable_ip": "203.0.113.10",
        "reachable_via": "floating_ip", "declared_open": [{"port": 22, "reason": "SSH open to the world", "rule": {}}],
    })
    monkeypatch.setattr(instance_exposure, "probe_declared_open_ports", lambda ip, declared: {
        "ip_address": ip, "confirmed": [{"port": 22, "reason": "SSH open to the world"}],
        "unconfirmed": [], "unscoped_rules": [],
    })

    res = client.get("/api/v1/security/instance-exposed-ports/sandbox-vm-1", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    body = res.json()
    assert body["has_signal"] is True
    assert body["reachable_ip"] == "203.0.113.10"
    assert body["confirmed"] == [{"port": 22, "reason": "SSH open to the world"}]


def test_instance_exposed_ports_viewer_gets_booleans_only(monkeypatch):
    from app.services import instance_exposure

    monkeypatch.setattr(instance_exposure, "get_instance_exposure_targets", lambda instance_id, conn=None: {
        "instance_id": instance_id, "instance_name": "sandbox-vm-1", "reachable_ip": "203.0.113.10",
        "reachable_via": "floating_ip", "declared_open": [{"port": 22, "reason": "SSH open to the world", "rule": {}}],
    })
    monkeypatch.setattr(instance_exposure, "probe_declared_open_ports", lambda ip, declared: {
        "ip_address": ip, "confirmed": [{"port": 22, "reason": "SSH open to the world"}],
        "unconfirmed": [], "unscoped_rules": [],
    })

    res = client.get("/api/v1/security/instance-exposed-ports/sandbox-vm-1", headers=_make_user_headers("viewer"))

    assert res.status_code == 200
    assert res.json() == {"has_signal": True, "degraded": False, "restricted": True}


def test_instance_exposed_ports_no_reachable_ip_is_not_a_signal(monkeypatch):
    from app.services import instance_exposure

    monkeypatch.setattr(instance_exposure, "get_instance_exposure_targets", lambda instance_id, conn=None: {
        "instance_id": instance_id, "instance_name": None, "reachable_ip": None,
        "reachable_via": None, "declared_open": [],
    })

    res = client.get("/api/v1/security/instance-exposed-ports/unknown-instance", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    body = res.json()
    assert body["has_signal"] is False
    assert "no reachable IP" in body["detail"].lower() or "no floating or fixed ip" in body["detail"].lower()


def test_instance_exposed_ports_degrades_on_connection_error(monkeypatch):
    from app.services import instance_exposure

    def _dead(instance_id, conn=None):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(instance_exposure, "get_instance_exposure_targets", _dead)

    res = client.get("/api/v1/security/instance-exposed-ports/sandbox-vm-1", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    body = res.json()
    assert body["degraded"] is True
    assert body["has_signal"] is False


# --------------------------------------------------------------------------
# GET /api/v1/security/keystone-tokens -- Phase Sec-5c, Identity scope
# --------------------------------------------------------------------------

def test_keystone_tokens_requires_authentication():
    res = client.get("/api/v1/security/keystone-tokens")
    assert res.status_code == 401


def test_keystone_tokens_admin_sees_pattern_detail(monkeypatch):
    from app.services import keystone_audit

    monkeypatch.setattr(keystone_audit, "get_token_issuance_log", lambda: [{"username": "mallory"}] * 3)
    monkeypatch.setattr(keystone_audit, "find_abusive_token_patterns", lambda events, now=None: {
        "rapid_reissue": [{"username": "mallory", "count": 3, "window_seconds": 10, "first_issued_at": "x"}],
        "unexpected_ip": [], "unexpected_ip_checked": False, "long_lived": [], "event_count": 3,
    })

    res = client.get("/api/v1/security/keystone-tokens", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    body = res.json()
    assert body["has_signal"] is True
    assert body["rapid_reissue"][0]["username"] == "mallory"


def test_keystone_tokens_viewer_gets_booleans_only(monkeypatch):
    from app.services import keystone_audit

    monkeypatch.setattr(keystone_audit, "get_token_issuance_log", lambda: [{"username": "mallory"}] * 3)
    monkeypatch.setattr(keystone_audit, "find_abusive_token_patterns", lambda events, now=None: {
        "rapid_reissue": [{"username": "mallory", "count": 3, "window_seconds": 10, "first_issued_at": "x"}],
        "unexpected_ip": [], "unexpected_ip_checked": False, "long_lived": [], "event_count": 3,
    })

    res = client.get("/api/v1/security/keystone-tokens", headers=_make_user_headers("viewer"))

    assert res.status_code == 200
    assert res.json() == {"has_signal": True, "degraded": False, "restricted": True}


def test_keystone_tokens_clean_log_has_no_signal(monkeypatch):
    from app.services import keystone_audit

    monkeypatch.setattr(keystone_audit, "get_token_issuance_log", lambda: [])
    monkeypatch.setattr(keystone_audit, "find_abusive_token_patterns", lambda events, now=None: {
        "rapid_reissue": [], "unexpected_ip": [], "unexpected_ip_checked": False, "long_lived": [], "event_count": 0,
    })

    res = client.get("/api/v1/security/keystone-tokens", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    assert res.json()["has_signal"] is False


def test_keystone_tokens_degrades_on_connection_error(monkeypatch):
    from app.services import keystone_audit

    def _dead():
        raise ConnectionError("connection refused")

    monkeypatch.setattr(keystone_audit, "get_token_issuance_log", _dead)

    res = client.get("/api/v1/security/keystone-tokens", headers=_make_user_headers("admin"))

    assert res.status_code == 200
    body = res.json()
    assert body["degraded"] is True
    assert body["has_signal"] is False
