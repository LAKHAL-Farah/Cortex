"""
services/api/tests/test_rbac_incident_audit.py

Roadmap 4.8: minimal RBAC (who can validate a remediation) + an audit log
across the whole diagnose -> propose -> validate -> execute chain.

The acceptance lines this file exists to prove:

- ">=2 roles enforced"                                  -> sections 1-2
- "audit log queryable end-to-end for one full incident" -> section 3

Sections:
1. The permission matrix (pure) and who may validate what, over HTTP.
2. Execution honours the same ceiling; the UI flags tell the truth.
3. One incident, start to finish.
"""
import copy
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import crud, models, rbac
from app.agents.nodes import openstack_expert as expert
from app.agents.nodes.openstack_expert_catalog import CATALOG
from app.agents.nodes.remediation import build_fix_proposal
from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app.main import app
from app.services import alert_routing
from app.services import remediation_approval as approval
from app.services import remediation_execution as execution
from app.services.remediation_executor import ExecutionResult

client = TestClient(app)
BY_ID = {entry["id"]: entry for entry in CATALOG}


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    had = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had:
        app.dependency_overrides[get_current_user] = saved


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setenv("CORTEX_REMEDIATION_EXECUTION", "live")


@pytest.fixture
def runner(monkeypatch):
    plans = []

    def fake_run(plan):
        plans.append(plan)
        return ExecutionResult(True, plan.backend, plan.operation, "done (recorded, not run)")

    monkeypatch.setattr(execution, "run_plan", fake_run)
    return plans


def _user(role):
    db = SessionLocal()
    try:
        user = crud.create_user(
            db, username=f"rbac-{role}-{uuid.uuid4().hex[:6]}", password_hash=hash_password("x" * 12), role=role
        )
        return user, {"Authorization": f"Bearer {create_access_token(user)}"}
    finally:
        db.close()


def _proposal(risk="medium", host="compute-02") -> dict:
    result = expert._build_result(BY_ID["nova-compute-down"], f"{host}'s service is down.", host,
                                  extra_raw={"diagnosed_by": "monitoring"})
    proposal = copy.deepcopy(build_fix_proposal(result["raw_data"]))
    proposal["proposal_id"] = f"fix-{uuid.uuid4().hex[:12]}"
    proposal["primary"]["risk"] = risk
    proposal["primary"]["effective_risk"] = risk
    return proposal


def _record(risk="medium", host="compute-02", trace_id=None, requested_by=None, diagnosis=None) -> uuid.UUID:
    db = SessionLocal()
    try:
        return approval.record_proposal(
            db, trace_id=trace_id, proposal=_proposal(risk, host), requested_by=requested_by, diagnosis=diagnosis
        ).id
    finally:
        db.close()


def _decide(row_id, decision, headers, comment="reviewed, looks right"):
    return client.post(f"/api/v1/remediation/proposals/{row_id}/decision", headers=headers,
                       json={"decision": decision, "comment": comment})


def _digest(row_id) -> str:
    db = SessionLocal()
    try:
        return approval.proposal_digest(approval.get_proposal(db, row_id).proposal)
    finally:
        db.close()


def _execute(row_id, headers):
    return client.post(f"/api/v1/remediation/proposals/{row_id}/execute", headers=headers,
                       json={"confirm": True, "proposal_digest": _digest(row_id)})


def _events(row_id) -> list[str]:
    db = SessionLocal()
    try:
        return [e.event for e in approval.history(db, row_id)]
    finally:
        db.close()


def _status(row_id) -> str:
    db = SessionLocal()
    try:
        return approval.get_proposal(db, row_id).status
    finally:
        db.close()


# --------------------------------------------------------------------
# 1. The matrix, and who may validate what
# --------------------------------------------------------------------

@pytest.mark.parametrize("role,risk,allowed", [
    ("admin", "low", True), ("admin", "medium", True), ("admin", "high", True), ("admin", None, True),
    ("operator", "low", True), ("operator", "medium", True),
    ("operator", "high", False), ("operator", None, False), ("operator", "catastrophic", False),
    ("viewer", "low", False), ("viewer", "high", False), (None, "low", False), ("intern", "low", False),
])
def test_permission_matrix(role, risk, allowed):
    assert rbac.can_validate(role, risk) is allowed


