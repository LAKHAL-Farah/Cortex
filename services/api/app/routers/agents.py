import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..auth import get_current_user
from ..db import get_db
from ..agents.graph import app_graph
from ..agents.trace import new_trace_id

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/agents",
    tags=["agents"],
)

_SECURITY_SUB_SIGNAL_KEYS = ("auth_signal", "sec_group_signal", "cve_signal", "ebpf_signal")
_RESTRICTED_NOTICE = (
    "This turn's finding involved the Security Agent (auth activity, security-group rules, "
    "known-vulnerable packages, or kernel-level alerts). Those specifics are restricted to admin "
    "accounts -- ask an admin to review this trace, or sign in with an admin account to see the "
    "full finding."
)


def _security_agent_involved(agent_used: str, raw_data: dict | None) -> bool:
    """True if the Security Agent contributed *any* finding this turn --
    as the direct answer (`agent_used == "security"`), as arbitration's
    chosen primary theory for a cross-agent incident
    (`raw_data["investigating_agent"]`), or merely as one of several
    corroborating/competing findings arbitration reported alongside
    another agent's primary theory (`cross_agent_findings`/
    `multi_node_findings`, see nodes/anomaly.py's `anomaly_arbitrate`).
    That last case matters just as much as the first two: the
    cross-agent narrative an LLM writes from *all* contributing findings
    (see anomaly.py's `_ARBITRATION_SYSTEM_PROMPT`, which explicitly asks
    it to name specifics from every agent that fired) can surface a CVE
    ID or an eBPF alert's output line in prose even when Security wasn't
    the winning theory -- so "involved at all", not just "was primary",
    is the right question for whether this response needs filtering.
    """
    if agent_used == "security":
        return True
    raw_data = raw_data or {}
    if raw_data.get("investigating_agent") == "security":
        return True
    for key in ("cross_agent_findings", "multi_node_findings"):
        if any(f.get("agent") == "security" for f in raw_data.get(key) or []):
            return True
    return False


def _redact_security_raw_data(raw_data: dict | None) -> dict | None:
    """Strips this turn's raw_data down to what's safe for a non-admin:
    booleans and hostnames survive (a viewer can still see "something was
    flagged on compute-02"), but the specific evidence -- which CVE,
    which security-group rule, which eBPF alert line, which auth-log
    entry -- does not, since (per nodes/security.py's module docstring)
    that's each individually exploitable, not just informative. Only
    Security's own sub-signals and Security's own entries in a
    cross-agent breakdown are touched; another agent's (Network's,
    Anomaly's) own findings in the same raw_data are left exactly as they
    were, since those aren't the sensitive part of this turn."""
    if raw_data is None:
        return None
    redacted = dict(raw_data)

    for key in _SECURITY_SUB_SIGNAL_KEYS:
        signal = redacted.get(key)
        if isinstance(signal, dict):
            redacted[key] = {"has_signal": signal.get("has_signal"), "degraded": signal.get("degraded"), "restricted": True}

    for key in ("cross_agent_findings", "multi_node_findings"):
        entries = redacted.get(key)
        if not entries:
            continue
        redacted[key] = [
            {"hostname": f.get("hostname"), "agent": f.get("agent"), "has_signal": f.get("has_signal"), "restricted": True}
            if f.get("agent") == "security" else f
            for f in entries
        ]

    return redacted


def _filter_security_response_for_role(answer: str, raw_data: dict | None, agent_used: str, role: str) -> tuple[str, dict | None]:
    """Applied once, right before the orchestrate response is built (and
    reused by GET /trace/{trace_id} for the same trace later -- otherwise
    a viewer could trivially bypass this by fetching the trace_id
    orchestrate handed them instead of reading the filtered response).

    Admins always get the full answer/raw_data -- see auth.py's
    User.role, "admin" | "viewer". A non-admin gets a generic redaction
    notice in place of the prose answer (the free-form arbitration/
    narration text can name specifics for any agent that contributed, not
    just the primary one, so partial prose-scrubbing isn't safe -- see
    `_security_agent_involved`'s own docstring) and a raw_data with only
    Security's own fields stripped down to booleans (`_redact_security_raw_data`).
    """
    if role == "admin" or not _security_agent_involved(agent_used, raw_data):
        return answer, raw_data
    return _RESTRICTED_NOTICE, _redact_security_raw_data(raw_data)


