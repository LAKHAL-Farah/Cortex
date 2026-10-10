"""Execute an approved fix -- only after an explicit click (roadmap 4.4,
adr-0015).

Confidence Ladder Level 0 means *nothing executes without approval*. This
module is where that sentence is enforced, in one place, so a second caller
(another route, a script, a future agent) cannot skip a rule by not going
through the UI:

1. **A role.** Only an `APPROVER_ROLES` account may execute (admin today).
2. **An approval.** The stored proposal must be `approved` (or
   `execution_failed`, for a deliberate retry). Never inferred from anything
   else: not from a comment, not from the agent's own confidence, not from the
   proposal being low risk. Level 0 has no exception for "safe" fixes.
3. **A separate, explicit click.** The request must carry `confirm: true` and
   the digest of the proposal the person was looking at. Approving records a
   decision; it never runs anything.
4. **What was approved is what runs.** The command comes from the stored
   snapshot, never from the request, and is turned into a typed, allow-listed
   plan (services/remediation_executor.py). Unresolved placeholders, compound
   commands, deletes and anything outside the allow-list are refused.
5. **Still true.** The approval must be recent (`APPROVAL_TTL`), and if the
   proposal was simulated, a fresh simulation on the Living Model must not
   show a bigger impact than the one that was approved. If the graph cannot be
   read, it does not run: "could not re-check" is not "unchanged".
6. **Once.** The status moves to `executing` under a row lock before anything
   runs, so a double click, a second tab or a replayed request gets a 409.

Every step lands in the same hash-chained audit trail as the decision:
`execution_started` (who clicked, the exact plan), then `executed` or
`execution_failed` (what the backend reported). The first is committed *before*
the action runs: if the process dies mid-way the trail still says it started,
and the stuck `executing` is reported as "outcome unknown" on the next click
rather than silently re-run (an interrupted reboot may well have happened).

The action itself runs after the HTTP response (`run_execution`, a background
task) because Ansible can take minutes; the UI polls the proposal.
"""
import copy
import hmac
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import remediation_approval as approval
from .impact_simulator import simulate_proposal
from .remediation_executor import (
    ANSIBLE_TIMEOUT_SECONDS,
    ExecutionPlan,
    NotExecutable,
    execution_mode,
    one_click_enabled,
    plan_for,
    run_plan,
)

logger = logging.getLogger(__name__)

# The ladder this deployment is on. Level 0 is the only level that exists:
# every state-changing action needs a person's approval *and* a person's click.
# Higher levels (auto-run low-risk fixes, ...) are Phase 9 and would be a
# change to *this* constant's meaning and to `start_execution`, reviewed as such.
CONFIDENCE_LEVEL = 0

# How long an approval stays good. The situation that justified the fix (a
# host struggling, a service down) is a snapshot; an hour-old yes is not a yes
# about *now*. Re-propose to get a fresh simulation and a fresh decision.
APPROVAL_TTL = timedelta(minutes=int(os.environ.get("CORTEX_REMEDIATION_APPROVAL_TTL_MINUTES", "60")))

# An `executing` row older than this has lost its worker (API restarted, ...).
EXECUTION_STALE_AFTER = timedelta(seconds=ANSIBLE_TIMEOUT_SECONDS + 120)

_VERDICT_RANK = {"safe": 0, "caution": 1, "disruptive": 2}
_EXECUTABLE_STATUSES = frozenset({"approved", "execution_failed"})

_STATUS_REFUSAL = {
    "proposed": "This fix has not been approved yet. Approve it first; approving does not run anything.",
    "info_requested": "This fix has not been approved yet; there is an open question on it. Approve it first.",
    "rejected": "This fix was rejected and cannot be executed.",
    "executed": "This fix was already executed. Ask for a new proposal if the problem is back.",
    "executing": "This fix is already running.",
}


def _refuse_status(status: str) -> Optional[str]:
    return None if status in _EXECUTABLE_STATUSES else _STATUS_REFUSAL.get(status, f"A proposal that is {status} cannot be executed.")


# --------------------------------------------------------------------
# What the screen shows (read-only, no side effects)
# --------------------------------------------------------------------