def test_unknown_risk_is_never_easier_than_known_risk():
    assert rbac.required_role_for("low") == rbac.OPERATOR
    assert rbac.required_role_for("medium") == rbac.OPERATOR
    assert rbac.required_role_for("high") == rbac.ADMIN
    assert rbac.required_role_for(None) == rbac.ADMIN
    assert rbac.required_role_for("new-level-from-the-future") == rbac.ADMIN


@pytest.mark.parametrize("risk", ["low", "medium"])
def test_an_operator_can_approve_and_reject_low_and_medium_risk(risk):
    _, operator_h = _user("operator")
    approve_id, reject_id = _record(risk), _record(risk)

    assert _decide(approve_id, "approve", operator_h).status_code == 200
    assert _decide(reject_id, "reject", operator_h, "not now").status_code == 200
    assert (_status(approve_id), _status(reject_id)) == ("approved", "rejected")


def test_an_operator_cannot_decide_a_high_risk_fix_and_nothing_changes():
    operator, operator_h = _user("operator")
    row_id = _record("high")

    for decision in ("approve", "reject"):
        response = _decide(row_id, decision, operator_h)
        assert response.status_code == 403
        assert "admin" in response.json()["detail"] and "high" not in response.json()["detail"].split("rated")[0]
    assert _status(row_id) == "proposed"
    assert _events(row_id) == ["proposed"]

    # ...but an admin can, and the trail shows who.
    admin, admin_h = _user("admin")
    assert _decide(row_id, "approve", admin_h, "reviewed the blast radius, go").status_code == 200
    assert _events(row_id) == ["proposed", "approved"]


def test_an_unknown_risk_is_treated_as_high():
    _, operator_h = _user("operator")
    row_id = _record(risk=None)

    assert _decide(row_id, "approve", operator_h).status_code == 403


def test_a_viewer_cannot_decide_at_any_risk_but_can_still_ask_questions():
    _, viewer_h = _user("viewer")
    for risk in ("low", "medium", "high"):
        row_id = _record(risk)
        assert _decide(row_id, "approve", viewer_h).status_code == 403
        assert _decide(row_id, "reject", viewer_h).status_code == 403
        assert _events(row_id) == ["proposed"]

    row_id = _record("low")
    assert _decide(row_id, "ask_more_info", viewer_h, "what does it restart?").status_code == 200
    assert _events(row_id)[:2] == ["proposed", "info_requested"]   # (the answer is logged after it)


def test_there_are_three_enforced_roles_with_three_different_reaches():
    """The roadmap says '>=2 roles enforced'. Same medium-risk fix, then the
    same high-risk fix, three accounts: the outcomes differ per role."""
    _, viewer_h = _user("viewer")
    _, operator_h = _user("operator")
    _, admin_h = _user("admin")
    medium, high = _record("medium"), _record("high")

    outcome = {
        role: (_decide(medium, "approve", h).status_code if role != "admin" else None,)
        for role, h in (("viewer", viewer_h), ("operator", operator_h))
    }
    assert outcome == {"viewer": (403,), "operator": (200,)}
    assert [_decide(high, "approve", h, "ok").status_code for h in (viewer_h, operator_h, admin_h)] == [403, 403, 200]


def test_an_admin_can_create_an_operator_account():
    _, admin_h = _user("admin")
    name = f"newop-{uuid.uuid4().hex[:6]}"

    created = client.post("/api/v1/auth/users", headers=admin_h,
                          json={"username": name, "password": "a-long-enough-pass", "role": "operator"})

    assert created.status_code == 201 and created.json()["role"] == "operator"
    assert client.post("/api/v1/auth/users", headers=admin_h,
                       json={"username": f"{name}x", "password": "a-long-enough-pass", "role": "superuser"}
                       ).status_code == 422


