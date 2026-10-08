"""
services/api/tests/test_remediation_approval.py

Roadmap 4.3: approve / reject / ask-for-more-info on a proposed fix, every
decision logged. The acceptance line this file exists to prove is
"an audit trail entry is created for each decision, timestamped".

Sections:
1. The decision rules (service level, real Postgres like the other DB-backed
   tests -- the append-only trigger and the advisory lock are Postgres
   behaviour, so there is nothing meaningful to fake).
2. The audit trail: one timestamped entry per decision, what it records,
   append-only enforcement, and the hash chain that detects tampering.
3. The HTTP surface: roles, status codes, and that the server's snapshot -- not
   the request -- decides what was approved.
4. POST /orchestrate: a fix proposal is recorded and the handle comes back.
"""
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import crud
from app.agents.nodes import openstack_expert as expert
from app.agents.nodes.openstack_expert_catalog import CATALOG
from app.agents.nodes.remediation import build_fix_proposal
from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app.main import app
from app.services import remediation_approval as approval

client = TestClient(app)
BY_ID = {entry["id"]: entry for entry in CATALOG}


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    """Same guard test_security_audit_log.py uses (another test module leaks
    a get_current_user dependency_override with no teardown)."""
    had_override = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had_override:
        app.dependency_overrides[get_current_user] = saved


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------

def _make_user(role: str = "viewer"):
    db = SessionLocal()
    try:
        user = crud.create_user(
            db,
            username=f"approval-test-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("irrelevant-for-this-test"),
            role=role,
        )
        return user, {"Authorization": f"Bearer {create_access_token(user)}"}
    finally:
        db.close()


def _proposal(symptom_id: str = "nova-compute-down", *, effective_risk: str | None = None) -> dict:
    """A real proposal from the real catalog, made unique per call so tests
    do not collide on the deterministic proposal_id. `effective_risk` forces
    what the 4.2 simulator would have written onto the primary step."""
    result = expert._build_result(
        BY_ID[symptom_id], "compute-02's cpu is flagged critical.", "compute-02", extra_raw={"diagnosed_by": "monitoring"}
    )
    proposal = copy.deepcopy(build_fix_proposal(result["raw_data"]))
    proposal["proposal_id"] = f"fix-{uuid.uuid4().hex[:12]}"
    if effective_risk:
        proposal["primary"]["effective_risk"] = effective_risk
    return proposal


def _record(user=None, **kw):
    db = SessionLocal()
    try:
        return approval.record_proposal(db, trace_id=None, proposal=_proposal(**kw), requested_by=user)
    finally:
        db.close()


def _decide(row_id, decision, actor, comment=None):
    db = SessionLocal()
    try:
        return approval.decide(db, proposal_row_id=row_id, decision=decision, comment=comment, actor=actor)
    finally:
        db.close()


def _entries(row_id):
    db = SessionLocal()
    try:
        return approval.history(db, row_id)
    finally:
        db.close()


def _status(row_id):
    db = SessionLocal()
    try:
        return approval.get_proposal(db, row_id).status
    finally:
        db.close()


# --------------------------------------------------------------------
# 1. Decision rules
# --------------------------------------------------------------------

def test_a_new_proposal_starts_proposed_and_is_logged_as_proposed():
    requester, _ = _make_user("viewer")
    row = _record(requester)
    assert row.status == "proposed"
    (entry,) = _entries(row.id)
    assert (entry.event, entry.from_status, entry.to_status) == ("proposed", None, "proposed")
    assert entry.actor_username == requester.username
    assert entry.details["source"] == "remediation_agent"


def test_recording_the_same_proposal_for_the_same_turn_twice_is_a_no_op():
    admin, _ = _make_user("admin")
    # Real FK to agent_traces, so write the trace first.
    db = SessionLocal()
    try:
        trace = crud.create_agent_trace(
            db, trace_id=uuid.uuid4(), user_query="fix it", intent="remediation", target_agent="remediation",
            critic_verdict_status=None, degraded=False, steps=[], final_answer="x", duration_ms=1.0,
            user_id=admin.id, user_role=admin.role,
        )
        proposal = _proposal()
        first = approval.record_proposal(db, trace_id=trace.id, proposal=proposal, requested_by=admin)
        second = approval.record_proposal(db, trace_id=trace.id, proposal=proposal, requested_by=admin)
        assert first.id == second.id
        assert len(approval.history(db, first.id)) == 1
    finally:
        db.close()


