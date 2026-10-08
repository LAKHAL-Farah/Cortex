"""Approve / reject / ask-for-more-info on a proposed fix, with every decision
written to an append-only audit trail (roadmap 4.3, adr-0014).

Where it sits: the remediation agent (4.1) proposes, the impact simulator
(4.2) says what it would touch, and *this* is the human decision point. It
changes no infrastructure and runs no command -- an approved proposal is just
a proposal whose status says a person signed off. Execution is a later item
and must read this status; it must not be inferred from anything else.

What the rules are (all enforced here, not in the router or the UI, so a
second caller cannot skip them):

- **Approve and reject need an approver role** (`APPROVER_ROLES`). Asking for
  more information is open to any signed-in account: wanting to understand a
  proposal before an admin decides on it must not itself be a privilege.
- **A proposal is decided once.** `approved` and `rejected` are final; a
  second click, a second tab, or a replayed request gets a conflict, never a
  second audit entry that would contradict the first. `info_requested` keeps
  the proposal open -- it can be asked again, then approved or rejected.
- **A reason is required where silence would be a gap in the record:** every
  rejection, every request for more info, and the approval of anything the
  simulation or the command rates as high risk (the proposal itself tells
  people to get a second pair of eyes on those).
- **The decision refers to a server-side snapshot**, never to a command the
  client sends. The audit entry copies the exact command, host, risk and
  simulation verdict out of that snapshot, plus a digest of the whole
  proposal, so "what did they approve" has one answer.
- **Status change and audit entry are one transaction.** There is no state
  where a proposal is approved without an entry saying so.

The audit trail is hash-chained (`entry_hash` = SHA-256 over the previous
entry's hash and this entry's canonical content, across the whole table in
`id` order). The table also has a database trigger rejecting UPDATE/DELETE
(see the migration); the chain is what notices tampering done around it.
Appends are serialised with a transaction-scoped advisory lock so two
simultaneous decisions cannot both extend the same predecessor.

Nothing in this module reads the Living Model, OpenStack or an LLM.
"""
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models

logger = logging.getLogger(__name__)

Decision = Literal["approve", "reject", "ask_more_info"]

# Who may approve or reject. The platform has two roles today (models.User);
# an "operator can approve low-risk only" tier belongs with the trust ladder
# (roadmap Phase 9), not here -- see adr-0014 on the open question in §13.
APPROVER_ROLES = frozenset({"admin"})

MAX_COMMENT_LENGTH = 2000
GENESIS_HASH = "0" * 64

# decision -> (audit event, resulting status)
_DECISIONS: dict[str, tuple[str, str]] = {
    "approve": ("approved", "approved"),
    "reject": ("rejected", "rejected"),
    "ask_more_info": ("info_requested", "info_requested"),
}
_FINAL_STATUSES = frozenset({"approved", "rejected"})

# Arbitrary constant key for pg_advisory_xact_lock; every writer of the audit
# chain takes the same one.
_CHAIN_LOCK_KEY = 0x43525458  # "CRTX"


class DecisionError(Exception):
    """A decision that must be refused. `status_code` is the HTTP status the
    router maps it to; `message` is safe to show the person."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# --------------------------------------------------------------------
# Hash chain (pure, unit-tested)
# --------------------------------------------------------------------

def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _utc_iso(moment: datetime) -> str:
    """One spelling of a timestamp for hashing, whichever timezone the driver
    hands it back in."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def proposal_digest(proposal: dict) -> str:
    """Fingerprint of the proposal exactly as it was shown."""
    return hashlib.sha256(_canonical(proposal).encode("utf-8")).hexdigest()


def compute_entry_hash(
    prev_hash: str,
    *,
    proposal_row_id: uuid.UUID,
    proposal_id: str,
    event: str,
    from_status: Optional[str],
    to_status: str,
    actor_user_id: Optional[uuid.UUID],
    actor_username: Optional[str],
    actor_role: Optional[str],
    comment: Optional[str],
    details: dict,
    created_at: datetime,
) -> str:
    content = {
        "proposal_row_id": str(proposal_row_id),
        "proposal_id": proposal_id,
        "event": event,
        "from_status": from_status,
        "to_status": to_status,
        "actor_user_id": str(actor_user_id) if actor_user_id else None,
        "actor_username": actor_username,
        "actor_role": actor_role,
        "comment": comment,
        "details": details,
        "created_at": _utc_iso(created_at),
    }
    return hashlib.sha256(f"{prev_hash}\n{_canonical(content)}".encode("utf-8")).hexdigest()


