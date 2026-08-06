#!/usr/bin/env python3
"""Manual one-off trigger for topology_sync.sync_topology(), e.g. to force
a pass right after standing up a new hypervisor instead of waiting for the
next TOPOLOGY_SYNC_INTERVAL_SECONDS tick. Supersedes run_discovery.py, which
called the now-deleted openstack_discovery.discover_new_computes() -- see
docs/architecture/adr-0002-topology-graph.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import SessionLocal
from app.services.topology_sync import sync_topology

if __name__ == "__main__":
    db = SessionLocal()
    try:
        result = sync_topology(db)
        print(result)
    finally:
        db.close()
