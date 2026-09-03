import uuid
from datetime import datetime
from sqlalchemy import case, select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from . import models, schemas
from .services import llm_client


class DuplicateNodeError(Exception):
    pass


def list_nodes(db: Session) -> list[models.Node]:
    return db.scalars(select(models.Node).order_by(models.Node.hostname)).all()


def get_node(db: Session, node_id: uuid.UUID) -> models.Node | None:
    return db.get(models.Node, node_id)


def get_node_by_ip(db: Session, ip_address: str) -> models.Node | None:
    return db.scalar(select(models.Node).where(models.Node.ip_address == ip_address))

def get_node_by_hostname(db: Session, hostname: str) -> models.Node | None:
    return db.scalar(select(models.Node).where(models.Node.hostname == hostname))


def list_open_anomaly_flags(db: Session, hostname: str) -> list[models.AnomalyFlag]:
    """Currently-open (non-"normal") AnomalyFlag rows for one host.

    Used by the anomaly agent's metric-check sub-step (app/agents/nodes/
    anomaly.py) to read the same already-scored signal GET /api/v1/anomalies
    surfaces, instead of re-deriving a z-score from scratch inside the
    graph node.
    """
    return (
        db.query(models.AnomalyFlag)
        .filter(models.AnomalyFlag.hostname == hostname, models.AnomalyFlag.severity != "normal")
        .all()
    )


def list_all_open_anomaly_flag_hostnames(db: Session) -> list[str]:
    """Distinct hostnames with at least one currently-open AnomalyFlag,
    worst-severity-first -- the "Living Model" scope for the anomaly
    agent's v0.8 dynamic fan-out (agents/nodes/anomaly.py's
    anomaly_dispatch): when a question doesn't name one specific node
    ("is anything wrong right now"), this is what replaces a fixed/
    hardcoded node list with whatever the topology is *actually* flagging
    at the moment the question is asked.
    """
    severity_order = case(
        (models.AnomalyFlag.severity == "critical", 0),
        (models.AnomalyFlag.severity == "high", 1),
        (models.AnomalyFlag.severity == "medium", 2),
        else_=3,
    )
    rows = (
        db.query(models.AnomalyFlag.hostname, func.min(severity_order).label("rank"))
        .filter(models.AnomalyFlag.severity != "normal")
        .group_by(models.AnomalyFlag.hostname)
        .order_by(func.min(severity_order))
        .all()
    )
    return [row.hostname for row in rows]


def create_node(db: Session, payload: schemas.NodeCreate) -> models.Node:
    node = models.Node(**payload.model_dump())
    db.add(node)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateNodeError("hostname or ip_address already registered") from exc
    db.refresh(node)
    return node