# --------------------------------------------------------------------
# 2. Execution honours the same ceiling; the UI flags tell the truth
# --------------------------------------------------------------------

def test_an_operator_can_run_a_medium_risk_fix_but_not_a_high_one(runner):
    _, operator_h = _user("operator")
    _, admin_h = _user("admin")
    medium, high = _record("medium"), _record("high")
    assert _decide(medium, "approve", operator_h).status_code == 200
    assert _decide(high, "approve", admin_h, "approved by an admin").status_code == 200

    assert _execute(medium, operator_h).status_code == 202
    refused = _execute(high, operator_h)

    assert refused.status_code == 403
    assert _status(high) == "approved"                       # untouched, still waiting for an admin
    assert len(runner) == 1
    assert _execute(high, admin_h).status_code == 202 and len(runner) == 2


def test_a_viewer_cannot_execute_anything(runner):
    _, viewer_h = _user("viewer")
    _, admin_h = _user("admin")
    row_id = _record("low")
    assert _decide(row_id, "approve", admin_h).status_code == 200

    assert _execute(row_id, viewer_h).status_code == 403
    assert runner == []


def test_can_decide_and_can_execute_flags_follow_role_and_risk():
    admin, admin_h = _user("admin")
    _, operator_h = _user("operator")
    _, viewer_h = _user("viewer")
    low, high = _record("low"), _record("high")

    def view(row_id, headers):
        return client.get(f"/api/v1/remediation/proposals/{row_id}", headers=headers).json()

    assert [view(low, h)["can_decide"] for h in (admin_h, operator_h, viewer_h)] == [True, True, False]
    assert [view(high, h)["can_decide"] for h in (admin_h, operator_h, viewer_h)] == [True, False, False]

    operator_view = view(high, operator_h)
    assert "admin" in operator_view["approver_hint"]
    assert view(high, admin_h)["approver_hint"] is None
    assert "read-only" in view(low, viewer_h)["approver_hint"]


def test_audit_endpoints_are_for_admins_and_operators_only():
    _, viewer_h = _user("viewer")
    _, operator_h = _user("operator")
    _, admin_h = _user("admin")
    trace = uuid.uuid4()
    paths = ["/api/v1/remediation/audit", "/api/v1/remediation/audit/verify",
             "/api/v1/remediation/audit/incidents", f"/api/v1/remediation/audit/incidents/{trace}"]

    for path in paths:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=viewer_h).status_code == 403
    for path in paths[:3]:
        assert client.get(path, headers=operator_h).status_code == 200
        assert client.get(path, headers=admin_h).status_code == 200
    assert client.get(paths[3], headers=operator_h).status_code == 404       # allowed, but nothing recorded


# --------------------------------------------------------------------
# 3. One incident, start to finish
# --------------------------------------------------------------------

def _diagnose(requester) -> tuple[uuid.UUID, dict]:
    """What POST /agents/orchestrate does: persist the trace, then record the
    proposal together with the diagnosis. Returns (trace_id, diagnosis)."""
    trace_id = uuid.uuid4()
    steps = [
        {"node": "router", "status": "ok", "duration_ms": 12.0, "timestamp": "2026-10-10T12:00:00Z"},
        {"node": "openstack_expert", "status": "ok", "duration_ms": 840.0, "timestamp": "2026-10-10T12:00:01Z"},
        {"node": "critic", "status": "ok", "duration_ms": 95.0, "timestamp": "2026-10-10T12:00:02Z"},
    ]
    db = SessionLocal()
    try:
        crud.create_agent_trace(
            db, trace_id=trace_id, user_query="compute-02 stopped answering, what do I do?", intent="troubleshoot",
            target_agent="openstack_expert", critic_verdict_status="approved", degraded=False, steps=steps,
            final_answer="nova-compute is down on compute-02", duration_ms=1200.0,
            user_id=requester.id, user_role=requester.role,
        )
    finally:
        db.close()
    diagnosis = approval.diagnosis_summary(
        trace_id=trace_id, user_query="compute-02 stopped answering, what do I do?", intent="troubleshoot",
        target_agent="openstack_expert", critic_verdict="approved", degraded=False, steps=steps,
    )
    return trace_id, diagnosis