def test_admin_can_approve_and_the_status_follows():
    admin, _ = _make_user("admin")
    row = _record()
    updated, entry = _decide(row.id, "approve", admin)
    assert updated.status == "approved"
    assert (entry.event, entry.from_status, entry.to_status) == ("approved", "proposed", "approved")
    assert _status(row.id) == "approved"


def test_reject_needs_a_reason_and_records_it():
    admin, _ = _make_user("admin")
    row = _record()
    with pytest.raises(approval.DecisionError) as err:
        _decide(row.id, "reject", admin)
    assert err.value.status_code == 422
    assert _status(row.id) == "proposed"            # nothing changed...
    assert len(_entries(row.id)) == 1               # ...and nothing was logged

    _, entry = _decide(row.id, "reject", admin, "  Wrong host -- this is compute-03.  ")
    assert entry.event == "rejected"
    assert entry.comment == "Wrong host -- this is compute-03."   # trimmed
    assert _status(row.id) == "rejected"


def test_a_viewer_cannot_approve_or_reject_and_nothing_is_logged():
    viewer, _ = _make_user("viewer")
    row = _record()
    for decision in ("approve", "reject"):
        with pytest.raises(approval.DecisionError) as err:
            _decide(row.id, decision, viewer, "please")
        assert err.value.status_code == 403
    assert _status(row.id) == "proposed"
    assert len(_entries(row.id)) == 1


def test_anyone_can_ask_for_more_info_and_the_proposal_stays_open():
    viewer, _ = _make_user("viewer")
    admin, _ = _make_user("admin")
    row = _record()

    updated, entry = _decide(row.id, "ask_more_info", viewer, "Which guests are on compute-02?")
    assert updated.status == "info_requested"
    assert entry.event == "info_requested"
    assert entry.comment == "Which guests are on compute-02?"

    # Still undecided: can be asked again, then decided either way.
    _decide(row.id, "ask_more_info", viewer, "And is the restart reversible?")
    _, approved = _decide(row.id, "approve", admin)
    assert approved.from_status == "info_requested"
    assert [e.event for e in _entries(row.id)] == ["proposed", "info_requested", "info_requested", "approved"]


def test_asking_for_more_info_needs_a_question():
    viewer, _ = _make_user("viewer")
    row = _record()
    with pytest.raises(approval.DecisionError) as err:
        _decide(row.id, "ask_more_info", viewer, "   ")
    assert err.value.status_code == 422


@pytest.mark.parametrize("first", ["approve", "reject"])
def test_a_final_decision_cannot_be_decided_again(first):
    admin, _ = _make_user("admin")
    row = _record()
    _decide(row.id, first, admin, "because")
    before = len(_entries(row.id))
    for again in ("approve", "reject", "ask_more_info"):
        with pytest.raises(approval.DecisionError) as err:
            _decide(row.id, again, admin, "second thoughts")
        assert err.value.status_code == 409
    assert len(_entries(row.id)) == before          # no contradicting entry was written


def test_approving_a_high_risk_fix_needs_a_comment_but_a_medium_one_does_not():
    admin, _ = _make_user("admin")
    risky = _record(effective_risk="high")
    with pytest.raises(approval.DecisionError) as err:
        _decide(risky.id, "approve", admin)
    assert err.value.status_code == 422
    assert "high risk" in err.value.message
    _, entry = _decide(risky.id, "approve", admin, "Reviewed with the on-call; guests drained first.")
    assert entry.details["effective_risk"] == "high"

    routine = _record()
    _, entry = _decide(routine.id, "approve", admin)
    assert entry.comment is None


def test_unknown_proposal_is_a_404_and_overlong_comment_is_refused():
    admin, _ = _make_user("admin")
    with pytest.raises(approval.DecisionError) as err:
        _decide(uuid.uuid4(), "approve", admin)
    assert err.value.status_code == 404

    row = _record()
    with pytest.raises(approval.DecisionError) as err:
        _decide(row.id, "reject", admin, "x" * (approval.MAX_COMMENT_LENGTH + 1))
    assert err.value.status_code == 422