def _filter_security_response_for_role(answer: str, raw_data: dict | None, agent_used: str, role: str) -> tuple[str, dict | None]:
    """Applied once, right before the orchestrate response is built (and
    reused by GET /trace/{trace_id} for the same trace later -- otherwise
    a viewer could trivially bypass this by fetching the trace_id
    orchestrate handed them instead of reading the filtered response).

    Admins always get the full answer/raw_data -- see auth.py's
    User.role, "admin" | "viewer". A non-admin gets a generic redaction
    notice in place of the prose answer (the free-form arbitration/
    narration text can name specifics for any agent that contributed, not
    just the primary one, so partial prose-scrubbing isn't safe -- see
    `_security_agent_involved`'s own docstring) and a raw_data with only
    Security's own fields stripped down to booleans (`_redact_security_raw_data`).
    """
    if role == "admin" or not _security_agent_involved(agent_used, raw_data):
        return answer, raw_data
    return _RESTRICTED_NOTICE, _redact_security_raw_data(raw_data)


def _filter_security_steps_for_role(steps: list[dict], involved: bool, role: str) -> list[dict]:
    """Same rule as `_filter_security_response_for_role`, applied per-step
    to the trace timeline. Only two step *names* can ever carry Security
    specifics: "security" itself (the standalone leaf agent, see
    nodes/security.py) and "anomaly" (the cross-agent arbitration join
    node, see nodes/anomaly.py's `anomaly_arbitrate` -- its own narrative
    can name a CVE/eBPF alert from a *corroborating* Security finding even
    when Security wasn't the winning theory). Every other step name
    (router/monitoring/network/openstack_expert/critic/compose) is
    structurally incapable of repeating Security's own findings, so
    `involved` -- this turn's already-computed top-level answer/raw_data
    involvement flag, see `_security_agent_involved` -- is reused directly
    rather than re-deriving it per step."""
    if role == "admin" or not involved:
        return steps

    filtered = []
    for step in steps:
        if step.get("node") not in ("security", "anomaly"):
            filtered.append(step)
            continue
        new_detail = dict(step.get("detail") or {})
        new_detail["summary"] = _RESTRICTED_NOTICE
        new_detail.pop("chained_from", None)
        filtered.append({**step, "detail": new_detail})
    return filtered