def execution_info(row: models.RemediationProposal) -> dict:
    """`{"available", "summary", "backend", "blocked_reason"}` for one proposal.
    `available` means: an admin could click Execute right now and it would be
    attempted. It is for display; `start_execution` re-checks everything."""
    details = approval.step_details(row.proposal)
    plan: Optional[ExecutionPlan] = None
    reason: Optional[str] = None
    try:
        plan = plan_for(details["command"] or "", details["host"])
    except NotExecutable as exc:
        reason = exc.reason

    if execution_mode() != "live":
        reason = "Execution is switched off on this deployment (CORTEX_REMEDIATION_EXECUTION=off). Run the command yourself."
    elif details["inputs_needed"]:
        reason = "A value still has to be filled in before this can run: " + ", ".join(details["inputs_needed"]) + "."
    elif reason is None and row.status in ("executed", "executing", "rejected"):
        reason = _refuse_status(row.status)
    # (A plan error found above stays as `reason`: showing it already while the
    # fix is awaiting its decision tells the approver Cortex cannot run it.)

    available = reason is None and plan is not None and row.status in _EXECUTABLE_STATUSES
    # One-click is offered while the fix is still awaiting its decision, so it
    # cannot depend on `available` (which needs the status to be `approved`).
    # It needs the same things Execute needs: a runnable plan, no blank to fill
    # in, execution switched on -- and the sandbox-only switch.
    one_click = (
        one_click_enabled()
        and plan is not None
        and execution_mode() == "live"
        and not details["inputs_needed"]
        and row.status in approval.OPEN_STATUSES
    )
    return {
        "available": available,
        "summary": plan.summary if plan else None,
        "backend": plan.backend if plan else None,
        "blocked_reason": reason,
        "one_click": one_click,
    }


# --------------------------------------------------------------------
# The click
# --------------------------------------------------------------------

def _approval_entry(entries: list[models.RemediationAuditEntry]) -> Optional[models.RemediationAuditEntry]:
    return next((e for e in reversed(entries) if e.event == "approved"), None)


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _resimulation_problem(snapshot: dict) -> Optional[str]:
    """Why the fix must not run because the Living Model now disagrees with
    what the approver saw; None when it still agrees (or was never simulated,
    which the approver saw too)."""
    old = snapshot.get("simulation") or (snapshot.get("primary") or {}).get("simulation") or {}
    if old.get("status") != "simulated":
        return None
    try:
        fresh = simulate_proposal(copy.deepcopy(snapshot))
    except Exception:  # noqa: BLE001
        logger.exception("remediation: re-simulation before execution failed")
        return "The expected impact could not be re-checked just now, so the fix was not run. Try again in a moment."
    new = fresh.get("simulation") or {}
    if new.get("status") != "simulated":
        return "The Living Model could not be read just now, so the expected impact could not be re-checked and the fix was not run. Try again in a moment."
    if _VERDICT_RANK[new["verdict"]] > _VERDICT_RANK[old["verdict"]]:
        return (
            f"Since this was approved the Living Model shows a bigger impact ({new['verdict']}, it was {old['verdict']}): "
            f"{new.get('headline', '')} Ask for a fresh proposal and decide again."
        ).strip()
    return None