def test_two_simultaneous_decisions_on_one_proposal_yield_exactly_one_winner():
    admin, _ = _make_user("admin")
    row = _record()

    def attempt(decision):
        try:
            _decide(row.id, decision, admin, "racing")
            return "won"
        except approval.DecisionError as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(map(str, pool.map(attempt, ["approve", "reject"])))

    assert outcomes == ["409", "won"]
    final = [e for e in _entries(row.id) if e.event in ("approved", "rejected")]
    assert len(final) == 1
    assert _status(row.id) == final[0].to_status
    assert _chain_ok()


def _chain_ok() -> bool:
    # Close the session: an open read transaction would hold a table lock and
    # block the TRUNCATE / ALTER TABLE statements later tests issue.
    db = SessionLocal()
    try:
        return approval.verify_chain(db).ok
    finally:
        db.close()


# --------------------------------------------------------------------
# 2. The audit trail
# --------------------------------------------------------------------

def test_every_decision_creates_one_timestamped_entry_with_who_what_and_why():
    admin, _ = _make_user("admin")
    row = _record()
    start = datetime.now(timezone.utc) - timedelta(seconds=1)

    _, entry = _decide(row.id, "approve", admin, "Quiet window, guests migrated.")

    assert entry.created_at is not None and entry.created_at >= start
    assert entry.created_at <= datetime.now(timezone.utc) + timedelta(seconds=1)
    assert entry.actor_user_id == admin.id
    assert entry.actor_username == admin.username
    assert entry.actor_role == "admin"
    assert entry.comment == "Quiet window, guests migrated."
    # What was approved is copied from the server's snapshot:
    assert entry.details["command"] == "docker restart nova_compute"
    assert entry.details["host"] == "compute-02"
    assert entry.details["proposal_digest"] == approval.proposal_digest(row.proposal)
    assert entry.proposal_id == row.proposal_id


def test_the_entry_survives_the_account_being_renamed_or_removed_because_it_is_a_snapshot():
    admin, _ = _make_user("admin")
    row = _record()
    _, entry = _decide(row.id, "reject", admin, "no")
    assert entry.actor_username == admin.username     # frozen text, not a live join
    # actor_user_id is a plain column, not an FK: removing the user must not
    # try to UPDATE an append-only row.
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": admin.id})
        db.commit()
    finally:
        db.close()
    (_, rejected) = _entries(row.id)
    assert rejected.actor_username == admin.username


def test_the_audit_table_refuses_update_delete_and_truncate():
    admin, _ = _make_user("admin")
    row = _record()
    _decide(row.id, "approve", admin)
    for statement in (
        "UPDATE remediation_audit_log SET comment = 'edited' WHERE proposal_row_id = :id",
        "DELETE FROM remediation_audit_log WHERE proposal_row_id = :id",
        "TRUNCATE remediation_audit_log",
    ):
        db = SessionLocal()
        try:
            with pytest.raises(DBAPIError) as err:
                db.execute(text(statement), {"id": row.id} if ":id" in statement else {})
            assert "append-only" in str(err.value)
        finally:
            db.rollback()
            db.close()
    assert len(_entries(row.id)) == 2


def test_entry_hash_is_deterministic_and_depends_on_every_field():
    base = dict(
        proposal_row_id=uuid.UUID(int=1), proposal_id="fix-1", event="approved", from_status="proposed",
        to_status="approved", actor_user_id=uuid.UUID(int=2), actor_username="a", actor_role="admin",
        comment="ok", details={"command": "x"}, created_at=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
    )
    reference = approval.compute_entry_hash("0" * 64, **base)
    assert reference == approval.compute_entry_hash("0" * 64, **base)
    assert reference != approval.compute_entry_hash("1" * 64, **base)
    for field, other in [
        ("comment", "different"), ("to_status", "rejected"), ("actor_username", "b"),
        ("details", {"command": "y"}), ("created_at", datetime(2026, 10, 8, 12, 0, 1, tzinfo=timezone.utc)),
    ]:
        assert approval.compute_entry_hash("0" * 64, **{**base, field: other}) != reference, field
    # The same instant spelled in another timezone hashes the same.
    shifted = base["created_at"].astimezone(timezone(timedelta(hours=1)))
    assert approval.compute_entry_hash("0" * 64, **{**base, "created_at": shifted}) == reference


