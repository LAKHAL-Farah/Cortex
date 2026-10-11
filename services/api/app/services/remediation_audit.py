"""One incident, start to finish (roadmap 4.8, adr-0017).

The hash-chained trail in `remediation_audit_log` (services/remediation_approval.py)
already records every step of a fix's life. What an auditor asks, though, is
rarely "show me entry 4812" -- it is "show me everything that happened for this
incident". This module answers that with one call.

An **incident** is one agent run, identified by its `trace_id` (the id
POST /api/v1/agents/orchestrate returns and every proposal links back to). For
it, `incident_timeline` assembles, in order:

1. **diagnose** -- the agent run itself (`agent_traces`) and the chained
   `diagnosed` entry binding the proposal to it;
2. **propose** -- each fix proposed from that run;
3. **validate** -- who approved / rejected / asked what, with what comment;
4. **execute** -- who clicked Execute, the exact plan, and the outcome;

plus the **alerts** that were open on the affected host while this happened
(correlated by host and time, and labelled as such -- they are context, not
part of the tamper-evident chain) with where each alert was routed, and the
result of re-verifying the whole hash chain.

Read-only. Nothing here writes to the trail.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import alert_routing
from . import remediation_approval as approval

# How far before the diagnosis an alert may have opened and still be shown as
# the likely trigger. The agent is usually asked about an alert minutes old, but
# a person may investigate a slow burn started earlier; an hour keeps the list
# short without hiding the obvious cause.
ALERT_LOOKBACK = timedelta(hours=1)

_STAGE_EVENTS = {
    "diagnose": ("diagnosed",),
    "propose": ("proposed",),
    "validate": ("approved", "rejected"),
    "execute": ("executed", "execution_failed"),
}


def _utc(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _stages(entries: list[models.RemediationAuditEntry], trace: Optional[models.AgentTrace]) -> list[dict]:
    """The four stages of the chain and whether/when/by whom each happened.

    `diagnose` counts as done when the trace exists even if the proposal
    predates the `diagnosed` entry (older proposals, or a caller that did not
    pass the diagnosis) -- the agent run happened either way; the `chained`
    flag says whether the *chain* vouches for it."""
    out = []
    for stage, events in _STAGE_EVENTS.items():
        hit = next((e for e in reversed(entries) if e.event in events), None)
        if stage == "diagnose":
            out.append({
                "stage": stage,
                "done": hit is not None or trace is not None,
                "chained": hit is not None,
                "at": _utc(hit.created_at) if hit else (_utc(trace.created_at) if trace else None),
                "by": hit.actor_username if hit else None,
                "detail": (hit.details or {}).get("diagnosis", {}).get("target_agent") if hit else (trace.target_agent if trace else None),
            })
            continue
        out.append({
            "stage": stage,
            "done": hit is not None,
            "chained": hit is not None,
            "at": _utc(hit.created_at) if hit else None,
            "by": hit.actor_username if hit else None,
            "detail": hit.event if hit else None,
        })
    return out


def _outcome(proposals: list[models.RemediationProposal]) -> str:
    """One word for where the incident ended up (the furthest any proposal got)."""
    order = ["executed", "executing", "execution_failed", "approved", "rejected", "info_requested", "proposed"]
    statuses = {p.status for p in proposals}
    for candidate in order:
        if candidate in statuses:
            return candidate
    return "no_proposal"


def _alerts_for(
    db: Session, hosts: set[str], trace: Optional[models.AgentTrace], entries: list[models.RemediationAuditEntry]
) -> list[dict]:
    if not hosts or not entries:
        return []
    start = _utc(trace.created_at if trace else entries[0].created_at) - ALERT_LOOKBACK
    end = _utc(entries[-1].created_at)
    # AnomalyEvent timestamps are naive UTC.
    start_naive, end_naive = start.replace(tzinfo=None), end.replace(tzinfo=None)
    events = list(db.execute(
        select(models.AnomalyEvent)
        .where(
            models.AnomalyEvent.hostname.in_(hosts),
            models.AnomalyEvent.started_at <= end_naive,
            (models.AnomalyEvent.resolved_at.is_(None)) | (models.AnomalyEvent.resolved_at >= start_naive),
        )
        .order_by(models.AnomalyEvent.started_at)
        .limit(25)
    ).scalars())
    routed = alert_routing.notifications_for(db, [e.id for e in events])
    return [
        {
            "id": e.id,
            "hostname": e.hostname,
            "metric_name": e.metric_name,
            "severity": e.severity,
            "started_at": _utc(e.started_at),
            "resolved_at": _utc(e.resolved_at),
            "acknowledged_at": _utc(e.acknowledged_at),
            "acknowledged_by": e.acknowledged_by,
            "routing": alert_routing.routing_summary(routed.get(e.id)),
        }
        for e in events
    ]


def incident_timeline(db: Session, trace_id: uuid.UUID) -> Optional[dict]:
    """Everything recorded for one incident, or None if nothing is."""
    trace = db.get(models.AgentTrace, trace_id)
    proposals = list(db.execute(
        select(models.RemediationProposal)
        .where(models.RemediationProposal.trace_id == trace_id)
        .order_by(models.RemediationProposal.created_at)
    ).scalars())
    if trace is None and not proposals:
        return None

    entries: list[models.RemediationAuditEntry] = []
    if proposals:
        entries = list(db.execute(
            select(models.RemediationAuditEntry)
            .where(models.RemediationAuditEntry.proposal_row_id.in_([p.id for p in proposals]))
            .order_by(models.RemediationAuditEntry.id)
        ).scalars())

    hosts = {d["host"] for d in (approval.step_details(p.proposal) for p in proposals) if d["host"]}
    report = approval.verify_chain(db)
    stages = _stages(entries, trace)

    return {
        "trace_id": trace_id,
        "outcome": _outcome(proposals),
        "complete": all(s["done"] for s in stages),
        "stages": stages,
        "trace": None if trace is None else {
            "trace_id": str(trace.id),
            "created_at": _utc(trace.created_at),
            "user_query": trace.user_query,
            "intent": trace.intent,
            "target_agent": trace.target_agent,
            "critic_verdict_status": trace.critic_verdict_status,
            "degraded": trace.degraded,
            "requested_by_role": trace.user_role,
            "steps": [
                {"node": st.get("node"), "status": st.get("status"), "duration_ms": st.get("duration_ms"),
                 "timestamp": st.get("timestamp")}
                for st in (trace.steps or []) if isinstance(st, dict)
            ],
        },
        "proposals": [
            {
                "id": p.id,
                "proposal_id": p.proposal_id,
                "status": p.status,
                "command": d["command"],
                "host": d["host"],
                "effective_risk": d["effective_risk"],
            }
            for p in proposals
            for d in (approval.step_details(p.proposal),)
        ],
        "entries": entries,
        "alerts": _alerts_for(db, hosts, trace, entries),
        "chain": report,
    }


def list_incidents(db: Session, *, since: datetime, limit: int = 100) -> list[dict]:
    """Recent incidents (one per agent run that produced a proposal), newest
    first, so an auditor can pick one to open with `incident_timeline`."""
    rows = list(db.execute(
        select(models.RemediationProposal)
        .where(models.RemediationProposal.trace_id.is_not(None), models.RemediationProposal.created_at >= since)
        .order_by(models.RemediationProposal.created_at.desc())
    ).scalars())
    grouped: dict[uuid.UUID, list[models.RemediationProposal]] = {}
    for row in rows:
        grouped.setdefault(row.trace_id, []).append(row)
    out = []
    for trace_id, props in list(grouped.items())[:limit]:
        details = [approval.step_details(p.proposal) for p in props]
        out.append({
            "trace_id": trace_id,
            "hosts": sorted({d["host"] for d in details if d["host"]}),
            "proposals": len(props),
            "outcome": _outcome(props),
            "highest_risk": _highest_risk([d["effective_risk"] for d in details]),
            "first_at": _utc(min(p.created_at for p in props)),
            "last_at": _utc(max(p.updated_at or p.created_at for p in props)),
        })
    return out


def _highest_risk(risks: list[Optional[str]]) -> Optional[str]:
    rank = {"low": 0, "medium": 1, "high": 2}
    known = [r for r in risks if r in rank]
    if len(known) != len(risks):          # an unknown risk counts as the worst
        return "high" if risks else None
    return max(known, key=rank.__getitem__) if known else None
