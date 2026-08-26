import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..db import get_db
from ..agents.graph import app_graph

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/agents",
    tags=["agents"],
)


@router.post("/orchestrate", response_model=schemas.AgentOrchestrateResponse)
def orchestrate(payload: schemas.AgentOrchestrateQuery, db: Session = Depends(get_db)):
    """Question in -> LangGraph (router -> monitoring -> compose) -> answer
    out. Mirrors GET /api/v1/dashboard's hostname->instance mapping (see
    dashboard.py) so the monitoring agent resolves a node the same way the
    rest of the API already does, instead of re-deriving it.
    """
    known_nodes: list[schemas.AgentKnownNode] = [
        {
            "hostname": n.hostname,
            "role": n.role.value if hasattr(n.role, "value") else n.role,
            "instance": f"{n.ip_address}:{n.exporter_port}",
        }
        for n in crud.list_nodes(db)
    ]

    result = app_graph.invoke(
        {
            "user_query": payload.query,
            "known_nodes": known_nodes,
            "failures": [],
        }
    )

    agent_result = result.get("agent_result") or {}
    return schemas.AgentOrchestrateResponse(
        answer=result["final_answer"],
        agent_used=result["target_agent"],
        raw_data=agent_result.get("raw_data"),
        confidence=agent_result.get("confidence"),
        degraded=bool(result.get("failures")),
    )
