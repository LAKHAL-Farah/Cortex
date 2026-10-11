"""
services/api/tests/test_remediation_execution.py

Roadmap 4.4: "approved fixes execute via Ansible/OpenStack SDK only after
explicit click" / acceptance: "nothing executes without approval (Confidence
Ladder Level 0)". Plus the 4.3 follow-up: asking for more information now gets
an answer.

The line this file exists to prove is the acceptance one, so most tests are
shaped as "try to make it run without X, observe that the runner was never
called". The backend (`run_plan`) is replaced by a recorder in every test:
nothing here can reach OpenStack or Ansible.

Sections:
1. Level 0 -- nothing runs without approval, a role, and the click.
2. What is checked again at click time (approval age, impact, plan, host).
3. The lifecycle in the audit trail (success, failure, retry, interruption).
4. Concurrency and robustness.
5. What the screen is told (GET).
6. "Ask for more information" gets an answer.
"""
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.agents.nodes import openstack_expert as expert
from app.agents.nodes.openstack_expert_catalog import CATALOG
from app.agents.nodes.remediation import build_fix_proposal
from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app.main import app
from app.services import remediation_approval as approval
from app.services import remediation_execution as execution
from app.services import remediation_executor as executor
from app.services.remediation_executor import ExecutionResult

client = TestClient(app)
BY_ID = {entry["id"]: entry for entry in CATALOG}
UUID_A = "3f2b8c1e-5d4a-4b7e-9a10-0c6d2e8f1a77"


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    had_override = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had_override:
        app.dependency_overrides[get_current_user] = saved


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setenv("CORTEX_REMEDIATION_EXECUTION", "live")


class Recorder:
    """Stands in for `run_plan`. Records every plan it is asked to run."""

    def __init__(self):
        self.plans: list = []
        self.result_for = lambda plan: ExecutionResult(True, plan.backend, plan.operation, "done (recorded, not run)")

    def __call__(self, plan):
        self.plans.append(plan)
        return self.result_for(plan)


