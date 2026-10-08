"""Roadmap 4.3 -- approve / reject / ask for more information on a proposed
fix, every decision logged (adr-0014).

The proposals themselves are created by POST /api/v1/agents/orchestrate (the
remediation agent's `fix_proposal` is recorded there and the turn's response
carries the handle -- `fix_proposal.approval.id` -- used below). This router
only reads them and records decisions on them; it never builds a proposal
and never executes anything.

Access: reading one proposal's status and history needs any signed-in account
(the proposal is already on that account's screen). Deciding is enforced in
services/remediation_approval.py, not here, so it cannot be bypassed by a
second caller. The global audit trail and its integrity check are
admin-only, like the security audit log.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user, require_admin
from ..db import get_db
from ..services import remediation_approval as approval

router = APIRouter(prefix="/api/v1/remediation", tags=["remediation"])


def _proposal_out(
    db: Session, row: models.RemediationProposal, user: models.User
) -> schemas.RemediationProposalOut:
    details = approval.step_details(row.proposal)
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
        can_decide=user.role in approval.APPROVER_ROLES and row.status not in ("approved", "rejected"),
        history=[schemas.RemediationAuditEntryOut.model_validate(e) for e in approval.history(db, row.id)],
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
