import uuid
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from . import models, schemas


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