@pytest.fixture
def runner(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(execution, "run_plan", rec)
    return rec


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------

def _make_user(role: str):
    db = SessionLocal()
    try:
        user = crud.create_user(
            db,
            username=f"exec-test-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("irrelevant-for-this-test"),
            role=role,
        )
        return user, {"Authorization": f"Bearer {create_access_token(user)}"}
    finally:
        db.close()


def _proposal(symptom_id: str = "nova-compute-down", command: str | None = None, host: str = "compute-02") -> dict:
    result = expert._build_result(BY_ID[symptom_id], f"{host}'s service is down.", host, extra_raw={"diagnosed_by": "monitoring"})
    proposal = copy.deepcopy(build_fix_proposal(result["raw_data"]))
    proposal["proposal_id"] = f"fix-{uuid.uuid4().hex[:12]}"
    if command is not None:
        proposal["primary"]["command"] = command
        proposal["primary"]["placeholders"] = []
        proposal["inputs_needed"] = []
    return proposal


def _simulated(proposal: dict, verdict: str = "caution") -> dict:
    sim = {"status": "simulated", "verdict": verdict, "headline": f"{verdict} impact", "effects": [], "effects_total": 0,
           "counts": {}, "assumptions": [], "warnings": [], "reversible": True, "duration": None}
    proposal["simulation"] = sim
    proposal["primary"]["simulation"] = sim
    return proposal


def _record(**kw) -> uuid.UUID:
    db = SessionLocal()
    try:
        proposal = kw.pop("proposal", None) or _proposal(**kw)
        return approval.record_proposal(db, trace_id=None, proposal=proposal, requested_by=None).id
    finally:
        db.close()


def _row(row_id):
    db = SessionLocal()
    try:
        row = approval.get_proposal(db, row_id)
        db.expunge(row)
        return row
    finally:
        db.close()


def _events(row_id) -> list[str]:
    db = SessionLocal()
    try:
        return [e.event for e in approval.history(db, row_id)]
    finally:
        db.close()


def _entries(row_id):
    db = SessionLocal()
    try:
        return approval.history(db, row_id)
    finally:
        db.close()


def _digest(row_id) -> str:
    return approval.proposal_digest(_row(row_id).proposal)


def _decide(row_id, decision, headers, comment=None):
    return client.post(f"/api/v1/remediation/proposals/{row_id}/decision", headers=headers,
                       json={"decision": decision, "comment": comment})


def _execute(row_id, headers, digest=None, **body):
    payload = {"confirm": True, "proposal_digest": digest or _digest(row_id), **body}
    return client.post(f"/api/v1/remediation/proposals/{row_id}/execute", headers=headers, json=payload)


@pytest.fixture
def admin():
    return _make_user("admin")


@pytest.fixture
def viewer():
    return _make_user("viewer")


def _approved(admin_headers, **kw) -> uuid.UUID:
    row_id = _record(**kw)
    assert _decide(row_id, "approve", admin_headers, "reviewed").status_code == 200
    return row_id


# --------------------------------------------------------------------
# 1. Level 0
# --------------------------------------------------------------------

def test_confidence_level_is_zero():
    assert execution.CONFIDENCE_LEVEL == 0


@pytest.mark.parametrize("setup", ["proposed", "info_requested", "rejected"])
def test_nothing_runs_for_a_proposal_that_is_not_approved(admin, runner, setup):
    _, h = admin
    row_id = _record()
    if setup == "info_requested":
        assert _decide(row_id, "ask_more_info", h, "what does it touch?").status_code == 200
    if setup == "rejected":
        assert _decide(row_id, "reject", h, "no").status_code == 200

    response = _execute(row_id, h)

    assert response.status_code == 409
    assert runner.plans == []
    assert _row(row_id).status == {"proposed": "proposed", "info_requested": "info_requested", "rejected": "rejected"}[setup]
    assert "execution_started" not in _events(row_id)


def test_approving_runs_nothing(admin, runner):
    _, h = admin
    row_id = _approved(h)
    assert runner.plans == []
    assert _row(row_id).status == "approved"
    assert _events(row_id) == ["proposed", "approved"]


def test_a_viewer_cannot_execute_even_an_approved_fix(admin, viewer, runner):
    _, admin_h = admin
    _, viewer_h = viewer
    row_id = _approved(admin_h)

    response = _execute(row_id, viewer_h)

    assert response.status_code == 403
    assert runner.plans == []
    assert _row(row_id).status == "approved"


def test_an_unauthenticated_caller_cannot_execute(admin, runner):
    _, h = admin
    row_id = _approved(h)
    response = client.post(f"/api/v1/remediation/proposals/{row_id}/execute",
                           json={"confirm": True, "proposal_digest": _digest(row_id)})
    assert response.status_code in (401, 403)
    assert runner.plans == []


def test_a_status_flipped_behind_the_trail_is_not_an_approval(admin, runner):
    """`status` is a cache of the audit trail (models.RemediationProposal), and
    a cache can be wrong -- a bad migration, a manual UPDATE, a bug. Execution
    reads the trail too: no `approved` entry, no run, whatever the column says."""
    from sqlalchemy import text

    _, h = admin
    row_id = _record()
    db = SessionLocal()
    try:
        db.execute(text("UPDATE remediation_proposals SET status = 'approved' WHERE id = :i"), {"i": row_id})
        db.commit()
    finally:
        db.close()
    assert _row(row_id).status == "approved" and _events(row_id) == ["proposed"]

    response = _execute(row_id, h)

    assert response.status_code == 409 and "No approval is on record" in response.json()["detail"]
    assert runner.plans == []
    assert _row(row_id).status == "approved"  # untouched: refusing must not write


def test_the_click_must_say_confirm_true(admin, runner):
    _, h = admin
    row_id = _approved(h)
    for body in ({}, {"proposal_digest": _digest(row_id)}, {"confirm": False, "proposal_digest": _digest(row_id)},
                 {"confirm": True}):
        assert client.post(f"/api/v1/remediation/proposals/{row_id}/execute", headers=h, json=body).status_code == 422
    assert runner.plans == []


def test_the_click_must_name_the_proposal_it_saw(admin, runner):
    _, h = admin
    row_id = _approved(h)
    response = _execute(row_id, h, digest="0" * 64)
    assert response.status_code == 409
    assert "not the one on record" in response.json()["detail"]
    assert runner.plans == []


def test_what_runs_comes_from_the_stored_snapshot_not_from_the_request(admin, runner):
    _, h = admin
    row_id = _approved(h)
    response = _execute(row_id, h, command="rm -rf /", host="evil", plan={"operation": "x"})
    assert response.status_code == 202
    assert [p.operation for p in runner.plans] == ["container.restart"]
    assert runner.plans[0].targets == ("nova_compute",) and runner.plans[0].host == "compute-02"


def test_the_happy_path_runs_once_and_is_fully_audited(admin, runner):
    user, h = admin
    row_id = _approved(h)

    response = _execute(row_id, h)

    assert response.status_code == 202
    assert response.json()["proposal"]["status"] == "executing"
    assert len(runner.plans) == 1 and runner.plans[0].backend == "ansible"

    assert _row(row_id).status == "executed"  # the background task finished
    entries = _entries(row_id)
    assert [e.event for e in entries] == ["proposed", "approved", "execution_started", "executed"]
    started, done = entries[2], entries[3]
    assert started.actor_username == user.username and started.from_status == "approved" and started.to_status == "executing"
    assert started.details["confidence_level"] == 0
    assert started.details["plan"]["operation"] == "container.restart"
    assert started.details["approved_by"] == user.username and started.details["attempt"] == 1
    assert done.actor_username == user.username and done.from_status == "executing" and done.to_status == "executed"
    assert done.details["execution"]["ok"] is True and done.details["execution"]["backend"] == "ansible"
    assert done.details["proposal_digest"] == approval.proposal_digest(_row(row_id).proposal)

    db = SessionLocal()
    try:
        assert approval.verify_chain(db).ok
    finally:
        db.close()


def test_an_sdk_fix_goes_through_the_sdk_backend(admin, runner):
    _, h = admin
    row_id = _approved(h, command="openstack compute service set --disable compute-02 nova-compute")
    assert _execute(row_id, h).status_code == 202
    assert runner.plans[0].backend == "openstack_sdk" and runner.plans[0].operation == "compute_service.disable"


def test_the_kill_switch_overrides_an_approval(admin, runner, monkeypatch):
    _, h = admin
    row_id = _approved(h)
    monkeypatch.setenv("CORTEX_REMEDIATION_EXECUTION", "off")

    response = _execute(row_id, h)

    assert response.status_code == 503
    assert runner.plans == []
    assert _row(row_id).status == "approved"
    got = client.get(f"/api/v1/remediation/proposals/{row_id}", headers=h).json()
    assert got["can_execute"] is False and "switched off" in got["execution"]["blocked_reason"]


def test_deciding_still_works_with_execution_switched_off(admin, monkeypatch):
    _, h = admin
    monkeypatch.setenv("CORTEX_REMEDIATION_EXECUTION", "off")
    row_id = _record()
    assert _decide(row_id, "approve", h, "ok").status_code == 200


# --------------------------------------------------------------------
# 2. Checked again at click time
# --------------------------------------------------------------------

def test_a_blank_still_to_be_filled_in_blocks_execution_but_not_approval(admin, runner):
    _, h = admin
    row_id = _record(symptom_id="instance-error-state")  # primary needs an <instance_id>
    assert _row(row_id).proposal["inputs_needed"]
    assert _decide(row_id, "approve", h, "will fill it in by hand").status_code == 200  # 4.3: allowed

    response = _execute(row_id, h)

    assert response.status_code == 422 and "filled in" in response.json()["detail"]
    assert runner.plans == [] and _row(row_id).status == "approved"


@pytest.mark.parametrize("command", [f"openstack server delete {UUID_A}", "docker system prune -a --volumes", f"openstack server rebuild {UUID_A} cirros"])
def test_destructive_fixes_can_be_approved_but_never_run(admin, runner, command):
    _, h = admin
    proposal = _proposal(command=command)
    proposal["primary"]["effective_risk"] = "high"
    row_id = _record(proposal=proposal)
    assert _decide(row_id, "approve", h, "reviewed by two people").status_code == 200

    response = _execute(row_id, h)

    assert response.status_code == 422
    assert runner.plans == [] and _row(row_id).status == "approved"
    got = client.get(f"/api/v1/remediation/proposals/{row_id}", headers=h).json()
    assert got["can_execute"] is False and got["execution"]["blocked_reason"]


def test_an_expired_approval_does_not_run(admin, runner, monkeypatch):
    _, h = admin
    row_id = _approved(h)
    monkeypatch.setattr(execution, "APPROVAL_TTL", timedelta(seconds=-1))

    response = _execute(row_id, h)

    assert response.status_code == 409 and "expired" in response.json()["detail"]
    assert runner.plans == [] and _row(row_id).status == "approved"


def test_a_fresh_approval_is_within_the_ttl_by_default():
    assert execution.APPROVAL_TTL >= timedelta(minutes=1)


def test_a_bigger_impact_since_approval_blocks_execution(admin, runner, monkeypatch):
    _, h = admin
    row_id = _approved(h, proposal=_simulated(_proposal(), "caution"))
    monkeypatch.setattr(execution, "simulate_proposal", lambda p: _simulated(copy.deepcopy(p), "disruptive"))

    response = _execute(row_id, h)

    assert response.status_code == 409 and "bigger impact" in response.json()["detail"]
    assert runner.plans == [] and _row(row_id).status == "approved"


def test_an_unreadable_living_model_blocks_a_simulated_fix(admin, runner, monkeypatch):
    _, h = admin
    row_id = _approved(h, proposal=_simulated(_proposal(), "safe"))
    unavailable = {"status": "unavailable", "verdict": None, "headline": "graph down"}
    monkeypatch.setattr(execution, "simulate_proposal", lambda p: {**p, "simulation": unavailable})

    response = _execute(row_id, h)

    assert response.status_code == 409 and "could not be read" in response.json()["detail"]
    assert runner.plans == []


def test_a_simulator_crash_blocks_rather_than_waves_it_through(admin, runner, monkeypatch):
    _, h = admin
    row_id = _approved(h, proposal=_simulated(_proposal(), "safe"))

    def boom(_):
        raise RuntimeError("neo4j exploded")

    monkeypatch.setattr(execution, "simulate_proposal", boom)
    assert _execute(row_id, h).status_code == 409
    assert runner.plans == []


@pytest.mark.parametrize("approved_verdict, fresh_verdict", [("caution", "caution"), ("disruptive", "safe"), ("disruptive", "caution")])
def test_the_same_or_a_lighter_impact_runs(admin, runner, monkeypatch, approved_verdict, fresh_verdict):
    _, h = admin
    proposal = _simulated(_proposal(), approved_verdict)
    proposal["primary"]["effective_risk"] = "high" if approved_verdict == "disruptive" else "medium"
    row_id = _record(proposal=proposal)
    assert _decide(row_id, "approve", h, "reviewed the exact command").status_code == 200
    monkeypatch.setattr(execution, "simulate_proposal", lambda p: _simulated(copy.deepcopy(p), fresh_verdict))

    assert _execute(row_id, h).status_code == 202
    assert len(runner.plans) == 1


def test_a_proposal_that_was_never_simulated_is_not_blocked_for_lack_of_a_simulation(admin, runner, monkeypatch):
    _, h = admin
    row_id = _approved(h)  # no simulation on this proposal; the approver saw that
    monkeypatch.setattr(execution, "simulate_proposal", lambda p: pytest.fail("must not be called"))
    assert _execute(row_id, h).status_code == 202


# --------------------------------------------------------------------
# 3. Lifecycle in the trail
# --------------------------------------------------------------------

def test_a_failed_run_is_recorded_and_can_be_retried_by_another_click(admin, runner):
    _, h = admin
    row_id = _approved(h)
    runner.result_for = lambda plan: ExecutionResult(False, plan.backend, plan.operation, "compute-02 was unreachable over SSH.",
                                                     output="UNREACHABLE!", error="unreachable")

    assert _execute(row_id, h).status_code == 202
    assert _row(row_id).status == "execution_failed"
    failed = _entries(row_id)[-1]
    assert failed.event == "execution_failed" and "unreachable" in failed.comment
    assert failed.details["execution"]["ok"] is False and failed.details["execution"]["output_tail"] == "UNREACHABLE!"

    # Not retried by itself: the second attempt needs its own click.
    assert len(runner.plans) == 1
    runner.result_for = lambda plan: ExecutionResult(True, plan.backend, plan.operation, "done")
    assert _execute(row_id, h).status_code == 202
    assert len(runner.plans) == 2
    assert _row(row_id).status == "executed"
    assert _events(row_id) == ["proposed", "approved", "execution_started", "execution_failed", "execution_started", "executed"]
    assert _entries(row_id)[4].details["attempt"] == 2


def test_an_executed_fix_cannot_run_again(admin, runner):
    _, h = admin
    row_id = _approved(h)
    assert _execute(row_id, h).status_code == 202
    again = _execute(row_id, h)
    assert again.status_code == 409 and "already executed" in again.json()["detail"]
    assert len(runner.plans) == 1


def test_no_decision_can_change_a_proposal_once_it_ran(admin, runner):
    _, h = admin
    row_id = _approved(h)
    assert _execute(row_id, h).status_code == 202
    for decision in ("approve", "reject", "ask_more_info"):
        response = _decide(row_id, decision, h, "too late")
        assert response.status_code == 409, decision
    assert _row(row_id).status == "executed"


def test_an_interrupted_run_is_reported_as_unknown_not_silently_rerun(admin, runner, monkeypatch):
    _, h = admin
    row_id = _approved(h)
    db = SessionLocal()
    try:
        user = crud.get_user_by_username(db, admin[0].username)
        execution.start_execution(db, proposal_row_id=row_id, actor=user, confirmed_digest=_digest(row_id))
    finally:
        db.close()
    assert _row(row_id).status == "executing"  # ...and the process "died" before run_execution

    # Within the window: a plain "already running".
    assert _execute(row_id, h).status_code == 409 and "already running" in _execute(row_id, h).json()["detail"]
    assert runner.plans == []

    # Past the window: the click records the interruption and still does NOT run.
    monkeypatch.setattr(execution, "EXECUTION_STALE_AFTER", timedelta(seconds=-1))
    response = _execute(row_id, h)
    assert response.status_code == 409 and "outcome is unknown" in response.json()["detail"]
    assert runner.plans == []
    assert _row(row_id).status == "execution_failed"
    last = _entries(row_id)[-1]
    assert last.event == "execution_failed" and last.details["interrupted"] is True

    # A deliberate second click is then allowed.
    assert _execute(row_id, h).status_code == 202
    assert len(runner.plans) == 1


def test_a_worker_that_dies_leaves_executing_not_a_false_result(admin, monkeypatch):
    _, h = admin
    row_id = _approved(h)

    def exploding_runner(plan):
        raise RuntimeError("worker killed")

    monkeypatch.setattr(execution, "run_plan", exploding_runner)
    assert _execute(row_id, h).status_code == 202  # run_execution swallowed it
    assert _row(row_id).status == "executing"
    assert _events(row_id)[-1] == "execution_started"


# --------------------------------------------------------------------
# 4. Concurrency
# --------------------------------------------------------------------

def test_two_simultaneous_clicks_start_exactly_one_run(admin, runner):
    user, h = admin
    row_id = _approved(h)
    digest = _digest(row_id)

    def click(_):
        db = SessionLocal()
        try:
            return execution.start_execution(db, proposal_row_id=row_id, actor=user, confirmed_digest=digest)[1].event
        except approval.DecisionError as exc:
            return str(exc.status_code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(click, range(2)))

    assert outcomes == ["409", "execution_started"]
    assert _events(row_id).count("execution_started") == 1


def test_the_chain_stays_valid_across_the_whole_lifecycle(admin, runner):
    _, h = admin
    row_id = _approved(h)
    _execute(row_id, h)
    db = SessionLocal()
    try:
        report = approval.verify_chain(db)
    finally:
        db.close()
    assert report.ok


def test_the_new_events_are_append_only_like_the_old_ones(admin, runner):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _, h = admin
    row_id = _approved(h)
    _execute(row_id, h)
    db = SessionLocal()
    try:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE remediation_audit_log SET comment = 'x' WHERE event = 'executed' AND proposal_row_id = :i"),
                       {"i": row_id})
            db.commit()
    finally:
        db.rollback()
        db.close()