def test_one_full_incident_is_queryable_end_to_end(runner):
    viewer, _ = _user("viewer")               # the person who asked the agent
    operator, operator_h = _user("operator")  # validates and runs it
    _, admin_h = _user("admin")               # audits it afterwards
    host = f"compute-{uuid.uuid4().hex[:4]}"

    # An alert that was open on that host meanwhile (routed like production does).
    db = SessionLocal()
    try:
        alert = models.AnomalyEvent(hostname=host, metric_name="service_down", current_value=1.0, z_score=0.0,
                                    severity="critical", method="service_state", started_at=datetime.utcnow() - timedelta(minutes=2))
        alert_routing.route_event(db, alert)
        db.commit()
        alert_id = alert.id
    finally:
        db.close()

    # diagnose -> propose -> validate -> execute
    trace_id, diagnosis = _diagnose(viewer)
    row_id = _record("medium", host=host, trace_id=trace_id, requested_by=viewer, diagnosis=diagnosis)
    assert _decide(row_id, "approve", operator_h, "confirmed on the console, go ahead").status_code == 200
    assert _execute(row_id, operator_h).status_code == 202
    assert len(runner) == 1

    # ---- the audit read: one call, whole story ----
    response = client.get(f"/api/v1/remediation/audit/incidents/{trace_id}", headers=admin_h)
    assert response.status_code == 200
    incident = response.json()

    assert incident["outcome"] == "executed" and incident["complete"] is True
    assert [(s["stage"], s["done"], s["chained"]) for s in incident["stages"]] == [
        ("diagnose", True, True), ("propose", True, True), ("validate", True, True), ("execute", True, True),
    ]
    by_stage = {s["stage"]: s for s in incident["stages"]}
    assert by_stage["diagnose"]["by"] == viewer.username
    assert by_stage["validate"]["by"] == operator.username and by_stage["execute"]["by"] == operator.username

    assert [e["event"] for e in incident["entries"]] == [
        "diagnosed", "proposed", "approved", "execution_started", "executed",
    ]
    assert [e["actor_username"] for e in incident["entries"]][2:] == [operator.username] * 3
    approved = incident["entries"][2]
    assert approved["comment"] == "confirmed on the console, go ahead"     # the *why* survives

    assert incident["trace"]["user_query"].startswith("compute-02 stopped answering")
    assert incident["trace"]["target_agent"] == "openstack_expert"
    assert [s["node"] for s in incident["trace"]["steps"]] == ["router", "openstack_expert", "critic"]
    assert incident["proposals"][0]["host"] == host and incident["proposals"][0]["status"] == "executed"
    assert incident["chain"]["ok"] is True and incident["chain"]["entries_checked"] >= 5

    # The alert that was open on the host is attached, with where it was routed.
    alerts = {a["id"]: a for a in incident["alerts"]}
    assert str(alert_id) in alerts
    assert alerts[str(alert_id)]["routing"]["channels"] == ["dashboard", "email"]

    # Same entries via the flat audit view, filtered by incident / actor / event.
    flat = client.get(f"/api/v1/remediation/audit?trace_id={trace_id}", headers=admin_h).json()["entries"]
    assert sorted(e["id"] for e in flat) == sorted(e["id"] for e in incident["entries"])
    mine = client.get(f"/api/v1/remediation/audit?trace_id={trace_id}&actor={operator.username}", headers=admin_h)
    assert {e["event"] for e in mine.json()["entries"]} == {"approved", "execution_started", "executed"}
    only = client.get(f"/api/v1/remediation/audit?trace_id={trace_id}&event=diagnosed", headers=admin_h)
    assert [e["event"] for e in only.json()["entries"]] == ["diagnosed"]

    # It also shows up in the incident list.
    listed = {i["trace_id"]: i for i in client.get("/api/v1/remediation/audit/incidents", headers=admin_h).json()}
    assert listed[str(trace_id)]["outcome"] == "executed" and listed[str(trace_id)]["hosts"] == [host]