def update_node(db: Session, node: models.Node, payload: schemas.NodeUpdate) -> models.Node:
    for field, value in payload.model_dump().items():
        setattr(node, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateNodeError("hostname or ip_address already registered") from exc
    db.refresh(node)
    return node


def delete_node(db: Session, node: models.Node) -> None:
    db.delete(node)
    db.commit()

def set_node_exporter_installed(db: Session, node: models.Node, installed: bool) -> models.Node:
    node.node_exporter_installed = installed
    db.commit()
    db.refresh(node)
    return node


def record_topology_sync_run(
    db: Session,
    *,
    sync_type: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    summary: dict | None = None,
    error: str | None = None,
) -> models.TopologySyncRun:
    """Appends one row to the sync-run metadata table (see
    models.TopologySyncRun). Called from main.py's periodic-task wrappers
    after every topology_sync.sync_topology()/
    prometheus_health.sync_prometheus_health() pass -- success or failure --
    so GET /api/v1/topology/health has real run history to answer from.
    """
    run = models.TopologySyncRun(
        sync_type=sync_type,
        status=status,
        summary=summary,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_latest_topology_sync_run(db: Session, sync_type: str) -> models.TopologySyncRun | None:
    return db.scalar(
        select(models.TopologySyncRun)
        .where(models.TopologySyncRun.sync_type == sync_type)
        .order_by(models.TopologySyncRun.finished_at.desc())
        .limit(1)
    )


def list_recent_topology_sync_runs(db: Session, sync_type: str, limit: int = 5) -> list[models.TopologySyncRun]:
    return db.scalars(
        select(models.TopologySyncRun)
        .where(models.TopologySyncRun.sync_type == sync_type)
        .order_by(models.TopologySyncRun.finished_at.desc())
        .limit(limit)
    ).all()


# --------------------------------------------------------------------------
# Copilot conversation history -- server-side counterpart to
# lib/copilotHistory.ts's fetch wrapper. Every function below is scoped by
# user_id (the logged-in account, see app.auth.get_current_user) so one
# account's history is never visible to another, and following the account
# means it shows up the same way from any device that account logs into.
# --------------------------------------------------------------------------

def list_conversations(db: Session, user_id: uuid.UUID) -> list[models.Conversation]:
    return db.scalars(
        select(models.Conversation)
        .where(models.Conversation.user_id == user_id)
        .order_by(models.Conversation.updated_at.desc())
    ).all()


def get_conversation(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID) -> models.Conversation | None:
    return db.scalar(
        select(models.Conversation)
        .where(models.Conversation.id == conversation_id, models.Conversation.user_id == user_id)
    )


def get_session_memory(db: Session, conversation_id: uuid.UUID) -> dict:
    """v0.8: the compact `resolved_entities` record for one conversation
    (see models.AgentSessionMemory), or `{}` for a conversation that
    hasn't resolved anything yet -- routers/agents.py loads this before
    invoking the graph and passes it in as CortexState's `session_memory`.
    """
    row = db.scalar(
        select(models.AgentSessionMemory).where(models.AgentSessionMemory.conversation_id == conversation_id)
    )
    return row.resolved_entities if row else {}


def upsert_session_memory(db: Session, conversation_id: uuid.UUID, resolved_entities: dict) -> None:
    """Merges `resolved_entities` (this turn's `state["resolved_entities"]`,
    e.g. {"last_node": ..., "last_agent": "monitoring"}) onto whatever this
    conversation already had on record -- a shallow merge, not a replace,
    so a turn that only resolved a node (e.g. rag_agent, which doesn't
    touch node/metric memory at all) doesn't blow away an unrelated
    last_metric a previous turn already set. An empty `resolved_entities`
    (a turn that resolved nothing new -- a clarify turn, or an error) is a
    no-op: nothing to merge, and nothing worth writing a row for.
    """
    if not resolved_entities:
        return

    row = db.scalar(
        select(models.AgentSessionMemory).where(models.AgentSessionMemory.conversation_id == conversation_id)
    )
    if row is None:
        db.add(models.AgentSessionMemory(conversation_id=conversation_id, resolved_entities=resolved_entities))
    else:
        row.resolved_entities = {**row.resolved_entities, **resolved_entities}
    db.commit()


def create_conversation(
    db: Session, user_id: uuid.UUID, payload: schemas.ConversationCreate
) -> models.Conversation:
    conversation = models.Conversation(user_id=user_id, **payload.model_dump())
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def replace_conversation(
    db: Session, conversation: models.Conversation, payload: schemas.ConversationUpdate
) -> models.Conversation:
    """Overwrites a conversation's title/category and its entire message
    list in one call. Messages are deleted and reinserted rather than
    diffed against the existing set -- the client always sends its full,
    current transcript (see schemas.ConversationUpdate's docstring), so a
    diff would just be more code to reach the same end state.
    """
    conversation.title = payload.title
    conversation.category = payload.category

    db.query(models.ConversationMessage).filter(
        models.ConversationMessage.conversation_id == conversation.id
    ).delete()

    for position, message in enumerate(payload.messages):
        db.add(
            models.ConversationMessage(
                conversation_id=conversation.id,
                role=message.role.value,
                content=message.content,
                sources=[s.model_dump() for s in message.sources] if message.sources else None,
                errored=message.errored,
                agent_used=message.agent_used,
                raw_data=message.raw_data,
                position=position,
            )
        )

    db.commit()
    db.refresh(conversation)
    return conversation


def delete_conversation(db: Session, conversation: models.Conversation) -> None:
    db.delete(conversation)
    db.commit()


def list_conversation_messages(db: Session, conversation_id: uuid.UUID) -> list[models.ConversationMessage]:
    return db.scalars(
        select(models.ConversationMessage)
        .where(models.ConversationMessage.conversation_id == conversation_id)
        .order_by(models.ConversationMessage.position.asc())
    ).all()


# --------------------------------------------------------------------------
# Users (app/auth.py, routers/auth.py)

class DuplicateUserError(Exception):
    pass


def get_user(db: Session, user_id) -> models.User | None:
    return db.get(models.User, user_id)


def get_user_by_username(db: Session, username: str) -> models.User | None:
    return db.scalar(select(models.User).where(models.User.username == username))


def list_users(db: Session) -> list[models.User]:
    return db.scalars(select(models.User).order_by(models.User.username)).all()


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(models.User)) or 0


def create_user(db: Session, *, username: str, password_hash: str, role: str,
                 must_change_password: bool = False) -> models.User:
    user = models.User(
        username=username,
        password_hash=password_hash,
        role=role,
        must_change_password=must_change_password,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateUserError(f"username '{username}' already taken") from exc
    db.refresh(user)
    return user


# --------------------------------------------------------------------------
# Agent orchestrator tracing (v0.7, adr-0009) -- one row per orchestrate
# turn (models.AgentTrace), plus the rollups GET /api/v1/agents/stats needs.
# --------------------------------------------------------------------------

def create_agent_trace(
    db: Session,
    *,
    trace_id: uuid.UUID,
    user_query: str,
    intent: str | None,
    target_agent: str | None,
    critic_verdict_status: str | None,
    degraded: bool,
    steps: list,
    final_answer: str,
    duration_ms: float,
) -> models.AgentTrace:
    trace = models.AgentTrace(
        id=trace_id,
        user_query=user_query,
        intent=intent,
        target_agent=target_agent,
        critic_verdict_status=critic_verdict_status,
        degraded=degraded,
        steps=steps,
        final_answer=final_answer,
        duration_ms=duration_ms,
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)
    return trace


def get_agent_trace(db: Session, trace_id: uuid.UUID) -> models.AgentTrace | None:
    return db.get(models.AgentTrace, trace_id)


def agent_trace_stats(db: Session, *, since: datetime) -> dict:
    """The 6.3 "cost/latency dashboard" rollup: invocations, tier split
    (target_agent), average latency, and failure/flag rates since a given
    cutoff. Deliberately a handful of aggregate queries rather than
    pulling every row back and reducing in Python -- this is meant to
    answer "do we need dedicated infra for agent X" from a glance, not to
    replace per-trace inspection (that's GET /agents/trace/{id}).

    v0.8 adds `model_tier` to each `by_agent` row and a top-level
    `router_tier` -- both from services/llm_client.ROUTER_TIER/AGENT_TIERS
    (a static per-call-site assignment, not derived from the trace data
    itself), so this same dashboard answers "is the fast tier actually
    carrying the high-volume nodes" alongside the existing latency
    numbers, without needing to scan every trace's `steps` JSON for it.
    """
    base = select(models.AgentTrace).where(models.AgentTrace.created_at >= since)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    by_agent_rows = db.execute(
        select(
            models.AgentTrace.target_agent,
            func.count().label("count"),
            func.avg(models.AgentTrace.duration_ms).label("avg_duration_ms"),
        )
        .where(models.AgentTrace.created_at >= since)
        .group_by(models.AgentTrace.target_agent)
    ).all()

    degraded_count = db.scalar(
        select(func.count()).where(
            models.AgentTrace.created_at >= since, models.AgentTrace.degraded.is_(True)
        )
    ) or 0
    flagged_count = db.scalar(
        select(func.count()).where(
            models.AgentTrace.created_at >= since,
            models.AgentTrace.critic_verdict_status == "flagged",
        )
    ) or 0

    return {
        "since": since.isoformat(),
        "total_invocations": total,
        "router_tier": llm_client.ROUTER_TIER,
        "by_agent": [
            {
                "target_agent": row.target_agent,
                "count": row.count,
                "avg_duration_ms": round(row.avg_duration_ms, 2) if row.avg_duration_ms is not None else None,
                "model_tier": llm_client.AGENT_TIERS.get(row.target_agent, "unknown"),
            }
            for row in by_agent_rows
        ],
        "degraded_rate": round(degraded_count / total, 4) if total else 0.0,
        "critic_flagged_rate": round(flagged_count / total, 4) if total else 0.0,
    }