# --------------------------------------------------------------------
# 5. What the screen is told
# --------------------------------------------------------------------

def test_get_tells_an_admin_that_an_approved_fix_can_be_executed(admin, viewer):
    _, ah = admin
    _, vh = viewer
    row_id = _approved(ah)
    as_admin = client.get(f"/api/v1/remediation/proposals/{row_id}", headers=ah).json()
    as_viewer = client.get(f"/api/v1/remediation/proposals/{row_id}", headers=vh).json()

    assert as_admin["can_execute"] is True and as_viewer["can_execute"] is False
    assert as_admin["execution"]["available"] is True and as_viewer["execution"]["available"] is True
    assert "nova_compute" in as_admin["execution"]["summary"] and as_admin["execution"]["backend"] == "ansible"
    assert as_admin["proposal_digest"] == _digest(row_id)
    assert as_admin["can_decide"] is False  # decided already


def test_get_offers_no_execute_before_approval(admin):
    _, h = admin
    row_id = _record()
    got = client.get(f"/api/v1/remediation/proposals/{row_id}", headers=h).json()
    assert got["can_execute"] is False and got["can_decide"] is True
    assert got["execution"]["available"] is False


def test_get_offers_execute_again_after_a_failure_and_not_after_success(admin, runner):
    _, h = admin
    row_id = _approved(h)
    runner.result_for = lambda plan: ExecutionResult(False, plan.backend, plan.operation, "nope", error="nope")
    _execute(row_id, h)
    assert client.get(f"/api/v1/remediation/proposals/{row_id}", headers=h).json()["can_execute"] is True
    runner.result_for = lambda plan: ExecutionResult(True, plan.backend, plan.operation, "ok")
    _execute(row_id, h)
    got = client.get(f"/api/v1/remediation/proposals/{row_id}", headers=h).json()
    assert got["can_execute"] is False and got["status"] == "executed"
    assert "already executed" in got["execution"]["blocked_reason"]