def start_execution(
    db: Session,
    *,
    proposal_row_id: uuid.UUID,
    actor: models.User,
    confirmed_digest: str,
) -> tuple[models.RemediationProposal, models.RemediationAuditEntry, ExecutionPlan]:
    """Validate every rule above, mark the proposal `executing` and log
    `execution_started`, all committed together. Raises
    `approval.DecisionError` for anything refused; on success the caller must
    hand the returned plan to `run_execution`."""
    if actor.role not in approval.APPROVER_ROLES:
        logger.warning("remediation: %s (%s) tried to execute proposal %s without an approver role",
                       actor.username, actor.role, proposal_row_id)
        raise approval.DecisionError(403, "Only an admin can execute an approved fix.")
    if execution_mode() != "live":
        raise approval.DecisionError(503, "Execution is switched off on this deployment. Run the command yourself.")

    row = db.get(models.RemediationProposal, proposal_row_id)
    if row is None:
        raise approval.DecisionError(404, "No such proposal.")

    # Cheap refusals first, none of them needs the lock or the graph.
    if not hmac.compare_digest(approval.proposal_digest(row.proposal), confirmed_digest or ""):
        raise approval.DecisionError(409, "The proposal you confirmed is not the one on record. Reload and look at it again.")
    if row.status != "executing":
        if (refusal := _refuse_status(row.status)):
            raise approval.DecisionError(409, refusal)
    details = approval.step_details(row.proposal)
    if details["inputs_needed"]:
        raise approval.DecisionError(
            422, "A value still has to be filled in (" + ", ".join(details["inputs_needed"]) + "); Cortex will not run a command with a blank."
        )
    try:
        plan = plan_for(details["command"] or "", details["host"])
    except NotExecutable as exc:
        raise approval.DecisionError(422, exc.reason) from exc

    if row.status != "executing" and (problem := _resimulation_problem(row.proposal)):
        logger.info("remediation: refused to execute %s: %s", proposal_row_id, problem)
        raise approval.DecisionError(409, problem)

    # Authoritative checks, under the row lock.
    # populate_existing: the unlocked read above put this row in the session's
    # identity map; without it the locked SELECT would hand back that stale
    # copy and the lock would protect nothing.
    row = db.execute(
        select(models.RemediationProposal)
        .where(models.RemediationProposal.id == proposal_row_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    entries = approval.history(db, row.id)

    if row.status == "executing":
        started = next((e for e in reversed(entries) if e.event == "execution_started"), None)
        if started is not None and datetime.now(timezone.utc) - _utc(started.created_at) > EXECUTION_STALE_AFTER:
            approval.append_entry(
                db, row, event="execution_failed", from_status="executing", to_status="execution_failed", actor=None,
                comment="No result was ever recorded for this run (the API may have restarted while it ran). "
                        "The outcome is unknown: check the host before executing again.",
                extra_details={"interrupted": True, "started_entry_id": started.id},
            )
            row.status = "execution_failed"
            db.commit()
            raise approval.DecisionError(
                409, "The previous run never reported back, so its outcome is unknown. Check the host, then click Execute again if it is still needed."
            )
        db.rollback()
        raise approval.DecisionError(409, _STATUS_REFUSAL["executing"])
    if (refusal := _refuse_status(row.status)):
        db.rollback()
        raise approval.DecisionError(409, refusal)

    approved = _approval_entry(entries)
    if approved is None:  # status says approved but the trail does not: refuse, loudly
        db.rollback()
        logger.error("remediation: proposal %s is %s with no approval entry in the trail", row.id, row.status)
        raise approval.DecisionError(409, "No approval is on record for this proposal, so it will not run.")
    age = datetime.now(timezone.utc) - _utc(approved.created_at)
    if age > APPROVAL_TTL:
        db.rollback()
        minutes = int(APPROVAL_TTL.total_seconds() // 60)
        raise approval.DecisionError(
            409, f"This approval is older than {minutes} minutes, so it has expired. Ask for a fresh proposal and approve it again."
        )

    attempt = 1 + sum(1 for e in entries if e.event == "execution_started")
    previous = row.status
    row.status = "executing"
    entry = approval.append_entry(
        db, row, event="execution_started", from_status=previous, to_status="executing", actor=actor,
        comment=None,
        extra_details={
            "confidence_level": CONFIDENCE_LEVEL,
            "plan": plan.as_dict(),
            "attempt": attempt,
            "approved_entry_id": approved.id,
            "approved_by": approved.actor_username,
        },
    )
    db.commit()
    db.refresh(row)
    db.refresh(entry)
    return row, entry, plan


def finish_execution(
    db: Session,
    *,
    proposal_row_id: uuid.UUID,
    actor_id: Optional[uuid.UUID],
    plan: ExecutionPlan,
    result,
) -> Optional[models.RemediationAuditEntry]:
    """Record what the backend reported and move `executing` to `executed` /
    `execution_failed`. A proposal that is no longer `executing` (already
    closed out) is left alone."""
    row = db.execute(
        select(models.RemediationProposal)
        .where(models.RemediationProposal.id == proposal_row_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None or row.status != "executing":
        db.rollback()
        logger.warning("remediation: result for %s arrived but it is not executing; not recorded", proposal_row_id)
        return None
    actor = crud_get_user(db, actor_id)
    event = "executed" if result.ok else "execution_failed"
    row.status = event
    entry = approval.append_entry(
        db, row, event=event, from_status="executing", to_status=event, actor=actor,
        comment=None if result.ok else result.summary, extra_details=result.as_details(plan),
    )
    db.commit()
    db.refresh(entry)
    return entry


def crud_get_user(db: Session, user_id: Optional[uuid.UUID]) -> Optional[models.User]:
    return db.get(models.User, user_id) if user_id else None


def run_execution(
    proposal_row_id: uuid.UUID,
    actor_id: Optional[uuid.UUID],
    plan: ExecutionPlan,
    *,
    session_factory: Optional[Callable[[], Session]] = None,
    runner: Optional[Callable[[ExecutionPlan], object]] = None,
) -> None:
    """Background task: run the plan, then record the outcome. Uses its own
    session (the request's is closed by now). Never raises -- the trail, not an
    exception in a worker thread, is where the outcome goes; if even the
    recording fails, the proposal stays `executing` and `start_execution`'s
    stale check reports it as "outcome unknown"."""
    if session_factory is None:
        from ..db import SessionLocal as session_factory  # noqa: N813
    try:
        # Looked up now, not at definition time, so a test can replace run_plan.
        result = (runner or run_plan)(plan)
        db = session_factory()
        try:
            finish_execution(db, proposal_row_id=proposal_row_id, actor_id=actor_id, plan=plan, result=result)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("remediation: could not record the outcome of executing %s", proposal_row_id)