def _entry_hash_of(row: models.RemediationAuditEntry) -> str:
    return compute_entry_hash(
        row.prev_hash,
        proposal_row_id=row.proposal_row_id,
        proposal_id=row.proposal_id,
        event=row.event,
        from_status=row.from_status,
        to_status=row.to_status,
        actor_user_id=row.actor_user_id,
        actor_username=row.actor_username,
        actor_role=row.actor_role,
        comment=row.comment,
        details=row.details or {},
        created_at=row.created_at,
    )


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    entries_checked: int
    # `id` of the first entry that does not verify, and why; None when ok.
    broken_at: Optional[int] = None
    reason: Optional[str] = None


def verify_chain(db: Session) -> ChainReport:
    """Re-derive every hash from the stored content, in order. Stops at the
    first break -- everything after a break is untrustworthy anyway."""
    expected_prev = GENESIS_HASH
    checked = 0
    rows = db.execute(select(models.RemediationAuditEntry).order_by(models.RemediationAuditEntry.id)).scalars()
    for row in rows:
        if row.prev_hash != expected_prev:
            return ChainReport(False, checked, row.id, "previous-hash link does not match (an entry before it was removed or changed)")
        if _entry_hash_of(row) != row.entry_hash:
            return ChainReport(False, checked, row.id, "entry content does not match its recorded hash (the entry was altered)")
        expected_prev = row.entry_hash
        checked += 1
    return ChainReport(True, checked)


# --------------------------------------------------------------------
# Appending
# --------------------------------------------------------------------

def _lock_chain(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})


def _last_hash(db: Session) -> str:
    last = db.execute(
        select(models.RemediationAuditEntry.entry_hash).order_by(models.RemediationAuditEntry.id.desc()).limit(1)
    ).scalar_one_or_none()
    return last or GENESIS_HASH


def step_details(proposal: dict) -> dict:
    """What a person needs in order to know, later, exactly what was in front
    of whoever decided. Copied from the frozen snapshot, never from a request."""
    primary = proposal.get("primary") or {}
    sim = proposal.get("simulation") or primary.get("simulation") or {}
    return {
        "command": primary.get("command"),
        "host": proposal.get("host"),
        "symptom_id": proposal.get("symptom_id"),
        "base_risk": primary.get("risk"),
        "effective_risk": primary.get("effective_risk") or primary.get("risk"),
        "simulation_status": sim.get("status"),
        "simulation_verdict": sim.get("verdict"),
        "inputs_needed": list(proposal.get("inputs_needed") or []),
        "proposal_digest": proposal_digest(proposal),
    }


def _append(
    db: Session,
    row: models.RemediationProposal,
    *,
    event: str,
    from_status: Optional[str],
    to_status: str,
    actor: Optional[models.User],
    comment: Optional[str],
    extra_details: Optional[dict] = None,
) -> models.RemediationAuditEntry:
    """Adds (does not commit) one chained entry. Caller owns the transaction."""
    _lock_chain(db)
    details = {**step_details(row.proposal), **(extra_details or {})}
    created_at = datetime.now(timezone.utc)
    prev_hash = _last_hash(db)
    entry = models.RemediationAuditEntry(
        proposal_row_id=row.id,
        proposal_id=row.proposal_id,
        event=event,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor.id if actor else None,
        actor_username=actor.username if actor else None,
        actor_role=actor.role if actor else None,
        comment=comment,
        details=details,
        prev_hash=prev_hash,
        created_at=created_at,
        entry_hash=compute_entry_hash(
            prev_hash,
            proposal_row_id=row.id,
            proposal_id=row.proposal_id,
            event=event,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor.id if actor else None,
            actor_username=actor.username if actor else None,
            actor_role=actor.role if actor else None,
            comment=comment,
            details=details,
            created_at=created_at,
        ),
    )
    db.add(entry)
    return entry


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------