def test_the_orchestrate_handle_still_leads_to_a_proposal_that_cannot_run_unapproved(admin, runner):
    """End to end from the record the agent route makes: a freshly recorded
    proposal -- what POST /orchestrate hands the UI -- is not executable."""
    _, h = admin
    row_id = _record()
    assert _row(row_id).status == "proposed"
    assert _execute(row_id, h).status_code == 409
    assert runner.plans == []


# --------------------------------------------------------------------
# 6. "Ask for more information" now answers
# --------------------------------------------------------------------

def _ask(row_id, headers, question):
    return _decide(row_id, "ask_more_info", headers, question)


def test_a_question_gets_an_answer_in_the_trail(admin, viewer):
    _, ah = admin
    _, vh = viewer
    row_id = _record(proposal=_simulated(_proposal()))

    response = _ask(row_id, vh, "How do I undo it, and what will it affect?")

    assert response.status_code == 200
    assert _events(row_id) == ["proposed", "info_requested", "info_provided"]
    answer = _entries(row_id)[-1]
    assert answer.actor_username is None  # an answer from Cortex, not from a person
    assert answer.details["answers_entry_id"] == _entries(row_id)[1].id
    assert set(answer.details["topics"]) >= {"undo", "impact"} and answer.details["question_understood"] is True
    assert "caution impact" in answer.comment  # from the stored simulation
    assert answer.details["source"] == "proposal_snapshot"
    # Still open: an answer is not a decision.
    assert _row(row_id).status == "info_requested"
    got = client.get(f"/api/v1/remediation/proposals/{row_id}", headers=ah).json()
    assert got["can_decide"] is True and got["answer_pending"] is False
    assert got["history"][-1]["event"] == "info_provided"