def test_a_rejected_incident_reads_as_incomplete_with_the_reason(runner):
    viewer, _ = _user("viewer")
    _, admin_h = _user("admin")
    trace_id, diagnosis = _diagnose(viewer)
    row_id = _record("high", trace_id=trace_id, requested_by=viewer, diagnosis=diagnosis)
    assert _decide(row_id, "reject", admin_h, "restarting during the batch window would hurt").status_code == 200

    incident = client.get(f"/api/v1/remediation/audit/incidents/{trace_id}", headers=admin_h).json()

    assert incident["outcome"] == "rejected" and incident["complete"] is False
    assert {s["stage"]: s["done"] for s in incident["stages"]} == {
        "diagnose": True, "propose": True, "validate": True, "execute": False,
    }
    assert incident["entries"][-1]["comment"] == "restarting during the batch window would hurt"
    assert runner == []


def test_a_proposal_recorded_without_a_diagnosis_still_has_a_timeline():
    """Older proposals (and callers that do not pass the diagnosis) start the
    chain at `proposed`; the trace still shows the diagnose stage, marked as
    not vouched for by the chain."""
    viewer, _ = _user("viewer")
    _, admin_h = _user("admin")
    trace_id, _unused = _diagnose(viewer)
    _record("low", trace_id=trace_id, requested_by=viewer, diagnosis=None)

    incident = client.get(f"/api/v1/remediation/audit/incidents/{trace_id}", headers=admin_h).json()

    diagnose = incident["stages"][0]
    assert diagnose["done"] is True and diagnose["chained"] is False
    assert [e["event"] for e in incident["entries"]] == ["proposed"]


def test_the_diagnosed_entry_binds_the_question_without_storing_it():
    viewer, viewer_h = _user("viewer")
    trace_id, diagnosis = _diagnose(viewer)
    row_id = _record("low", trace_id=trace_id, requested_by=viewer, diagnosis=diagnosis)

    # A viewer can read a proposal's history, so it must not carry the question.
    history = client.get(f"/api/v1/remediation/proposals/{row_id}", headers=viewer_h).json()["history"]
    diagnosed = history[0]
    assert diagnosed["event"] == "diagnosed"
    assert "compute-02 stopped answering" not in str(diagnosed)
    assert len(diagnosed["details"]["diagnosis"]["query_digest"]) == 64
    assert [s["node"] for s in diagnosed["details"]["diagnosis"]["steps"]] == ["router", "openstack_expert", "critic"]


def test_the_chain_stays_valid_after_a_diagnosed_and_proposed_pair():
    """Regression: two appends in one transaction must chain, not fork."""
    viewer, _ = _user("viewer")
    _, admin_h = _user("admin")
    trace_id, diagnosis = _diagnose(viewer)
    _record("low", trace_id=trace_id, requested_by=viewer, diagnosis=diagnosis)

    report = client.get("/api/v1/remediation/audit/verify", headers=admin_h).json()

    assert report["ok"] is True, report


def test_recording_the_same_proposal_twice_does_not_duplicate_the_diagnosis():
    viewer, _ = _user("viewer")
    trace_id, diagnosis = _diagnose(viewer)
    proposal = _proposal("low")
    db = SessionLocal()
    try:
        first = approval.record_proposal(db, trace_id=trace_id, proposal=proposal, requested_by=viewer, diagnosis=diagnosis)
        second = approval.record_proposal(db, trace_id=trace_id, proposal=proposal, requested_by=viewer, diagnosis=diagnosis)
        assert first.id == second.id
    finally:
        db.close()
    assert _events(first.id) == ["diagnosed", "proposed"]


def test_an_unknown_incident_is_a_404():
    _, admin_h = _user("admin")

    assert client.get(f"/api/v1/remediation/audit/incidents/{uuid.uuid4()}", headers=admin_h).status_code == 404
