"""Approve / reject / ask-for-more-info on a proposed fix, with every decision
written to an append-only audit trail (roadmap 4.3, adr-0014).

Where it sits: the remediation agent (4.1) proposes, the impact simulator
(4.2) says what it would touch, and *this* is the human decision point. It
changes no infrastructure and runs no command -- an approved proposal is just
a proposal whose status says a person signed off. Execution is a later item
and must read this status; it must not be inferred from anything else.

What the rules are (all enforced here, not in the router or the UI, so a
second caller cannot skip them):

- **Approve and reject need an approver role** (`APPROVER_ROLES`), and the
  fix must be within that role's reach (rbac.py, roadmap 4.8): an operator may
  decide low and medium risk fixes, only an admin a high-risk one. Asking for
  more information is open to any signed-in account: wanting to understand a
  proposal before someone decides on it must not itself be a privilege.
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

Roadmap 4.4 (adr-0015) extends the lifecycle past the decision -- see
services/remediation_execution.py, which owns `executing / executed /
execution_failed` and the explicit Execute click. This module only has to know
that once a proposal is past `approved`, it is closed to further decisions, and
it now also *answers* an `ask_more_info` (`answer_info_request`, from the
stored snapshot -- services/proposal_qa.py).

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

from .. import models, rbac

logger = logging.getLogger(__name__)

Decision = Literal["approve", "reject", "ask_more_info"]

# Who may approve or reject, in the broad sense: the roles that can validate
# *some* fix. Whether one particular fix is within an account's reach also
# depends on its effective risk -- operators stop at medium, admins have no
# ceiling -- and that rule lives in rbac.can_validate (roadmap 4.8), checked in
# `decide` below and in services/remediation_execution.py.
APPROVER_ROLES = rbac.VALIDATOR_ROLES

MAX_COMMENT_LENGTH = 2000
GENESIS_HASH = "0" * 64

# decision -> (audit event, resulting status)
_DECISIONS: dict[str, tuple[str, str]] = {
    "approve": ("approved", "approved"),
    "reject": ("rejected", "rejected"),
    "ask_more_info": ("info_requested", "info_requested"),
}
_FINAL_STATUSES = frozenset({"approved", "rejected"})
# Open for approve / reject / ask: nothing has been decided yet.
OPEN_STATUSES = frozenset({"proposed", "info_requested"})
EXECUTION_STATUSES = frozenset({"executing", "executed", "execution_failed"})
# Once the proposal has left OPEN_STATUSES no decision can change it. (A failed
# execution may be *executed* again -- that is not a decision.)
CLOSED_STATUSES = _FINAL_STATUSES | EXECUTION_STATUSES

_STATUS_WORDS = {"executing": "being executed", "executed": "executed", "execution_failed": "executed (and failed)"}

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
    # Flush so the next append in this same transaction (record_proposal writes
    # `diagnosed` then `proposed`) sees this entry as the chain's last hash.
    # Without it both would read the same predecessor and fork the chain.
    db.flush()
    return entry


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------

MAX_DIAGNOSIS_STEPS = 40


def diagnosis_summary(
    *,
    trace_id: Optional[uuid.UUID],
    user_query: Optional[str],
    intent: Optional[str],
    target_agent: Optional[str],
    critic_verdict: Optional[str],
    degraded: bool,
    steps: Optional[list],
) -> dict:
    """What goes into the chain for the *diagnose* stage (roadmap 4.8).

    Structural facts only: which agents ran and how it ended. Not the question
    text, not the findings -- the proposal's history is readable by every
    signed-in account, and a trace's own RBAC filtering (security specifics are
    admin-only) lives on the trace, not here. `query_digest` still binds the
    entry to the exact question asked, so the full trace
    (GET /api/v1/agents/trace/{trace_id}, or the incident timeline) can be
    shown to match it later.
    """
    return {
        "trace_id": str(trace_id) if trace_id else None,
        "query_digest": hashlib.sha256((user_query or "").encode("utf-8")).hexdigest() if user_query else None,
        "intent": intent,
        "target_agent": target_agent,
        "critic_verdict": critic_verdict,
        "degraded": bool(degraded),
        "steps": [
            {"node": st.get("node"), "status": st.get("status"), "duration_ms": st.get("duration_ms")}
            for st in (steps or [])[:MAX_DIAGNOSIS_STEPS]
            if isinstance(st, dict)
        ],
    }


def record_proposal(
    db: Session,
    *,
    trace_id: Optional[uuid.UUID],
    proposal: dict,
    requested_by: Optional[models.User],
    diagnosis: Optional[dict] = None,
) -> models.RemediationProposal:
    """Persist a proposal the agent just made and log that it was proposed.

    When the caller passes the `diagnosis` (see `diagnosis_summary`) the chain
    starts one step earlier, with a `diagnosed` entry, so the trail for one
    incident reads diagnose -> propose -> validate -> execute without a gap
    (roadmap 4.8). Without it the trail starts at `proposed`, as it did before.

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
        if diagnosis is not None:
            _append(
                db, row,
                event="diagnosed", from_status=None, to_status="proposed",
                actor=requested_by, comment=None,
                extra_details={"source": "agent_trace", "diagnosis": diagnosis},
            )
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
    db: Session,
    *,
    since: datetime,
    proposal_id: Optional[str] = None,
    trace_id: Optional[uuid.UUID] = None,
    event: Optional[str] = None,
    actor: Optional[str] = None,
    limit: int = 200,
) -> list[models.RemediationAuditEntry]:
    """Newest first, across all proposals (the admin's view of the trail).
    Optional filters: one proposal, one incident (`trace_id`), one event kind,
    one actor's username."""
    query = select(models.RemediationAuditEntry).where(models.RemediationAuditEntry.created_at >= since)
    if proposal_id:
        query = query.where(models.RemediationAuditEntry.proposal_id == proposal_id)
    if trace_id is not None:
        query = query.where(
            models.RemediationAuditEntry.proposal_row_id.in_(
                select(models.RemediationProposal.id).where(models.RemediationProposal.trace_id == trace_id)
            )
        )
    if event:
        query = query.where(models.RemediationAuditEntry.event == event)
    if actor:
        query = query.where(models.RemediationAuditEntry.actor_username == actor)
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
        raise DecisionError(403, rbac.denial_message(actor.role, None, "approve or reject a proposed fix"))

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

    if row.status in CLOSED_STATUSES:
        db.rollback()
        word = _STATUS_WORDS.get(row.status, row.status)
        raise DecisionError(409, f"This proposal was already {word}; a final decision cannot be changed.")

    details = step_details(row.proposal)
    if decision in ("approve", "reject") and not rbac.can_validate(actor.role, details["effective_risk"]):
        # An operator facing a high-risk fix. Same treatment as above: refused,
        # not recorded as a decision, and a warning for whoever reads the logs.
        db.rollback()
        logger.warning(
            "remediation: %s (%s) tried to %s %s-risk proposal %s above their role",
            actor.username, actor.role, decision, details["effective_risk"], proposal_row_id,
        )
        raise DecisionError(403, rbac.denial_message(actor.role, details["effective_risk"], "approve or reject a proposed fix"))

    event, new_status = _DECISIONS[decision]
    missing = _requires_reason(decision, details)
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