def test_the_answer_only_says_what_the_proposal_holds(admin):
    _, h = admin
    proposal = _proposal()
    row_id = _record(proposal=proposal)
    _ask(row_id, h, "why was this proposed and how do I check it worked?")
    text = _entries(row_id)[-1].comment
    assert proposal["evidence"] in text
    for check in proposal["verify_commands"]:
        assert check["command"] in text
    assert "Simulation" not in text and "caution" not in text  # nothing invented about a simulation that never ran


def test_a_question_nobody_can_match_gets_the_overview_and_says_so(admin):
    _, h = admin
    row_id = _record()
    _ask(row_id, h, "zxqv blorp wibble?")
    answer = _entries(row_id)[-1]
    assert answer.details["question_understood"] is False
    assert "could not tell which part" in answer.comment
    assert "docker restart nova_compute" in answer.comment


def test_the_answer_says_whether_cortex_can_run_it(admin):
    _, h = admin
    row_id = _record()
    _ask(row_id, h, "Can Cortex run this automatically or do I do it by hand?")
    assert "click Execute" in _entries(row_id)[-1].comment

    hand = _record(proposal=_proposal(command=f"openstack server delete {UUID_A}"))
    _ask(hand, h, "Can Cortex run this automatically?")
    assert "cannot run this one for you" in _entries(hand)[-1].comment