@router.post("/orchestrate", response_model=schemas.AgentOrchestrateResponse)
def orchestrate(
    payload: schemas.AgentOrchestrateQuery,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Question in -> LangGraph (router -> ... -> critic -> compose) ->
    answer out. Mirrors GET /api/v1/dashboard's hostname->instance mapping
    (see dashboard.py) so the monitoring agent resolves a node the same
    way the rest of the API already does, instead of re-deriving it.

    v0.7 (adr-0009): mints `trace_id` here, before the graph runs, and
    persists the completed run (models.AgentTrace) here, after -- the
    graph itself only ever *accumulates* JSON-serializable state
    (trace_events, see agents/trace.py), it never touches this endpoint's
    DB session, same rule state.py already holds every node to for
    `known_nodes`. A trace is written even for a clarify/error turn --
    "the router asked a clarifying question" is exactly the kind of thing
    worth being able to look back at.

    v0.8: same DB-session-stays-outside-the-graph rule applies to session
    memory (agents/state.py's `session_memory`/`resolved_entities`) --
    loaded here before the graph runs, merged back in here after. Scoped
    by `payload.conversation_id` when the caller sends one (see
    schemas.AgentOrchestrateQuery), and looked up via
    crud.get_conversation (not a bare id lookup) so this can't read or
    write another account's conversation's memory just because its id was
    guessed/reused -- same ownership check GET /api/v1/conversations/{id}
    already applies. No conversation_id at all -- a direct/API caller with
    no conversation concept -- just runs stateless, exactly as every
    orchestrate call did before this existed.
    """
    known_nodes: list[schemas.AgentKnownNode] = [
        {
            "hostname": n.hostname,
            "role": n.role.value if hasattr(n.role, "value") else n.role,
            "instance": f"{n.ip_address}:{n.exporter_port}",
        }
        for n in crud.list_nodes(db)
    ]

    conversation = None
    if payload.conversation_id is not None:
        conversation = crud.get_conversation(db, current_user.id, payload.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="No conversation found for that id.")

    session_memory = crud.get_session_memory(db, conversation.id) if conversation else {}

    trace_id = new_trace_id()
    started = time.monotonic()
    result = app_graph.invoke(
        {
            "user_query": payload.query,
            "known_nodes": known_nodes,
            "failures": [],
            "trace_id": trace_id,
            "trace_events": [],
            "session_memory": session_memory,
            "resolved_entities": {},
            "agent_results": [],
        }
    )
    duration_ms = (time.monotonic() - started) * 1000

    if conversation is not None:
        crud.upsert_session_memory(db, conversation.id, result.get("resolved_entities") or {})

    agent_result = result.get("agent_result") or {}
    critic_verdict = result.get("critic_verdict")
    degraded = bool(result.get("failures")) or bool(
        critic_verdict and critic_verdict["status"] == "flagged"
    )

    crud.create_agent_trace(
        db,
        trace_id=uuid.UUID(trace_id),
        user_query=payload.query,
        intent=result.get("intent"),
        target_agent=result.get("target_agent"),
        critic_verdict_status=critic_verdict["status"] if critic_verdict else None,
        degraded=degraded,
        steps=result.get("trace_events") or [],
        final_answer=result["final_answer"],
        duration_ms=duration_ms,
    )

    # v0.9: RBAC output filtering -- applied here, to the *response*, never
    # upstream in the graph or to what gets persisted just above. The
    # persisted models.AgentTrace row always keeps the full, unfiltered
    # finding (an admin reviewing history later, including a viewer's own
    # past turns, should see the real thing) -- see GET /trace/{trace_id}
    # below, which applies this same filter keyed to *its own* caller's
    # role, not the original caller's.
    filtered_answer, filtered_raw_data = _filter_security_response_for_role(
        result["final_answer"], agent_result.get("raw_data"), result["target_agent"], current_user.role
    )
    security_involved = _security_agent_involved(result["target_agent"], agent_result.get("raw_data"))

    return schemas.AgentOrchestrateResponse(
        answer=filtered_answer,
        agent_used=result["target_agent"],
        raw_data=filtered_raw_data,
        confidence=agent_result.get("confidence"),
        degraded=degraded,
        trace_id=trace_id,
        critic_verdict=critic_verdict["status"] if critic_verdict else None,
        # v0.11: same list just persisted to models.AgentTrace above, handed
        # back inline so the caller that triggered this turn doesn't need a
        # second round-trip to GET /trace/{trace_id} just to show the real
        # router -> agent [-> chained agent] -> critic -> compose pipeline.
        # v0.9: filtered by the same RBAC rule as `answer`/`raw_data` above
        # when the Security Agent contributed to this turn -- a step's own
        # `detail.summary` (see agents/trace.py's `_safe_detail`) carries
        # the same kind of per-agent specifics the top-level answer does.
        steps=_filter_security_steps_for_role(result.get("trace_events") or [], security_involved, current_user.role),
    )


@router.get("/trace/{trace_id}", response_model=schemas.AgentTraceResponse)
def get_trace(
    trace_id: uuid.UUID,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """"Why did it say that" as a lookup, not an investigation (v0.7's
    stated goal) -- the full step-by-step run for one orchestrate turn.

    v0.9: now requires auth (it didn't before -- trace_id is a UUID
    handed back by /orchestrate, not a secret, but "not a secret" isn't
    the same as "safe to serve unfiltered to anyone who has it"), and
    applies the exact same RBAC filter POST /orchestrate does, keyed to
    *this* request's caller -- not the original caller who triggered the
    turn. That's deliberate: an admin looking back at a viewer's old
    security-flavored trace should see the real thing; a viewer looking
    back at their own should still get the redacted version, the same as
    if they'd asked it fresh right now.
    """
    trace = crud.get_agent_trace(db, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="No trace found for that id.")

    # The persisted row has no separate raw_data column (see
    # models.AgentTrace) -- so involvement is read off `steps` themselves:
    # the "security" node ran directly, or the "anomaly" (arbitrate) step
    # recorded Security among `contributing_agents` (see trace.py's
    # `_safe_detail`, added for exactly this exact/structured check).
    security_involved = trace.target_agent == "security" or any(
        step.get("node") == "security"
        or "security" in ((step.get("detail") or {}).get("contributing_agents") or [])
        for step in (trace.steps or [])
    )
    filtered_answer = trace.final_answer if current_user.role == "admin" or not security_involved else _RESTRICTED_NOTICE
    filtered_steps = _filter_security_steps_for_role(trace.steps or [], security_involved, current_user.role)

    return schemas.AgentTraceResponse(
        trace_id=str(trace.id),
        user_query=trace.user_query,
        intent=trace.intent,
        target_agent=trace.target_agent,
        critic_verdict_status=trace.critic_verdict_status,
        degraded=trace.degraded,
        steps=filtered_steps,
        final_answer=filtered_answer,
        duration_ms=trace.duration_ms,
        created_at=trace.created_at.isoformat(),
    )


@router.get("/stats", response_model=schemas.AgentStatsResponse)
def get_stats(hours: int = 24, db: Session = Depends(get_db)):
    """6.3's cost/latency rollup: invocations, tier (target_agent) split,
    average latency per agent, degraded/critic-flagged rate, over the last
    `hours` -- enough to decide when an agent needs its own dedicated
    infra vs. shared capacity, without standing up a separate dashboard
    service for v0.7.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return schemas.AgentStatsResponse(**crud.agent_trace_stats(db, since=since))
