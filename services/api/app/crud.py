import uuid
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