def test_a_question_is_answered_once_even_if_the_task_runs_twice(admin):
    _, h = admin
    row_id = _record()
    _ask(row_id, h, "what is the risk?")
    question = next(e for e in _entries(row_id) if e.event == "info_requested")
    db = SessionLocal()
    try:
        assert approval.answer_info_request(db, proposal_row_id=row_id, question_entry_id=question.id) is None
    finally:
        db.close()
    assert _events(row_id).count("info_provided") == 1


def test_each_question_gets_its_own_answer_and_can_still_be_decided(admin):
    _, h = admin
    row_id = _record()
    _ask(row_id, h, "what is the risk?")
    _ask(row_id, h, "what are the alternatives?")
    assert _events(row_id) == ["proposed", "info_requested", "info_provided", "info_requested", "info_provided"]
    assert _decide(row_id, "approve", h, "answers were enough").status_code == 200
    db = SessionLocal()
    try:
        assert approval.verify_chain(db).ok
    finally:
        db.close()


def test_answering_does_not_need_the_llm_or_the_graph(admin, monkeypatch):
    """The answer is built from the snapshot alone: break everything else."""
    _, h = admin
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(execution, "simulate_proposal", lambda p: pytest.fail("the answer must not touch the graph"))
    row_id = _record()
    _ask(row_id, h, "what will it do?")
    assert _events(row_id)[-1] == "info_provided"


def test_a_viewer_asking_still_works_and_is_answered(viewer):
    _, vh = viewer
    row_id = _record()
    assert _ask(row_id, vh, "what is it for?").status_code == 200
    assert _events(row_id)[-1] == "info_provided"
