import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from .. import crud, schemas
from ..db import get_db
from ..security import require_api_key
from ..services.prometheus_sd import regenerate_file_sd



import logging
from .. import crud, schemas
from ..db import get_db
from ..security import require_api_key
from ..services.prometheus_sd import regenerate_file_sd

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


@router.get("", response_model=list[schemas.NodeOut])
def list_nodes(db: Session = Depends(get_db)):
    return crud.list_nodes(db)


@router.get("/{node_id}", response_model=schemas.NodeOut)
def get_node(node_id: uuid.UUID, db: Session = Depends(get_db)):
    node = crud.get_node(db, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "node not found")
    return node


@router.post("", response_model=schemas.NodeOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_api_key)])
def create_node(payload: schemas.NodeCreate, db: Session = Depends(get_db)):
    try:
        node = crud.create_node(db, payload)
    except crud.DuplicateNodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    regenerate_file_sd(db)
    return node


@router.put("/{node_id}", response_model=schemas.NodeOut, dependencies=[Depends(require_api_key)])
def update_node(node_id: uuid.UUID, payload: schemas.NodeUpdate, db: Session = Depends(get_db)):
    node = crud.get_node(db, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "node not found")
    try:
        node = crud.update_node(db, node, payload)
    except crud.DuplicateNodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    regenerate_file_sd(db)
    return node


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_api_key)])
def delete_node(node_id: uuid.UUID, db: Session = Depends(get_db)):
    node = crud.get_node(db, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "node not found")
    crud.delete_node(db, node)
    regenerate_file_sd(db)



def _safe_regenerate_file_sd(db: Session) -> None:
    try:
        regenerate_file_sd(db)
    except Exception:
        logger.exception("failed to regenerate prometheus file_sd target file")