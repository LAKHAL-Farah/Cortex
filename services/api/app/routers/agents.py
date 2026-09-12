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
from ..services import security_rbac

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/agents",
    tags=["agents"],
)

# v0.9's RBAC output filtering moved to services/security_rbac.py in
# Phase Sec-2, so routers/security.py's dashboard endpoints can call the
# exact same functions instead of keeping a second copy that could drift
# out of sync (see that module's own docstring). These names are kept as
# thin aliases -- not re-implementations -- purely so this router's own
# call sites below and any existing imports of this module's private
# names don't need to change.
_RESTRICTED_NOTICE = security_rbac.RESTRICTED_NOTICE
_security_agent_involved = security_rbac.security_agent_involved
_redact_security_raw_data = security_rbac.redact_security_raw_data
_filter_security_response_for_role = security_rbac.filter_security_response_for_role
_filter_security_steps_for_role = security_rbac.filter_security_steps_for_role


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
