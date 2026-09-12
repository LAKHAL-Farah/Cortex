"""Phase Sec-2: read-only, pollable panel endpoints for the Security
Agent's findings.

Before this router existed, every Security Agent finding only reached a
caller through POST /api/v1/agents/orchestrate (chat) -- there was no
direct GET a dashboard could `useSWR` against the way routers/network.py
already gives the Network agent's data (see that router's own module
docstring). This is that same shape for Security: agents/nodes/
security.py's four sub-checks (auth-anomaly, sec-group-diff/drift,
CVE-match, eBPF-signal), read directly and redacted for the caller's
role, with no chat/LLM round-trip required.

RBAC is the one thing this module deliberately does *not* reinvent: it
imports the exact same `security_rbac.filter_security_response_for_role`
routers/agents.py's chat path already uses, rather than keeping its own
copy -- a viewer-role account gets identical protection whether they ask
via chat or load this dashboard, which is only true if both routers call
the same function (see services/security_rbac.py's module docstring).

No LLM narration: `agents/nodes/security.py`'s `_investigate(...,
narrate=False)` skips the chat path's LLM call and uses its deterministic
fallback sentence instead -- a panel a dashboard polls every few seconds,
for every known node, shouldn't pay an LLM invocation per host per poll
just to restate the same four sub-checks in prose. The sub-checks
themselves are unchanged: this still reads live Loki/Neutron/CVE-feed/
eBPF-sensor data (or the Phase Sec-1 snapshot table for drift) the exact
same way the chat path does, through the same circuit breakers.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, models
from ..auth import get_current_user
from ..db import get_db
from ..agents.nodes import security as security_agent_node
from ..services import security_rbac

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/security", tags=["security"])


def _known_node(node_row: models.Node) -> dict:
    """Same hostname/role/instance shape routers/agents.py's `orchestrate`
    builds from a `models.Node` row, so `_investigate` (which only knows
    about the KnownNode TypedDict shape, never the ORM model) works
    identically whether it's called from the chat graph or from here.
    """
    return {
        "hostname": node_row.hostname,
        "role": node_row.role.value if hasattr(node_row.role, "value") else node_row.role,
        "instance": f"{node_row.ip_address}:{node_row.exporter_port}",
    }


def _redacted_finding(node: dict, current_user: models.User) -> dict:
    agent_result, failures = security_agent_node._investigate(
        f"security dashboard check for {node['hostname']}", node, narrate=False
    )
    answer, raw_data = security_rbac.filter_security_response_for_role(
        agent_result["summary"], agent_result["raw_data"], "security", current_user.role
    )
    return {
        "hostname": node["hostname"],
        "role": node["role"],
        "confidence": agent_result["confidence"],
        "has_signal": agent_result["raw_data"]["has_signal"],
        "degraded": bool(failures),
        "answer": answer,
        "raw_data": raw_data,
    }


@router.get("/findings")
def list_findings(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One Security Agent finding per known node, redacted the same way
    POST /api/v1/agents/orchestrate redacts a security-flavored chat
    answer for the caller's role (see services/security_rbac.py). This is
    the endpoint a security-overview dashboard page polls for its main
    per-host list/table.
    """
    findings = [_redacted_finding(_known_node(node_row), current_user) for node_row in crud.list_nodes(db)]
    return {"findings": findings}


@router.get("/findings/{hostname}")
def get_finding(hostname: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Same finding as one entry of GET /findings, for a dashboard drill-
    down into a single host instead of re-fetching (and re-computing) the
    whole fleet's findings just to show one.
    """
    node_row = crud.get_node_by_hostname(db, hostname)
    if node_row is None:
        raise HTTPException(status_code=404, detail=f"No known node '{hostname}'.")
    return _redacted_finding(_known_node(node_row), current_user)


@router.get("/status")
def get_status(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Condensed fleet-wide badge -- "ok" if no known node currently has a
    signal fired across any of the four sub-checks and every sub-check
    ran cleanly, "degraded" otherwise. Same ok/degraded vocabulary
    routers/network.py's GET /health already uses, so dashboard header
    badges read consistently across agents regardless of caller role
    (this endpoint never returns anything specific enough to need
    redacting in the first place -- see services/security_rbac.py).
    """
    any_signal = False
    any_degraded = False
    node_count = 0
    for node_row in crud.list_nodes(db):
        node_count += 1
        agent_result, failures = security_agent_node._investigate(
            f"security dashboard status check for {node_row.hostname}", _known_node(node_row), narrate=False
        )
        if agent_result["raw_data"]["has_signal"]:
            any_signal = True
        if failures:
            any_degraded = True

    status = "degraded" if (any_signal or any_degraded) else "ok"
    return {"status": status, "has_signal": any_signal, "degraded": any_degraded, "node_count": node_count}


@router.get("/groups/{hostname}")
def get_security_groups(hostname: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Live security-group rules for one node plus the Phase Sec-1 drift
    diff against the most recent stored snapshot -- the same data GET
    /findings' `raw_data.sec_group_signal` carries for this host, broken
    out on its own for a dedicated "security groups" panel view that
    shouldn't need to also pull auth/CVE/eBPF data it isn't showing.

    Same RBAC rule as everything else in this router: a non-admin gets
    `has_signal`/`degraded` booleans, never the actual rules or which
    ones were added/removed.
    """
    node_row = crud.get_node_by_hostname(db, hostname)
    if node_row is None:
        raise HTTPException(status_code=404, detail=f"No known node '{hostname}'.")

    sec_group_signal = security_agent_node._check_sec_group_diff(_known_node(node_row))
    _, redacted = security_rbac.filter_security_response_for_role(
        "", {"sec_group_signal": sec_group_signal}, "security", current_user.role
    )
    return redacted["sec_group_signal"]