def test_the_chain_verifies_and_detects_an_entry_edited_behind_the_triggers_back():
    admin, _ = _make_user("admin")
    row = _record()
    _, entry = _decide(row.id, "reject", admin, "original reason")

    db = SessionLocal()
    try:
        assert approval.verify_chain(db).ok
    finally:
        db.close()

    # Simulate what the trigger cannot stop: a superuser editing the table.
    admin_db = SessionLocal()
    try:
        try:
            admin_db.execute(text("ALTER TABLE remediation_audit_log DISABLE TRIGGER USER"))
        except DBAPIError:
            admin_db.rollback()
            pytest.skip("test database user cannot disable triggers")
        try:
            admin_db.execute(
                text("UPDATE remediation_audit_log SET comment = 'forged' WHERE id = :id"), {"id": entry.id}
            )
            admin_db.commit()

            check = SessionLocal()
            try:
                report = approval.verify_chain(check)
            finally:
                check.close()
            assert not report.ok
            assert report.broken_at == entry.id
            assert "altered" in report.reason
        finally:
            admin_db.rollback()
            admin_db.execute(
                text("UPDATE remediation_audit_log SET comment = 'original reason' WHERE id = :id"), {"id": entry.id}
            )
            admin_db.execute(text("ALTER TABLE remediation_audit_log ENABLE TRIGGER USER"))
            admin_db.commit()
    finally:
        admin_db.close()

    db = SessionLocal()
    try:
        assert approval.verify_chain(db).ok
    finally:
        db.close()


# --------------------------------------------------------------------
# 3. HTTP surface
# --------------------------------------------------------------------

def test_get_proposal_shows_status_history_and_whether_the_caller_may_decide():
    admin, admin_h = _make_user("admin")
    _, viewer_h = _make_user("viewer")
    row = _record()

    as_admin = client.get(f"/api/v1/remediation/proposals/{row.id}", headers=admin_h).json()
    as_viewer = client.get(f"/api/v1/remediation/proposals/{row.id}", headers=viewer_h).json()
    assert as_admin["status"] == "proposed"
    assert as_admin["command"] == "docker restart nova_compute"
    assert as_admin["can_decide"] is True
    assert as_viewer["can_decide"] is False
    assert [e["event"] for e in as_admin["history"]] == ["proposed"]

    client.post(f"/api/v1/remediation/proposals/{row.id}/decision", headers=admin_h, json={"decision": "approve"})
    after = client.get(f"/api/v1/remediation/proposals/{row.id}", headers=admin_h).json()
    assert after["can_decide"] is False             # nothing left to decide
    assert [e["event"] for e in after["history"]] == ["proposed", "approved"]


