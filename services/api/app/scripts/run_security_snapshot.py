#!/usr/bin/env python3
"""Manual one-off trigger for
security_snapshot_builder.capture_security_group_snapshots(), e.g. right
after injecting a fault via openstack-sim's
/_sandbox/security-group-rule/add or /remove, instead of waiting for the
next SECURITY_SNAPSHOT_INTERVAL_SECONDS tick (see main.py). Mirrors
run_topology_sync.py.

Typical real-life-scenario sandbox flow this supports (see
infra/openstack-sim/app.py's SECURITY_GROUPS/SECURITY_GROUP_RULES seed
data and its own module comments):

    1. `docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up`
    2. Ask the Security Agent about compute1-sim -- it flags the seeded
       world-open SSH rule (services/security_audit.py's `_risk_reason`),
       but `sec_group_signal.drift` is empty (no snapshot yet).
    3. `python app/scripts/run_security_snapshot.py` -- takes the first
       snapshot.
    4. Inject a change:
         curl -X POST http://localhost:5000/_sandbox/security-group-rule/add \\
           -H 'Content-Type: application/json' \\
           -d '{"id": "test-rule-1", "security_group_id": "8f3f0f4a-0000-0000-0000-0000000000e1", \\
                "direction": "ingress", "ethertype": "IPv4", "protocol": "tcp", \\
                "port_range_min": 8080, "port_range_max": 8080, "remote_ip_prefix": "10.0.0.0/16"}'
       (a newly-opened *internal-only* port -- not risky by the static
       baseline, so this only shows up at all because of Phase Sec-1.)
    5. Ask the Security Agent about compute1-sim again (or GET
       /api/v1/security/groups/compute1-sim) *before* running this script
       again -- drift is still empty, since nothing has re-snapshotted yet.
    6. `python app/scripts/run_security_snapshot.py` again, then ask once
       more -- `sec_group_signal.drift` now reports the added rule against
       the snapshot from step 3, even though it never tripped the
       overly-permissive baseline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import SessionLocal
from app.services.security_snapshot_builder import capture_security_group_snapshots

if __name__ == "__main__":
    db = SessionLocal()
    try:
        rows_written = capture_security_group_snapshots(db)
        print(f"security-group snapshot: {rows_written} row(s) written")
    finally:
        db.close()
