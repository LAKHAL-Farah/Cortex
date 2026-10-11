"""Collision-free node IPs for tests that register real `nodes` rows.

`nodes.ip_address` is unique and the test DB is shared by the whole session
with no per-test cleanup, so two test modules that each invent their own
address scheme (a counter here, a random octet there) will sometimes pick the
same `10.0.1.x` and the second create fails with a 409. Ask the DB instead.
"""
import itertools

from app import crud
from app.db import SessionLocal

# Process-wide so consecutive calls don't keep re-checking the same low octets.
_octets = itertools.cycle(range(2, 255))


def next_free_node_ip(prefix: str = "10.0.1") -> str:
    """Returns an address in `prefix`.0/24 (a managed subnet -- see
    schemas.NodeBase's ip_must_be_in_private_subnet validator) that no
    existing node is using."""
    db = SessionLocal()
    try:
        for _ in range(253):
            candidate = f"{prefix}.{next(_octets)}"
            if crud.get_node_by_ip(db, candidate) is None:
                return candidate
    finally:
        db.close()
    raise RuntimeError(f"no free address left in {prefix}.0/24 for the test DB")
