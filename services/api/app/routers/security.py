"""Phase Sec-2/Sec-3: read-only, pollable panel endpoints for the Security
Agent's findings.

Before this router existed, every Security Agent finding only reached a
caller through POST /api/v1/agents/orchestrate (chat) -- there was no
direct GET a dashboard could `useSWR` against the way routers/network.py
already gives the Network agent's data (see that router's own module
docstring). This is that same shape for Security: agents/nodes/
security.py's four sub-checks (auth-anomaly, sec-group-diff/drift,
CVE-match, eBPF-signal), read from a cached scan pass and redacted for
the caller's role, with no chat/LLM round-trip and no live per-request
Loki/Neutron/CVE-feed/eBPF round-trip required.

RBAC is the one thing this module deliberately does *not* reinvent: it
imports the exact same `security_rbac.filter_security_response_for_role`
routers/agents.py's chat path already uses, rather than keeping its own
copy -- a viewer-role account gets identical protection whether they ask
via chat or load this dashboard, which is only true if both routers call
the same function (see services/security_rbac.py's module docstring).

Phase Sec-5 adds three more read endpoints, deliberately not shaped like
each other, because the three checks aren't shaped like each other (see
the phased roadmap's own framing -- "layer" is stated explicitly for each):
GET /exposed-ports/{hostname} is Node-scope and reads the same
`security_finding_cache` everything else in this file reads (Sec-5a rides
the existing periodic scan pass, nothing new to poll); GET
/instance-exposed-ports (fleet-wide table) and GET
/instance-exposed-ports/{instance_id} (one instance) are Instance-scope
and have no per-node cache to read at all (Sec-5b is a live TCP-connect
probe against a VM's own IP, not part of any node's periodic scan --
the list variant checks every instance the topology graph knows about,
concurrently, on every request rather than riding a cache); GET
/keystone-tokens is Identity-scope, fleet-wide, and likewise computed live
(Sec-5c has no `hostname` to key a cache row on in the first place -- see
services/keystone_audit.py's own module docstring for why this is
deliberately not forced into the same per-host shape as everything else).

Phase Sec-3 changed *where* a finding comes from, not what it looks like:
GET /findings and friends used to call `_investigate(..., narrate=False)`
live, once per known node, on every single request -- which is why the
/security and /security/security-groups pages never rendered instantly,
every page load blocked on a fresh external round trip per host. Now a
periodic background pass (main.py's SECURITY_SCAN_INTERVAL_SECONDS,
services/security_scan_cache.py) does that live work on a schedule and
writes it into `security_finding_cache`; every read endpoint below reads
that cache (falling back to one on-demand live scan only for a host that
pass hasn't reached yet, e.g. a node registered seconds ago). GET /health
mirrors GET /api/v1/topology/health -- real run history, not a guess from
whatever's sitting in the cache -- and POST /resync is the same
"Reconverge"-style manual trigger TopologyResyncButton already gives the
topology page, just for a security scan pass instead of an OpenStack
sync pass.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, graph_db, models, schemas
from ..auth import get_current_user, require_admin
from ..db import get_db
from ..services import instance_exposure, keystone_audit, security_rbac, security_sandbox
from ..services.security_scan_cache import run_security_scan, scan_one_node

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/security", tags=["security"])


def _redact_cache_row(cache_row: models.SecurityFindingCache, current_user: models.User) -> dict:
    answer, raw_data = security_rbac.filter_security_response_for_role(
        cache_row.answer, cache_row.raw_data, "security", current_user.role
    )
    return {
        "hostname": cache_row.hostname,
        "role": cache_row.role,
        "confidence": cache_row.confidence,
        "has_signal": cache_row.has_signal,
        "degraded": cache_row.degraded,
        "answer": answer,
        "raw_data": raw_data,
        "scanned_at": cache_row.updated_at.isoformat(),
    }


def _cached_or_live(db: Session, node_row: models.Node) -> models.SecurityFindingCache:
    """Reads this host's cached finding, computing one on the spot (and
    caching it) if the periodic pass hasn't reached this node yet -- a
    node registered between two scan ticks should still return something
    real on its first GET rather than a blank/missing row.
    """
    cache_row = crud.get_security_finding_cache(db, node_row.hostname)
    if cache_row is not None:
        return cache_row
    return scan_one_node(db, node_row)


@router.get("/findings")
def list_findings(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One Security Agent finding per known node, redacted the same way
    POST /api/v1/agents/orchestrate redacts a security-flavored chat
    answer for the caller's role (see services/security_rbac.py). This is
    the endpoint a security-overview dashboard page polls for its main
    per-host list/table -- reading `security_finding_cache`
    (services/security_scan_cache.py), not a live per-request scan.
    """
    findings = [_redact_cache_row(_cached_or_live(db, node_row), current_user) for node_row in crud.list_nodes(db)]
    return {"findings": findings}