def record_proposal(
    db: Session,
    *,
    trace_id: Optional[uuid.UUID],
    proposal: dict,
    requested_by: Optional[models.User],
) -> models.RemediationProposal:
    """Persist a proposal the agent just made and log that it was proposed.

    Idempotent per (trace_id, proposal_id): recording the same proposal for
    the same turn twice returns the first row and adds no second entry.
    """
    proposal_id = proposal["proposal_id"]
    if trace_id is not None:
        existing = db.execute(
            select(models.RemediationProposal).where(
                models.RemediationProposal.trace_id == trace_id,
                models.RemediationProposal.proposal_id == proposal_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    row = models.RemediationProposal(
        id=uuid.uuid4(),
        trace_id=trace_id,
        proposal_id=proposal_id,
        status="proposed",
        proposal=proposal,
        requested_by_user_id=requested_by.id if requested_by else None,
    )
    db.add(row)
    try:
        db.flush()
        _append(
            db, row,
            event="proposed", from_status=None, to_status="proposed",
            actor=requested_by, comment=None,
            extra_details={"source": "remediation_agent"},
        )
        db.commit()
    except IntegrityError:
        # Lost a race with another recorder of the same (trace, proposal).
        db.rollback()
        if trace_id is not None:
            winner = db.execute(
                select(models.RemediationProposal).where(
                    models.RemediationProposal.trace_id == trace_id,
                    models.RemediationProposal.proposal_id == proposal_id,
                )
            ).scalar_one_or_none()
            if winner is not None:
                return winner
        raise
    db.refresh(row)
    return row


def get_proposal(db: Session, proposal_row_id: uuid.UUID) -> Optional[models.RemediationProposal]:
    return db.get(models.RemediationProposal, proposal_row_id)


def history(db: Session, proposal_row_id: uuid.UUID) -> list[models.RemediationAuditEntry]:
    return list(
        db.execute(
            select(models.RemediationAuditEntry)
            .where(models.RemediationAuditEntry.proposal_row_id == proposal_row_id)
            .order_by(models.RemediationAuditEntry.id)
        ).scalars()
    )


def list_audit(
    db: Session, *, since: datetime, proposal_id: Optional[str] = None, limit: int = 200
) -> list[models.RemediationAuditEntry]:
    """Newest first, across all proposals (the admin's view of the trail)."""
    query = select(models.RemediationAuditEntry).where(models.RemediationAuditEntry.created_at >= since)
    if proposal_id:
        query = query.where(models.RemediationAuditEntry.proposal_id == proposal_id)
    return list(db.execute(query.order_by(models.RemediationAuditEntry.id.desc()).limit(limit)).scalars())


def _requires_reason(decision: str, details: dict) -> Optional[str]:
    """The sentence to show when a decision needs a comment and has none."""
    if decision == "reject":
        return "Say why you are rejecting this fix, so the next person (and the audit trail) knows."
    if decision == "ask_more_info":
        return "Say what you need to know before this can be decided."
    if decision == "approve" and details.get("effective_risk") == "high":
        return "This fix is rated high risk -- note who reviewed the exact command, or why it is acceptable, before approving."
    return None


def decide(
    db: Session,
    *,
    proposal_row_id: uuid.UUID,
    decision: str,
    comment: Optional[str],
    actor: models.User,
) -> tuple[models.RemediationProposal, models.RemediationAuditEntry]:
    """Apply one decision and log it. Raises `DecisionError` for anything
    that must be refused; on success the proposal's new status and the audit
    entry are committed together."""
    if decision not in _DECISIONS:
        raise DecisionError(422, f"Unknown decision '{decision}'.")

    if decision in ("approve", "reject") and actor.role not in APPROVER_ROLES:
        # Deliberately not logged as a decision -- nothing was decided.
        logger.warning(
            "remediation: %s (%s) tried to %s proposal %s without an approver role",
            actor.username, actor.role, decision, proposal_row_id,
        )
        raise DecisionError(403, "Only an admin can approve or reject a proposed fix. You can still ask for more information.")

    cleaned = (comment or "").strip() or None
    if cleaned and len(cleaned) > MAX_COMMENT_LENGTH:
        raise DecisionError(422, f"Keep the comment under {MAX_COMMENT_LENGTH} characters.")

    # Row lock: two simultaneous decisions on the same proposal serialise here,
    # and the loser sees the winner's status below.
    row = db.execute(
        select(models.RemediationProposal).where(models.RemediationProposal.id == proposal_row_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise DecisionError(404, "No such proposal.")

    if row.status in _FINAL_STATUSES:
        db.rollback()
        raise DecisionError(409, f"This proposal was already {row.status}; a final decision cannot be changed.")

    event, new_status = _DECISIONS[decision]
    missing = _requires_reason(decision, step_details(row.proposal))
    if missing and not cleaned:
        db.rollback()
        raise DecisionError(422, missing)

    previous = row.status
    row.status = new_status
    entry = _append(db, row, event=event, from_status=previous, to_status=new_status, actor=actor, comment=cleaned)
    db.commit()
    db.refresh(row)
    db.refresh(entry)
    return row, entry
