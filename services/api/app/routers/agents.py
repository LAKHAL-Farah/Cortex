import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..db import get_db
from ..agents.graph import app_graph
from ..agents.trace import new_trace_id

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/agents",
    tags=["agents"],
)


@router.post("/orchestrate", response_model=schemas.AgentOrchestrateResponse)
def orchestrate(payload: schemas.AgentOrchestrateQuery, db: Session = Depends(get_db)):
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
    """
    known_nodes: list[schemas.AgentKnownNode] = [
        {
            "hostname": n.hostname,
            "role": n.role.value if hasattr(n.role, "value") else n.role,
            "instance": f"{n.ip_address}:{n.exporter_port}",
        }
        for n in crud.list_nodes(db)
    ]

    trace_id = new_trace_id()
    started = time.monotonic()
    result = app_graph.invoke(
        {
            "user_query": payload.query,
            "known_nodes": known_nodes,
            "failures": [],
            "trace_id": trace_id,
            "trace_events": [],
        }
    )
    duration_ms = (time.monotonic() - started) * 1000

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

    return schemas.AgentOrchestrateResponse(
        answer=result["final_answer"],
        agent_used=result["target_agent"],
        raw_data=agent_result.get("raw_data"),
        confidence=agent_result.get("confidence"),
        degraded=degraded,
        trace_id=trace_id,
        critic_verdict=critic_verdict["status"] if critic_verdict else None,
    )


@router.get("/trace/{trace_id}", response_model=schemas.AgentTraceResponse)
def get_trace(trace_id: uuid.UUID, db: Session = Depends(get_db)):
    """"Why did it say that" as a lookup, not an investigation (v0.7's
    stated goal) -- the full step-by-step run for one orchestrate turn."""
    trace = crud.get_agent_trace(db, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="No trace found for that id.")

    return schemas.AgentTraceResponse(
        trace_id=str(trace.id),
        user_query=trace.user_query,
        intent=trace.intent,
        target_agent=trace.target_agent,
        critic_verdict_status=trace.critic_verdict_status,
        degraded=trace.degraded,
        steps=trace.steps,
        final_answer=trace.final_answer,
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