@router.get("/findings/{hostname}")
def get_finding(hostname: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Same finding as one entry of GET /findings, for a dashboard drill-
    down into a single host instead of re-fetching the whole fleet's
    cached findings just to show one.
    """
    node_row = crud.get_node_by_hostname(db, hostname)
    if node_row is None:
        raise HTTPException(status_code=404, detail=f"No known node '{hostname}'.")
    return _redact_cache_row(_cached_or_live(db, node_row), current_user)


@router.get("/status")
def get_status(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Condensed fleet-wide badge -- "ok" if no known node currently has a
    signal fired across any of the four sub-checks and every sub-check
    ran cleanly, "degraded" otherwise. Same ok/degraded vocabulary
    routers/network.py's GET /health already uses, so dashboard header
    badges read consistently across agents regardless of caller role
    (this endpoint never returns anything specific enough to need
    redacting in the first place -- see services/security_rbac.py).

    Reads the same cache GET /findings does (fast, no live scan), plus
    two fields Phase Sec-3 added: `last_scan_at` (freshest cache row's
    `updated_at`, null if nothing has been scanned yet) and
    `sandbox_mode` (whether POST /sandbox/drift/{hostname}'s drift-demo
    controls are actually reachable in this deployment -- see
    services/security_sandbox.py).
    """
    cache_rows = crud.list_security_finding_cache(db)
    node_count = len(crud.list_nodes(db))
    any_signal = any(row.has_signal for row in cache_rows)
    any_degraded = any(row.degraded for row in cache_rows)
    last_scan_at = max((row.updated_at for row in cache_rows), default=None)

    status = "degraded" if (any_signal or any_degraded) else "ok"
    return {
        "status": status,
        "has_signal": any_signal,
        "degraded": any_degraded,
        "node_count": node_count,
        "last_scan_at": last_scan_at.isoformat() if last_scan_at else None,
        "sandbox_mode": security_sandbox.CORTEX_ENV == "sandbox",
    }


@router.get("/health")
def get_security_health(db: Session = Depends(get_db)):
    """Scan-loop health, backed by the `security_scan_runs` table main.py
    writes to after every pass of `run_security_scan` -- NOT a live
    recomputation. Same reasoning routers/topology.py's GET /health
    docstring gives for its own version of this endpoint: the cache can
    look perfectly fine (last successful pass's rows still sitting there)
    even if the scan loop itself has silently stopped running, so this
    answers "is the scan loop healthy" from actual run history instead of
    guessing from the cache.
    """
    run = crud.get_latest_security_scan_run(db)
    if run is None:
        return {"status": "unknown", "last_run": None}
    return {
        "status": run.status,
        "last_run": {
            "status": run.status,
            "summary": run.summary,
            "error": run.error,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat(),
        },
    }


def _resync_run_status(summary: dict) -> str:
    """Same classification main.py's periodic loop applies to a
    run_security_scan() pass (see main.py::_security_scan_status) --
    duplicated rather than imported for the same reason routers/
    topology.py's own `_resync_run_status` duplicates
    `_topology_sync_status`: importing main.py from a router would be
    circular (main.py imports this router).
    """
    if summary.get("degraded"):
        return "degraded"
    return "ok"


@router.post("/resync")
def trigger_security_resync(db: Session = Depends(get_db)):
    """Manual, on-demand trigger for the same fleet-wide scan pass
    main.py's periodic loop already runs every
    SECURITY_SCAN_INTERVAL_SECONDS -- the Security-page equivalent of
    TopologyResyncButton's "Reconverge" control. Records its own outcome
    to `security_scan_runs` (same table the periodic pass writes to) so
    GET /health reflects a manual run exactly like it would an automatic
    one, then returns that run so the button can show it immediately
    without a second round trip.
    """
    started_at = datetime.utcnow()
    try:
        summary = run_security_scan(db)
        run_status = _resync_run_status(summary)
        error = None
    except Exception as exc:
        logger.exception("security resync pass failed")
        summary = None
        run_status = "failed"
        error = repr(exc)
    finished_at = datetime.utcnow()

    run = crud.record_security_scan_run(
        db, status=run_status, summary=summary, error=error, started_at=started_at, finished_at=finished_at,
    )
    return {
        "status": run.status,
        "summary": run.summary,
        "error": run.error,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat(),
    }


@router.get("/groups/{hostname}")
def get_security_groups(hostname: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cached security-group rules for one node plus the Phase Sec-1
    drift diff against the most recent stored snapshot: the same data
    GET /findings' `raw_data.sec_group_signal` carries for this host,
    broken out on its own for the dedicated "security groups" panel view
    that shouldn't need to also pull auth/CVE/eBPF data it isn't showing.

    Same RBAC rule as everything else in this router: a non-admin gets
    `has_signal`/`degraded` booleans, never the actual rules, the drift
    entries, or (Phase Sec-3) the full current-groups-per-node listing
    under `data.security_groups`.
    """
    node_row = crud.get_node_by_hostname(db, hostname)
    if node_row is None:
        raise HTTPException(status_code=404, detail=f"No known node '{hostname}'.")

    cache_row = _cached_or_live(db, node_row)
    sec_group_signal = cache_row.raw_data.get("sec_group_signal", {})
    _, redacted = security_rbac.filter_security_response_for_role(
        "", {"sec_group_signal": sec_group_signal}, "security", current_user.role
    )
    result = dict(redacted["sec_group_signal"])
    result["scanned_at"] = cache_row.updated_at.isoformat()
    return result


@router.get("/exposed-ports/{hostname}")
def get_exposed_ports(hostname: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Phase Sec-5a, Node scope. Cached confirmed-exposure cross-check for
    one node -- exactly `raw_data.exposed_port_signal` from GET /findings
    for this same host, broken out on its own for a dedicated "exposed
    ports" panel, same pattern GET /groups/{hostname} already established
    for sec-group data. Reads the same `security_finding_cache` row (no
    new scan, no new collector call here) since `_check_exposed_ports`
    already ran as part of this node's regular periodic pass.
    """
    node_row = crud.get_node_by_hostname(db, hostname)
    if node_row is None:
        raise HTTPException(status_code=404, detail=f"No known node '{hostname}'.")

    cache_row = _cached_or_live(db, node_row)
    exposed_port_signal = cache_row.raw_data.get("exposed_port_signal", {})
    _, redacted = security_rbac.filter_security_response_for_role(
        "", {"exposed_port_signal": exposed_port_signal}, "security", current_user.role
    )
    result = dict(redacted["exposed_port_signal"])
    result["scanned_at"] = cache_row.updated_at.isoformat()
    return result


@router.get("/instance-exposed-ports")
def list_instance_exposed_ports(current_user: models.User = Depends(get_current_user)):
    """Phase Sec-5b, fleet-wide table variant. Every instance the
    topology graph knows about (`graph_db.fetch_all_instances()`, synced
    by topology_sync.py's regular pass -- not a fresh Nova listing, same
    "read topology if it's there" rule Sec-5b's IP resolution already
    follows), each checked with the exact same `instance_exposure.
    build_instance_exposure_signal` the single-instance endpoint below
    uses, redacted the same way.

    Genuinely live, not cached -- there is still no per-node scan loop
    this can ride (see this router's module docstring and services/
    instance_exposure.py's own docstring). Unlike the single-instance
    endpoint, this checks every known instance on every request, so the
    per-instance OpenStack SDK reads and TCP-connect probes run
    concurrently (one thread per instance) rather than one after
    another -- otherwise N instances would mean N x up to ~4s (two
    declared ports at CONNECT_TIMEOUT_SECONDS each) in the worst case,
    serially, on a single page load.
    """
    instances = graph_db.fetch_all_instances()

    def _signal_for(instance: dict) -> dict:
        return instance_exposure.build_instance_exposure_signal(instance["id"], instance_name=instance["name"])

    if instances:
        with ThreadPoolExecutor(max_workers=min(len(instances), 16)) as pool:
            signals = list(pool.map(_signal_for, instances))
    else:
        signals = []

    redacted_signals = []
    for signal in signals:
        _, redacted = security_rbac.filter_security_response_for_role(
            "", {"instance_exposure_signal": signal}, "security", current_user.role
        )
        redacted_signals.append(redacted["instance_exposure_signal"])

    return {"instances": redacted_signals}


@router.get("/instance-exposed-ports/{instance_id}")
def get_instance_exposed_ports(instance_id: str, current_user: models.User = Depends(get_current_user)):
    """Phase Sec-5b, Instance scope -- an *explicit stretch*, not part of
    any node's periodic scan (see this router's module docstring and
    services/instance_exposure.py's own docstring for why this is
    genuinely a different kind of check, not a variant of Sec-5a): live,
    on demand, one instance at a time. Resolves the instance's own
    reachable IP and declared-world-open rules, then actually TCP-connect
    probes each declared port -- `confirmed` entries are a real, live
    reachability finding, not a cached one, since there's no per-node
    scan loop for this to ride.

    No local `Node`/instance registry exists to 404 against up front (see
    crud.py -- only physical `Node` rows are tracked, not OpenStack
    server IDs), so an unknown `instance_id` surfaces as "no reachable IP
    found" rather than a 404 -- an honest "couldn't resolve this
    instance", not a guess that it doesn't exist.
    """
    signal = instance_exposure.build_instance_exposure_signal(instance_id)

    _, redacted = security_rbac.filter_security_response_for_role(
        "", {"instance_exposure_signal": signal}, "security", current_user.role
    )
    return redacted["instance_exposure_signal"]


@router.get("/keystone-tokens")
def get_keystone_tokens(current_user: models.User = Depends(get_current_user)):
    """Phase Sec-5c, Identity scope -- fleet/project-wide, not per-node
    (see services/keystone_audit.py's own module docstring for why this
    deliberately has no `hostname` in its response, unlike every other
    endpoint in this router). Live, on demand: reads the sandbox's (or a
    real deployment's) token-issuance log and applies the three small,
    explicit abuse patterns keystone_audit.find_abusive_token_patterns
    documents -- rapid re-issuance, an unexpected source IP, or an
    unusually long-lived token.
    """
    try:
        events = keystone_audit.get_token_issuance_log()
    except Exception as exc:
        logger.warning("keystone-tokens: couldn't reach the token-issuance log: %s", exc)
        signal = {
            "has_signal": False,
            "degraded": True,
            "detail": "Couldn't reach the Keystone token-issuance log -- no collector deployed yet.",
        }
    else:
        patterns = keystone_audit.find_abusive_token_patterns(events)
        has_signal = bool(patterns["rapid_reissue"] or patterns["unexpected_ip"] or patterns["long_lived"])
        if has_signal:
            fired = []
            if patterns["rapid_reissue"]:
                fired.append(f"{len(patterns['rapid_reissue'])} rapid-reissue user(s)")
            if patterns["unexpected_ip"]:
                fired.append(f"{len(patterns['unexpected_ip'])} token(s) from an unexpected IP")
            if patterns["long_lived"]:
                fired.append(f"{len(patterns['long_lived'])} unusually long-lived token(s)")
            detail = f"Token-abuse patterns found across {patterns['event_count']} issuance event(s): " + ", ".join(fired) + "."
        else:
            detail = f"No token-abuse pattern found across {patterns['event_count']} issuance event(s)."
        signal = {"has_signal": has_signal, "degraded": False, "detail": detail, **patterns}

    _, redacted = security_rbac.filter_security_response_for_role(
        "", {"keystone_token_signal": signal}, "security", current_user.role
    )
    return redacted["keystone_token_signal"]


# ---------------------------------------------------------------------
# Sandbox-only drift demonstration (Phase Sec-3). See
# services/security_sandbox.py's module docstring: gated on
# CORTEX_ENV=sandbox, mutates only openstack-sim's in-memory sandbox
# state, never reachable against a real cloud.
# ---------------------------------------------------------------------

@router.post("/sandbox/drift/{hostname}", dependencies=[Depends(require_admin)])
def simulate_security_group_drift(hostname: str, db: Session = Depends(get_db)):
    """Injects one real, end-to-end security-group change on `hostname`
    for demonstration purposes -- see security_sandbox.inject_demo_drift
    for the exact steps (snapshot, inject, re-scan). Admin-only, same gate
    as every other infra-mutating endpoint in this codebase (auth.py's
    `require_admin`), even though it only ever touches the sandbox.
    """
    node_row = crud.get_node_by_hostname(db, hostname)
    if node_row is None:
        raise HTTPException(status_code=404, detail=f"No known node '{hostname}'.")
    try:
        return security_sandbox.inject_demo_drift(db, hostname)
    except security_sandbox.SandboxUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except security_sandbox.NoSecurityGroupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/sandbox/drift/{hostname}", dependencies=[Depends(require_admin)])
def reset_security_group_drift(hostname: str, rule_id: str, db: Session = Depends(get_db)):
    """Removes one previously-injected demo rule (`rule_id`, returned by
    the POST above) and re-scans the host -- see
    security_sandbox.reset_demo_drift.
    """
    try:
        return security_sandbox.reset_demo_drift(db, hostname, rule_id)
    except security_sandbox.SandboxUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------
# Phase Sec-6: audit log. Not tied to any single sub-check -- this is the
# RBAC/redaction story made visible, and the evidence trail §7.11
# (Gouvernance & Garde-fous) asks for specifically around security-
# sensitive answers.
# ---------------------------------------------------------------------

@router.get(
    "/audit-log", response_model=schemas.SecurityAuditLogResponse, dependencies=[Depends(require_admin)]
)
def get_security_audit_log(hours: int = 24, db: Session = Depends(get_db)):
    """Who asked a security question, what role they had, and whether the
    answer was redacted -- over the last `hours` (default 24, same
    dashboard-rollup shape as GET /api/v1/agents/stats).

    Admin-only: this endpoint's whole purpose is to let an admin review
    *other* accounts' security-flavored turns, including the ones that
    were themselves redacted for a viewer -- so it needs to bypass the
    same RBAC filter it's reporting on, not apply it to itself.

    Deliberately reuses rather than re-derives the RBAC logic already
    gating chat/dashboard answers (models.AgentTrace.security_involved is
    computed once, at write time, via the exact same
    `security_rbac.security_agent_involved` call routers/agents.py makes
    for its own response filtering -- see that router and
    models.AgentTrace's own docstring): every row returned here already
    satisfies `security_involved`, so `redacted` below is just
    `security_rbac.filter_security_response_for_role`'s own rule
    (`role != "admin"`) replayed against the role snapshotted on the row,
    not a second, parallel definition of "redacted" that could drift out
    of sync with the one actually gating live answers.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = crud.list_security_audit_log(db, since=since)
    entries = [
        schemas.SecurityAuditLogEntry(
            trace_id=str(trace.id),
            created_at=trace.created_at.isoformat(),
            user_query=trace.user_query,
            username=username,
            user_role=trace.user_role,
            target_agent=trace.target_agent,
            # security_involved is true for every row this query returns
            # (crud.list_security_audit_log's own filter) -- the only
            # remaining variable in filter_security_response_for_role's
            # rule is the asker's role, so this is that same rule, not a
            # re-implementation of it.
            redacted=trace.user_role != "admin",
        )
        for trace, username in rows
    ]
    return schemas.SecurityAuditLogResponse(since=since.isoformat(), entries=entries)
