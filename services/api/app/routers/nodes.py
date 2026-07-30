import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from fastapi import BackgroundTasks
from .. import crud, schemas
from ..db import get_db
from ..security import require_api_key
from ..services.prometheus_sd import regenerate_file_sd
from ..services.inventory_manager import add_host_to_inventory
from ..services.ansible_runner import install_node_exporter

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
def create_node(payload: schemas.NodeCreate, background_tasks: BackgroundTasks,
                db: Session = Depends(get_db)):
    try:
        node = crud.create_node(db, payload)
    except crud.DuplicateNodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    add_host_to_inventory(node.hostname, node.ip_address, node.role)

    def _install_and_register():
        success = install_node_exporter(node.hostname)
        if success:
            # need a fresh session since this runs after the request's session closed
            from ..db import SessionLocal
            with SessionLocal() as bg_db:
                regenerate_file_sd(bg_db)
        else:
            logger.error("node_exporter install failed for %s; not added to Prometheus targets", node.hostname)

    background_tasks.add_task(_install_and_register)
    return node


@router.post("/{node_id}/exporter-check", response_model=schemas.NodeOut,
             dependencies=[Depends(require_api_key)])
def recheck_node_exporter(node_id: uuid.UUID, db: Session = Depends(get_db)):
    """Re-run the node_exporter install/check for an existing node and persist
    the result. Exists for nodes created before node_exporter_installed was
    tracked (they show as unknown/Inconnu until checked at least once), and
    as a manual retry for nodes whose install previously failed."""
    node = crud.get_node(db, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "node not found")

    success = install_node_exporter(node.hostname)
    if success:
        regenerate_file_sd(db)
    else:
        logger.error("node_exporter re-check failed for %s", node.hostname)

    node = crud.set_node_exporter_installed(db, node, success)
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
    _safe_regenerate_file_sd(db)
    return node


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_api_key)])
def delete_node(node_id: uuid.UUID, db: Session = Depends(get_db)):
    node = crud.get_node(db, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "node not found")
    crud.delete_node(db, node)
    # The DB row is already gone at this point; if the target file write
    # below fails (e.g. disk/permission issue), the node must still count
    # as deleted rather than surfacing a 500 for an operation that already
    # succeeded. Prometheus will pick up the correct target list on its
    # next file_sd refresh (or the next successful regenerate call) either way.
    _safe_regenerate_file_sd(db)



def _safe_regenerate_file_sd(db: Session) -> None:
    try:
        regenerate_file_sd(db)
    except Exception:
        logger.exception("failed to regenerate prometheus file_sd target file")