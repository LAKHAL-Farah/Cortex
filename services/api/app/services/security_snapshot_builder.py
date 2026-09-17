"""
services/api/app/services/security_snapshot_builder.py

Phase Sec-1: populates the `security_group_snapshots` table
(models.SecurityGroupSnapshot) that `_check_sec_group_diff`
(agents/nodes/security.py) reads to answer "did this change since we
last looked", not just "does this look risky right now"
(services/security_audit.py's static `_risk_reason` baseline).

Same pattern baseline_builder.py/topology_sync.py already establish for
a periodic job: its own `_connect()` (via security_audit's OpenStack
connection helper), its own SessionLocal-backed pass wired into main.py's
`_run_periodic`, so a security-group snapshot pass neither blocks on nor
gets blocked by the topology sync loop, the baseline refresh, or each
other -- all of them ultimately read from the same OpenStack cloud, but
none of them shares a connection or a schedule with any other.

Every pass is a fresh append (see models.SecurityGroupSnapshot's own
docstring on why this is append-only rather than upserted like
Baseline/RoleBaseline) -- `_check_sec_group_diff` always wants "the most
recent snapshot before this one", which only exists if the previous
pass's row was left alone rather than overwritten.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .. import crud, models
from . import security_audit

logger = logging.getLogger(__name__)


def capture_security_group_snapshots(db: Session) -> int:
    """Snapshots every currently-known node's attached security groups and
    rule-sets into `security_group_snapshots`, one row per
    (hostname, security_group_id), all stamped with the same
    `captured_at` for this pass.

    Returns the number of rows written (0 on a connection/listing failure
    -- logged and swallowed, same "a transient OpenStack hiccup shouldn't
    take a periodic job down permanently" contract every other job in
    main.py's lifespan already has via `_run_periodic`'s own try/except).
    """
    try:
        conn = security_audit._connect()
        groups_by_host = security_audit.list_security_groups_by_hostname(conn=conn)
    except Exception:
        logger.exception("security_snapshot_builder: failed to list security groups from OpenStack")
        return 0

    # Only snapshot hostnames Cortex actually knows about as Node rows --
    # a hypervisor OpenStack reports but node_seeder/topology_sync haven't
    # registered yet is skipped this pass rather than snapshotted under a
    # hostname nothing else in Cortex recognizes; the next pass picks it
    # up once it's a real Node row. If no nodes are registered at all yet
    # (a completely fresh, unseeded database), snapshot everything rather
    # than filtering down to nothing.
    known_hostnames = {n.hostname for n in crud.list_nodes(db)}

    captured_at = datetime.utcnow()
    rows_written = 0
    hosts_written = 0

    for hostname, groups in groups_by_host.items():
        if known_hostnames and hostname not in known_hostnames:
            continue
        if not groups:
            continue
        hosts_written += 1
        for group in groups:
            db.add(models.SecurityGroupSnapshot(
                hostname=hostname,
                security_group_id=group["id"],
                security_group_name=group["name"],
                rules=group["rules"],
                captured_at=captured_at,
            ))
            rows_written += 1

    db.commit()
    logger.info(
        "security_snapshot_builder: captured %d security-group snapshot row(s) across %d host(s)",
        rows_written, hosts_written,
    )
    return rows_written