def test_decision_endpoint_status_codes():
    admin, admin_h = _make_user("admin")
    _, viewer_h = _make_user("viewer")
    row = _record()
    url = f"/api/v1/remediation/proposals/{row.id}/decision"

    assert client.post(url, json={"decision": "approve"}).status_code == 401
    assert client.post(url, headers=viewer_h, json={"decision": "approve"}).status_code == 403
    assert client.post(url, headers=admin_h, json={"decision": "reject"}).status_code == 422       # no reason
    assert client.post(url, headers=admin_h, json={"decision": "launch"}).status_code == 422        # not a decision
    assert client.post(f"/api/v1/remediation/proposals/{uuid.uuid4()}/decision",
                       headers=admin_h, json={"decision": "approve"}).status_code == 404

    ok = client.post(url, headers=admin_h, json={"decision": "approve", "comment": "go"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["proposal"]["status"] == "approved"
    assert body["entry"]["actor_username"] == admin.username
    assert body["entry"]["created_at"]
    assert client.post(url, headers=admin_h, json={"decision": "reject", "comment": "no"}).status_code == 409


def test_the_request_cannot_change_what_is_being_approved():
    """The body schema has no command field; extra fields are ignored and the
    audit entry still carries the server's own snapshot."""
    _, admin_h = _make_user("admin")
    row = _record()
    resp = client.post(
        f"/api/v1/remediation/proposals/{row.id}/decision",
        headers=admin_h,
        json={"decision": "approve", "command": "rm -rf /", "primary": {"command": "rm -rf /"}},
    )
    assert resp.status_code == 200
    assert resp.json()["entry"]["details"]["command"] == "docker restart nova_compute"


def test_the_global_audit_trail_and_integrity_check_are_admin_only():
    _, admin_h = _make_user("admin")
    _, viewer_h = _make_user("viewer")
    row = _record()
    client.post(f"/api/v1/remediation/proposals/{row.id}/decision", headers=admin_h, json={"decision": "approve"})

    for url in ("/api/v1/remediation/audit", "/api/v1/remediation/audit/verify"):
        assert client.get(url, headers=viewer_h).status_code == 403

    trail = client.get("/api/v1/remediation/audit", headers=admin_h, params={"proposal_id": row.proposal_id})
    assert trail.status_code == 200
    events = [e["event"] for e in trail.json()["entries"]]
    assert events == ["approved", "proposed"]       # newest first

    verify = client.get("/api/v1/remediation/audit/verify", headers=admin_h).json()
    assert verify["ok"] is True and verify["entries_checked"] >= 2


# --------------------------------------------------------------------
# 4. POST /orchestrate records the proposal and hands back the handle
# --------------------------------------------------------------------

class _FakeGraph:
    def __init__(self, proposal):
        self._proposal = proposal

    def invoke(self, state):
        return {
            "intent": "remediation",
            "target_agent": "remediation",
            "final_answer": "Proposed fix ...",
            "failures": [],
            "trace_events": [],
            "critic_verdict": {"status": "pass"},
            "resolved_entities": {},
            "agent_result": {
                "summary": "Proposed fix ...",
                "confidence": 0.9,
                "raw_data": {"source": "catalog", "fix_proposal": self._proposal},
            },
        }


def test_orchestrate_records_the_fix_proposal_and_returns_an_approval_handle(monkeypatch):
    from app.routers import agents as agents_router

    _, admin_h = _make_user("admin")
    proposal = _proposal()
    monkeypatch.setattr(agents_router, "app_graph", _FakeGraph(proposal))

    resp = client.post("/api/v1/agents/orchestrate", headers=admin_h, json={"query": "propose a fix for compute-02"})
    assert resp.status_code == 200
    body = resp.json()
    handle = body["raw_data"]["fix_proposal"]["approval"]
    assert handle["status"] == "proposed"

    # The handle is a real, decidable record tied to this turn's trace...
    got = client.get(f"/api/v1/remediation/proposals/{handle['id']}", headers=admin_h).json()
    assert uuid.UUID(got["trace_id"]) == uuid.UUID(body["trace_id"])
    assert got["proposal_id"] == proposal["proposal_id"]
    assert [e["event"] for e in got["history"]] == ["proposed"]
    # ...and the proposal itself reached the caller untouched apart from the handle.
    returned = {k: v for k, v in body["raw_data"]["fix_proposal"].items() if k != "approval"}
    assert returned == proposal


def test_a_turn_without_a_proposal_gets_no_handle(monkeypatch):
    from app.routers import agents as agents_router

    class _NoProposal(_FakeGraph):
        def invoke(self, state):
            result = super().invoke(state)
            result["agent_result"]["raw_data"] = {"source": "catalog"}
            return result

    _, admin_h = _make_user("admin")
    monkeypatch.setattr(agents_router, "app_graph", _NoProposal(None))
    body = client.post("/api/v1/agents/orchestrate", headers=admin_h, json={"query": "what is nova?"}).json()
    assert "fix_proposal" not in body["raw_data"]


def test_if_recording_fails_the_answer_still_arrives_without_approval_controls(monkeypatch):
    from app.routers import agents as agents_router

    def boom(*a, **k):
        raise RuntimeError("database hiccup")

    _, admin_h = _make_user("admin")
    monkeypatch.setattr(agents_router, "app_graph", _FakeGraph(_proposal()))
    monkeypatch.setattr(agents_router.remediation_approval, "record_proposal", boom)

    resp = client.post("/api/v1/agents/orchestrate", headers=admin_h, json={"query": "fix compute-02"})
    assert resp.status_code == 200
    assert "approval" not in resp.json()["raw_data"]["fix_proposal"]