# --------------------------------------------------------------------
# Used by services/remediation_execution.py (same chain, same lock)
# --------------------------------------------------------------------

def append_entry(
    db: Session,
    row: models.RemediationProposal,
    *,
    event: str,
    from_status: Optional[str],
    to_status: str,
    actor: Optional[models.User],
    comment: Optional[str] = None,
    extra_details: Optional[dict] = None,
) -> models.RemediationAuditEntry:
    """`_append` for the other module that writes to the chain. Adds, does not
    commit; the caller owns the transaction and has locked the proposal row."""
    return _append(
        db, row, event=event, from_status=from_status, to_status=to_status,
        actor=actor, comment=comment, extra_details=extra_details,
    )


# --------------------------------------------------------------------
# Answering "ask for more information"
# --------------------------------------------------------------------

def unanswered_questions(entries: list[models.RemediationAuditEntry]) -> list[models.RemediationAuditEntry]:
    """The `info_requested` entries no `info_provided` entry points back at."""
    answered = {(e.details or {}).get("answers_entry_id") for e in entries if e.event == "info_provided"}
    return [e for e in entries if e.event == "info_requested" and e.id not in answered]


def answer_info_request(
    db: Session, *, proposal_row_id: uuid.UUID, question_entry_id: int
) -> Optional[models.RemediationAuditEntry]:
    """Write the answer to one logged question into the audit trail.

    The answer is built only from the proposal's stored snapshot
    (services/proposal_qa.py), so the trail records exactly what the person
    was told before deciding. It does not change the proposal's status -- the
    proposal stays open for a decision -- and it is idempotent per question,
    so a retried or doubled background task adds one answer, not two.
    Returns None when there is nothing to answer.
    """
    from .proposal_qa import answer_question  # local: keeps the import graph of this module small

    row = db.execute(
        select(models.RemediationProposal).where(models.RemediationProposal.id == proposal_row_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        db.rollback()
        return None
    entries = history(db, row.id)
    question = next((e for e in entries if e.id == question_entry_id and e.event == "info_requested"), None)
    if question is None or question not in unanswered_questions(entries):
        db.rollback()
        return None

    qa = answer_question(row.proposal, question.comment)
    entry = _append(
        db, row,
        event="info_provided", from_status=row.status, to_status=row.status,
        actor=None, comment=qa["answer"],
        extra_details={
            "answers_entry_id": question.id,
            "topics": qa["topics"],
            "question_understood": qa["matched"],
            "source": "proposal_snapshot",
        },
    )
    db.commit()
    db.refresh(entry)
    return entry
