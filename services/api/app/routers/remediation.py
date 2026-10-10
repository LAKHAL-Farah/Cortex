"""Roadmap 4.3 -- approve / reject / ask for more information on a proposed
fix, every decision logged (adr-0014); roadmap 4.4 -- an approved fix runs
only after an explicit Execute click (adr-0015).

The proposals themselves are created by POST /api/v1/agents/orchestrate (the
remediation agent's `fix_proposal` is recorded there and the turn's response
carries the handle -- `fix_proposal.approval.id` -- used below). This router
only reads them and records decisions on them; it never builds a proposal.
Execution is the one route that changes infrastructure, `POST
.../execute`, and every rule it enforces lives in
services/remediation_execution.py (approved first, admin only, a separate
click that names the proposal it saw, recent approval, impact not worse than
approved) -- see that module for why none of it is here.

Access: reading one proposal's status and history needs any signed-in account
(the proposal is already on that account's screen). Deciding is enforced in
services/remediation_approval.py, not here, so it cannot be bypassed by a
second caller. The global audit trail and its integrity check are
admin-only, like the security audit log.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user, require_admin
from ..db import get_db
from ..services import remediation_approval as approval
from ..services import remediation_execution as execution

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/remediation", tags=["remediation"])


def _proposal_out(
    db: Session, row: models.RemediationProposal, user: models.User
) -> schemas.RemediationProposalOut:
    details = approval.step_details(row.proposal)
    entries = approval.history(db, row.id)
    info = execution.execution_info(row)
    return schemas.RemediationProposalOut(
        id=row.id,
        proposal_id=row.proposal_id,
        trace_id=row.trace_id,
        status=row.status,
        command=details["command"],
        host=details["host"],
        effective_risk=details["effective_risk"],
        simulation_verdict=details["simulation_verdict"],
        inputs_needed=details["inputs_needed"],
        created_at=row.created_at,
        updated_at=row.updated_at,
        can_decide=user.role in approval.APPROVER_ROLES and row.status in approval.OPEN_STATUSES,
        proposal_digest=details["proposal_digest"],
        can_execute=info["available"] and user.role in approval.APPROVER_ROLES,
        execution=schemas.RemediationExecutionInfo(**info),
        answer_pending=bool(approval.unanswered_questions(entries)),
        history=[schemas.RemediationAuditEntryOut.model_validate(e) for e in entries],
    )


@router.get("/proposals/{proposal_row_id}", response_model=schemas.RemediationProposalOut)
def get_proposal(
    proposal_row_id: uuid.UUID,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = approval.get_proposal(db, proposal_row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such proposal.")
    return _proposal_out(db, row, current_user)


@router.post("/proposals/{proposal_row_id}/decision", response_model=schemas.RemediationDecisionResponse)
def decide(
    proposal_row_id: uuid.UUID,
    payload: schemas.RemediationDecisionRequest,
    background: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        row, entry = approval.decide(
            db,
            proposal_row_id=proposal_row_id,
            decision=payload.decision,
            comment=payload.comment,
            actor=current_user,
        )
    except approval.DecisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if entry.event == "info_requested":
        # The question is committed and logged; answering it is a second,
        # separate entry, written after the response (adr-0015 §6).
        background.add_task(_answer_question, row.id, entry.id)
    return schemas.RemediationDecisionResponse(
        proposal=_proposal_out(db, row, current_user),
        entry=schemas.RemediationAuditEntryOut.model_validate(entry),
    )


def _answer_question(proposal_row_id: uuid.UUID, question_entry_id: int) -> None:
    """Background task: its own session, never raises (the question stays
    logged and the UI keeps offering to ask again)."""
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        approval.answer_info_request(db, proposal_row_id=proposal_row_id, question_entry_id=question_entry_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("remediation: could not answer question %s on %s", question_entry_id, proposal_row_id)
    finally:
        db.close()


@router.post(
    "/proposals/{proposal_row_id}/execute",
    response_model=schemas.RemediationDecisionResponse,
    status_code=202,
)
def execute(
    proposal_row_id: uuid.UUID,
    payload: schemas.RemediationExecuteRequest,
    background: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The explicit click (roadmap 4.4). 202: the proposal is now `executing`
    and the action runs after this response; poll GET /proposals/{id} for
    `executed` / `execution_failed`. All the rules are in
    services/remediation_execution.py -- this only maps their refusals to HTTP
    and starts the background run."""
    try:
        row, entry, plan = execution.start_execution(
            db, proposal_row_id=proposal_row_id, actor=current_user, confirmed_digest=payload.proposal_digest
        )
    except approval.DecisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    background.add_task(execution.run_execution, row.id, current_user.id, plan)
    return schemas.RemediationDecisionResponse(
        proposal=_proposal_out(db, row, current_user),
        entry=schemas.RemediationAuditEntryOut.model_validate(entry),
    )


@router.get("/audit", response_model=schemas.RemediationAuditLogResponse, dependencies=[Depends(require_admin)])
def get_audit_log(
    hours: int = 24 * 7,
    proposal_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Every proposal / decision event over the last `hours`, newest first."""
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * 365)))
    rows = approval.list_audit(db, since=since, proposal_id=proposal_id)
    return schemas.RemediationAuditLogResponse(
        since=since.isoformat(),
        entries=[schemas.RemediationAuditEntryOut.model_validate(r) for r in rows],
    )


@router.get("/audit/verify", response_model=schemas.RemediationChainReport, dependencies=[Depends(require_admin)])
def verify_audit_chain(db: Session = Depends(get_db)):
    """Re-derives the whole audit chain and reports the first break, if any."""
    report = approval.verify_chain(db)
    return schemas.RemediationChainReport(
        ok=report.ok, entries_checked=report.entries_checked, broken_at=report.broken_at, reason=report.reason
    )
