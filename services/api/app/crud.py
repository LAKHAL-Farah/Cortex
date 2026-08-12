import uuid
from datetime import datetime
from sqlalchemy import select
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
