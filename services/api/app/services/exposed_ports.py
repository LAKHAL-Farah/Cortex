"""Node-scope exposed-port cross-check for the Security Agent's fifth
sub-check (Phase Sec-5a, agents/nodes/security.py).

Layer: **Node**. This is deliberately about what a controller/compute/
storage/monitoring host is actually listening on (an `ss -tlnp`-
equivalent) -- not what's running inside a guest VM's own network stack
(that's Phase Sec-5b's instance-scope probe, services/instance_exposure.py,
a genuinely different question answered a genuinely different way -- see
that module's own docstring for why a TCP-connect probe, not a listening-
socket read, is the only honest way to ask it).

Two independent pieces, same split cve_feed.py already established for
Sec-3 and for the identical reason: a pure matcher is trivially unit-
testable without mocking HTTP, and a real collector can be swapped in
later without touching the matching logic.

1. `get_listening_ports(hostname)` -- an HTTP client against
   `{CORTEX_PACKAGE_INVENTORY_URL}/listening-ports?host=`, reusing
   cve_feed.py's `PACKAGE_INVENTORY_URL` rather than a second env var:
   the roadmap doc's own §5 (Sec-5a) says to "extend [Sec-3's collector]
   with a `/listening-ports?host=` endpoint... so it's genuinely small
   once Sec-3 exists" -- a listening-socket read and an installed-
   package read are both "facts about this host's own OS", the same
   collector answers both, only the path differs. As of Phase Sec-5a,
   the sandbox (infra/openstack-sim/app.py) serves this route from a
   small seeded table, same shape as its own PACKAGE_INVENTORY. Outside
   the sandbox, point this at a real fact-gathering source (an
   Ansible-facts `ss`/`netstat` read, an osquery `listening_ports` table
   query, a Prometheus node_exporter textfile collector) and this starts
   reading real bound sockets there too. Raises on a connection/timeout/
   non-2xx -- same division of responsibility as every other client
   function in this codebase (cve_feed.get_package_inventory,
   ebpf_signal.get_node_ebpf_alerts): the client raises, the caller
   (nodes/security.py's `_check_exposed_ports`) wraps it in a breaker and
   degrades gracefully.

   Expected response shape from `.../listening-ports?host=<hostname>`:
       [{"port": int, "protocol": "tcp"|"udp", "process": str}, ...]

2. `find_exposed_port_mismatches(listening_ports, risky_rules)` -- a pure
   function, no network calls. Cross-checks this host's actual listening
   sockets against `security_audit._risk_reason`'s already-computed
   "overly permissive" security-group rules for this same host (the
   `risky_rules` list `security_audit.get_node_security_groups` already
   returns, and `_check_sec_group_diff` already fetched this same
   investigation pass -- see nodes/security.py's `_check_exposed_ports`
   for why this deliberately does NOT re-query Neutron a second time).

   The **interesting finding is the mismatch, not either side alone**
   (the roadmap doc's own framing, verbatim): a world-open rule with
   nothing actually listening behind it is a real but *unconfirmed*
   exposure (the door is open, but there's nothing to walk in on); a
   listening socket with no world-open rule covering it isn't reachable
   from outside at all (Neutron/a host firewall default-denies anything
   not explicitly permitted, so this is expected and not flagged). Only
   the intersection -- a rule that world-opens a port range AND a real
   process is confirmed bound to a port inside that range -- is reported
   here, since that's the only case that's both configured *and* real.
"""
import logging
import os

import requests

from . import cve_feed

logger = logging.getLogger(__name__)

# Deliberately the same collector Sec-3 already stood up (see module
# docstring) -- no second env var to configure, no second sandbox
# container to stand up.
LISTENING_PORTS_URL = os.environ.get("CORTEX_PACKAGE_INVENTORY_URL", cve_feed.PACKAGE_INVENTORY_URL)


def get_listening_ports(hostname: str) -> list[dict]:
    """[{"port": int, "protocol": str, "process": str}, ...] for every
    socket this host reports actually bound and listening. Raises on a
    connection/timeout/non-2xx -- see module docstring on why that's the
    expected, gracefully-degraded-by-the-caller state in an environment
    with no listening-port collector wired up yet."""
    response = requests.get(f"{LISTENING_PORTS_URL}/listening-ports", params={"host": hostname}, timeout=8)
    response.raise_for_status()
    return response.json()


def find_exposed_port_mismatches(listening_ports: list[dict], risky_rules: list[dict]) -> list[dict]:
    """Pure -- no network calls. One entry per (world-open rule, actually-
    listening port) pair where the rule's port range covers a port this
    host is genuinely bound to -- see module docstring for why only this
    intersection is reported, not either side alone.

    `risky_rules` is exactly `security_audit.get_node_security_groups()`'s
    `risky_rules` list: `[{"security_group": str, "rule": {...}, "reason":
    str}, ...]`, where `rule` carries `port_range_min`/`port_range_max`
    (both `None` means "all ports", the same "all protocols and ports
    open to the world" case `security_audit._risk_reason` already
    detects).
    """
    listening_by_port = {p["port"]: p for p in listening_ports if p.get("port") is not None}
    if not listening_by_port:
        return []

    mismatches: list[dict] = []
    for risky in risky_rules:
        rule = risky.get("rule") or {}
        port_min = rule.get("port_range_min")
        port_max = rule.get("port_range_max")

        if port_min is None:
            # No port restriction at all -- "all protocols and ports open
            # to the world" per _risk_reason -- so every listening port on
            # this host falls inside it.
            matched_ports = sorted(listening_by_port)
        else:
            hi = port_max if port_max is not None else port_min
            matched_ports = sorted(p for p in listening_by_port if port_min <= p <= hi)

        for port in matched_ports:
            proc = listening_by_port[port]
            mismatches.append({
                "port": port,
                "protocol": proc.get("protocol"),
                "process": proc.get("process"),
                "security_group": risky.get("security_group"),
                "reason": risky.get("reason"),
            })

    # Stable, deterministic order (worst/lowest port number first) rather
    # than "whatever order risky_rules happened to be in" -- same reason
    # cve_feed._check_cve_match picks a single `worst` deterministically.
    mismatches.sort(key=lambda m: m["port"])
    return mismatches
