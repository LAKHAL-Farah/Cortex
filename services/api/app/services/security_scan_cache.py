"""Fleet-wide caching pass for the Security Agent's findings (Phase Sec-3).

Before this module existed, routers/security.py's GET /findings called
`agents/nodes/security.py::_investigate` live, once per known node, on
*every* request -- so `/security` and `/security/security-groups` never
rendered instantly; both pages blocked on a fresh Loki/Neutron/CVE-feed/
eBPF round trip per host, per page load, per person looking at the page.

`run_security_scan` does that same live work on a schedule instead
(main.py's SECURITY_SCAN_INTERVAL_SECONDS, the same periodic-task idiom
`sync_topology`/`sync_prometheus_health` already use) and writes the
result into `security_finding_cache` (models.SecurityFindingCache, one
upserted row per hostname). routers/security.py then reads that cache --
a plain indexed Postgres read instead of four external calls per host --
and applies the exact same RBAC redaction it always did, just at read
time against the stored raw_data instead of against a freshly-computed
one.

This intentionally mirrors, rather than replaces, Phase Sec-1's snapshot
builder (security_snapshot_builder.py): that job feeds *drift* (this
pass's live rules vs. the last stored snapshot), this job feeds *how
fresh is the panel a person is looking at right now*. Different questions,
different tables, same "a periodic pass plus a manual on-demand trigger"
shape -- see routers/security.py's POST /resync.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .. import crud, models
from ..agents.nodes import security as security_agent_node

logger = logging.getLogger(__name__)


def _known_node(node_row: models.Node) -> dict:
    """Same KnownNode shape routers/security.py's own `_known_node`
    builds -- duplicated rather than imported to avoid a
    routers -> services -> routers import cycle (routers/security.py
    already imports this module). Kept in sync by hand, same tradeoff
    routers/topology.py's `_resync_run_status` docstring calls out for
    its own small duplicated helper.
    """
    return {
        "hostname": node_row.hostname,
        "role": node_row.role.value if hasattr(node_row.role, "value") else node_row.role,
        "instance": f"{node_row.ip_address}:{node_row.exporter_port}",
    }


def scan_one_node(db: Session, node_row: models.Node) -> models.SecurityFindingCache:
    """Runs the Security Agent's four sub-checks for one node and upserts
    its cache row -- the on-demand fallback routers/security.py uses for
    a hostname `run_security_scan` hasn't reached yet (a freshly-added
    node), and the single-host path POST /resync's sandbox drift-demo
    endpoint uses to make an injected change visible immediately instead
    of waiting for the next periodic tick.
    """
    node = _known_node(node_row)
    agent_result, failures = security_agent_node._investigate(
        f"security scan pass for {node['hostname']}", node, narrate=False
    )
    return crud.upsert_security_finding_cache(
        db,
        hostname=node["hostname"],
        role=node["role"],
        confidence=agent_result["confidence"],
        has_signal=agent_result["raw_data"]["has_signal"],
        degraded=bool(failures),
        answer=agent_result["summary"],
        raw_data=agent_result["raw_data"],
    )


def run_security_scan(db: Session) -> dict:
    """One fleet-wide pass: scans every known node and upserts its cache
    row. Returns a summary dict main.py's `_run_periodic_recorded`
    classifies into "ok"/"degraded" (see main.py's
    `_security_scan_status`) and routers/security.py's POST /resync
    returns directly to the caller.

    A single node's sub-checks failing (a Loki/Neutron/CVE-feed/eBPF
    hiccup) degrades that node's own `degraded` flag -- same
    resilience.get_breaker contract every other external read in this
    codebase already has -- but never aborts the rest of the pass; the
    other nodes' cache rows still get refreshed this tick.
    """
    node_rows = crud.list_nodes(db)
    any_signal = False
    any_degraded = False
    for node_row in node_rows:
        try:
            cache_row = scan_one_node(db, node_row)
        except Exception:
            logger.exception("security_scan_cache: scan failed for node %s", node_row.hostname)
            any_degraded = True
            continue
        if cache_row.has_signal:
            any_signal = True
        if cache_row.degraded:
            any_degraded = True

    return {"node_count": len(node_rows), "has_signal": any_signal, "degraded": any_degraded}
