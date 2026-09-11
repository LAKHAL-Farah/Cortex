"""Inter-node network latency, measured via TCP connect timing.

Not an ICMP ping -- containers here don't have raw-socket privileges (see
sandbox testing notes), and ping isn't installed in the api image anyway.
Instead this times a plain TCP handshake against each node's own
node_exporter port (Sprint 1, story 1.2 -- already deployed and listening
on every monitored node), which is a reliable proxy for network-path
latency without needing any new exposed port or external tool.

Independent of topology_sync.py/Neutron -- this only ever reads the
`nodes` table (Postgres), the same source `crud.list_nodes` already
serves elsewhere. Read-only, side-effect-free: nothing here writes to
Postgres or Neo4j, callers decide what to do with the measurements (e.g.
expose them via a network-health endpoint, see routers/network.py).
"""
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session

from .. import crud

logger = logging.getLogger(__name__)

# How long to wait for a single TCP handshake before giving up on that
# node. Kept short and fixed rather than configurable -- a healthy LAN
# handshake completes in single-digit ms, so 2s is already generous
# enough to distinguish "slow" from "actually down".
CONNECT_TIMEOUT_SECONDS = 2.0
MAX_PARALLEL_CHECKS = 16


def _measure_one(hostname: str, ip_address: str | None, port: int) -> dict[str, Any]:
    """TCP-connect to one node and time the handshake. Prefers the
    hostname (works both in Docker Compose's internal DNS and against a
    real OpenStack network where hostnames resolve via the topology's own
    DNS/hosts setup); falls back to ip_address if hostname resolution
    itself is what's failing, so a DNS hiccup doesn't get misreported as
    the node being unreachable.
    """
    target = hostname
    start = time.perf_counter()
    try:
        with socket.create_connection((target, port), timeout=CONNECT_TIMEOUT_SECONDS):
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "hostname": hostname,
                "ip_address": ip_address,
                "port": port,
                "latency_ms": round(elapsed_ms, 2),
                "reachable": True,
                "error": None,
            }
    except Exception as exc:
        if ip_address and target != ip_address:
            start = time.perf_counter()
            try:
                with socket.create_connection((ip_address, port), timeout=CONNECT_TIMEOUT_SECONDS):
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    return {
                        "hostname": hostname,
                        "ip_address": ip_address,
                        "port": port,
                        "latency_ms": round(elapsed_ms, 2),
                        "reachable": True,
                        "error": None,
                    }
            except Exception as exc2:
                exc = exc2

        logger.warning("network latency: %s (%s:%d) unreachable: %r", hostname, target, port, exc)
        return {
            "hostname": hostname,
            "ip_address": ip_address,
            "port": port,
            "latency_ms": None,
            "reachable": False,
            "error": repr(exc),
        }


def measure_node_latencies(db: Session) -> list[dict[str, Any]]:
    """One TCP-timing pass over every active, node_exporter-equipped node
    in Postgres (crud.list_nodes -- the same source topology_sync.py
    already reads). Nodes that aren't active or never got node_exporter
    installed are skipped rather than reported as unreachable: they were
    never expected to answer on exporter_port, so measuring them would
    just produce noisy false positives.
    """
    # Filtered on is_active only -- node_exporter_installed is frequently
    # unset (None, not False) outside a real Ansible-driven deployment
    # (e.g. sandbox nodes seeded directly, not through
    # install_node_exporter), so it isn't a reliable gate here. Whether a
    # node's exporter_port actually answers is exactly what the TCP-connect
    # measurement itself determines -- an unreachable node simply comes
    # back with reachable=False rather than being silently excluded.
    nodes = [n for n in crud.list_nodes(db) if n.is_active]

    if not nodes:
        logger.info("network latency: no active nodes to measure")
        return []

    # A timeout must not make every subsequent node wait as well. Bound the
    # pool so a large deployment still finishes promptly without opening an
    # unbounded number of sockets at once.
    workers = min(len(nodes), MAX_PARALLEL_CHECKS)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="network-latency") as executor:
        results = list(executor.map(lambda n: _measure_one(n.hostname, n.ip_address, n.exporter_port), nodes))

    unreachable = [r["hostname"] for r in results if not r["reachable"]]
    if unreachable:
        logger.warning(
            "network latency: %d/%d node(s) unreachable: %s",
            len(unreachable), len(results), ", ".join(unreachable),
        )

    return results
