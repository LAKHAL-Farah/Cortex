"""Instance-scope exposed-port check (Phase Sec-5b, explicit stretch --
see the roadmap doc's §7 open question 3: this is flagged as "wanted?"
not "committed", built after Sec-5a and kept independent of it).

Layer: **Instance**, not Node. `services/exposed_ports.py`'s Sec-5a check
answers "what is this *host* listening on" -- a question Cortex can
answer directly, because a `Node` row is a real host Cortex has (a real
or simulated) collector for. A guest VM's own network stack is not
reachable that way at all: there is no `ss -tlnp` you can run against a
VM from its hypervisor without going through the guest OS, and this
project deliberately does not deploy an in-guest agent (see this
module's own module-level design note below, and the roadmap doc's
explicit "(b) an in-guest agent... is not recommended as part of this
phase").

So "is this VM exposed" is answered the only honest way available
without guest access: **(a) an external reachability probe** against the
instance's floating/fixed IP, using the exact same TCP-connect timing
technique `network_latency.py` already uses for node-to-node latency,
just aimed at an instance's IP instead of a node's `node_exporter` port.
This tells you what an attacker on the network would actually see --
which is the realistic interpretation of "exposed port" for a VM, and
doesn't require deploying anything into every tenant workload.

Two independent pieces, same split every other module in this package
uses:

1. `get_instance_exposure_targets(instance_id, conn=None)` -- resolves
   one instance's reachable IP (floating IP preferred, since that's what
   an actual outside attacker would connect to; falls back to the first
   fixed IP if there's no floating IP, since a fixed IP on a network with
   an external-gatewayed router -- see network_health.get_
   instance_connectivity -- can still be reachable via SNAT/routing even
   without its own floating IP) and its own security groups' declared
   world-open rules (`security_audit._risk_reason`, the exact same
   baseline Sec-5a cross-checks against, applied to this one instance's
   rules instead of a node's rolled-up set).

   The IP-resolution half of this (which ports this instance owns, their
   fixed IPs, and any floating IP already associated with one of them)
   is read straight out of the already-synced topology graph
   (`graph_db.fetch_instance_reachability`, topology_sync.py's Phase 6)
   instead of re-querying Nova/Neutron for facts that graph already has --
   `conn.compute.servers()` + `conn.network.ports()` + `conn.network.ips()`
   used to be listed here a second time, on every single lookup, purely to
   answer a question the topology sync loop had already answered on its
   own schedule. If the instance hasn't been synced into the graph yet
   (or genuinely doesn't exist), this is reported as "can't resolve this
   instance" rather than silently falling back to that duplicate live
   read -- see `graph_db.fetch_instance_reachability`'s own docstring.

   The security-groups half is NOT read from topology, because there is
   nothing there to read: topology_sync.py never syncs security groups
   into the graph at all (a `:Port` vertex has no `security_group_ids`
   property). So this still does one live OpenStack SDK read -- `conn.
   network.ports()` -- but now filters it down to just the port ids the
   topology graph already said belong to this instance, then `conn.
   network.security_group_rules()` for the groups those ports carry, same
   `security_audit._rule_to_dict()`/`_risk_reason()` baseline as before.
2. `probe_declared_open_ports(ip_address, declared_ports, timeout_seconds=2.0)`
   -- a real TCP-connect probe (no mocking, no simulated response baked
   in here) against exactly the ports this instance's own security
   groups already declare open -- not a broad port scan, which would be
   both slow and a genuinely different, more invasive thing than "does
   this declared-open port actually answer".
3. `build_instance_exposure_signal(instance_id, instance_name=None, conn=None)`
   -- combines the two above into the same `has_signal`/`degraded`/
   `detail` envelope every other Security Agent check returns, so
   routers/security.py's single-instance lookup and its fleet-wide
   instance table (every instance from `graph_db.fetch_all_instances()`,
   run concurrently) build identical signals from identical logic rather
   than two copies of the same shaping code.

Honesty about what's real vs. illustrative in an openstack-sim checkout:
both pieces are genuine, unmocked code -- the SDK read against
`security_group_rules()` and the TCP-connect probe both run for real.
What's illustrative is the *target*: openstack-sim's seeded fixed IPs
(10.0.1.10x) aren't real listening hosts on the sandbox's own Docker
network, so a probe against them will honestly report "not reachable"
every time in a fresh sandbox checkout -- the same "real client, real
logic, no real target yet" state cve_feed.py and ebpf_signal.py were in
before Sec-3/Sec-4 stood up their own collectors. Point this at a real
OpenStack deployment's real floating IPs and it probes real, live
reachability.
"""
import logging
import socket
import time

from .. import graph_db
from . import security_audit

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 2.0


def _connect():
    """Same connection helper security_audit.py already defines --
    reused directly rather than duplicated, since this module's OpenStack
    read is the same cloud, same credentials, same `_rule_to_dict`/
    `_risk_reason` baseline, just scoped to one instance's own security
    groups instead of a node's rolled-up set."""
    return security_audit._connect()


def _declared_open_ports(rules: list[dict]) -> list[dict]:
    """Every rule in `rules` (already-normalized dicts, see
    security_audit._rule_to_dict) that `security_audit._risk_reason`
    flags as world-open, reduced to the concrete port(s) it actually
    covers -- a rule with no port restriction at all ("all protocols and
    ports open to the world") has no single port to probe, so it's
    reported separately by the caller rather than expanded into every
    possible port number.
    """
    declared: list[dict] = []
    for rule in rules:
        reason = security_audit._risk_reason(rule)
        if not reason:
            continue
        port_min = rule.get("port_range_min")
        port_max = rule.get("port_range_max")
        if port_min is None:
            declared.append({"port": None, "reason": reason, "rule": rule})
            continue
        hi = port_max if port_max is not None else port_min
        for port in range(port_min, hi + 1):
            declared.append({"port": port, "reason": reason, "rule": rule})
    return declared


def get_instance_exposure_targets(instance_id: str, conn=None) -> dict:
    """One instance's reachable IP (floating preferred, else first fixed)
    and its own declared-world-open rules -- everything
    `probe_declared_open_ports` below needs, resolved in one pass so the
    caller doesn't have to make two round trips to build one finding.

    IP resolution is read from the topology graph (`graph_db.
    fetch_instance_reachability`), not a fresh Nova/Neutron listing --
    see this module's own docstring on why re-querying facts topology_
    sync.py already synced would be a needless duplicate round trip. If
    the graph hasn't synced this instance at all (unknown id, or just not
    reached by a pass yet), that's reported honestly as "unresolved"
    (reachable_ip=None, declared_open=[]) rather than silently falling
    back to a second, live read of the same data.
    """
    facts = graph_db.fetch_instance_reachability(instance_id)
    if facts is None:
        return {
            "instance_id": instance_id,
            "instance_name": None,
            "reachable_ip": None,
            "reachable_via": None,
            "declared_open": [],
        }

    floating_ip = facts["floating_ip_address"]
    fixed_ip_addresses = facts["fixed_ip_addresses"]
    reachable_ip = floating_ip or (fixed_ip_addresses[0] if fixed_ip_addresses else None)

    # Security groups have no topology-graph source at all (see module
    # docstring) -- the one piece that still needs a live OpenStack SDK
    # read, scoped to just this instance's own port ids (from the graph,
    # above) instead of every port in the project.
    port_ids = set(facts["port_ids"])
    rule_dicts: list[dict] = []
    if port_ids:
        conn = conn or _connect()
        all_ports = list(conn.network.ports())
        sg_ids: set[str] = set()
        for port in all_ports:
            if getattr(port, "id", None) in port_ids:
                sg_ids.update(getattr(port, "security_group_ids", None) or [])

        if sg_ids:
            all_rules = list(conn.network.security_group_rules())
            rule_dicts = [
                security_audit._rule_to_dict(r)
                for r in all_rules
                if getattr(r, "security_group_id", None) in sg_ids
            ]

    return {
        "instance_id": instance_id,
        "instance_name": facts["instance_name"],
        "reachable_ip": reachable_ip,
        "reachable_via": "floating_ip" if floating_ip else ("fixed_ip" if reachable_ip else None),
        "declared_open": _declared_open_ports(rule_dicts),
    }


def _probe_one(ip_address: str, port: int) -> bool:
    """A single TCP-connect attempt -- True if something answered within
    CONNECT_TIMEOUT_SECONDS. Same technique network_latency._measure_one
    already uses for node-to-node timing; this module only needs
    reachable/not, not the timing itself, so it's a plain boolean rather
    than that function's richer latency_ms/error payload."""
    try:
        with socket.create_connection((ip_address, port), timeout=CONNECT_TIMEOUT_SECONDS):
            return True
    except Exception:
        return False


def probe_declared_open_ports(ip_address: str, declared_open: list[dict]) -> dict:
    """For every concrete (non-None) port `declared_open` names, actually
    attempts a TCP connection to `ip_address:port` and reports whether it
    answered. `confirmed` entries (rule says open, and it really is) are
    the genuine, realistic "this instance is reachable from the internet"
    finding; `unconfirmed` entries (rule says open, but nothing answered)
    are a real misconfiguration too -- an unnecessarily permissive rule --
    just a lower-priority one, since nothing is actually listening there
    right now.

    Entries with `port: None` (a rule with no port restriction at all)
    can't be individually probed -- collected separately as
    `unscoped_rules` so the caller can still surface "this instance has an
    all-ports-open rule" without pretending a single TCP probe could ever
    confirm or rule that out.
    """
    scoped = [d for d in declared_open if d["port"] is not None]
    unscoped = [d for d in declared_open if d["port"] is None]

    confirmed: list[dict] = []
    unconfirmed: list[dict] = []
    for entry in scoped:
        reachable = _probe_one(ip_address, entry["port"])
        target = {"port": entry["port"], "reason": entry["reason"]}
        (confirmed if reachable else unconfirmed).append(target)

    return {
        "ip_address": ip_address,
        "confirmed": confirmed,
        "unconfirmed": unconfirmed,
        "unscoped_rules": [{"reason": u["reason"]} for u in unscoped],
    }


def build_instance_exposure_signal(instance_id: str, instance_name: str | None = None, conn=None) -> dict:
    """One instance's full Sec-5b signal -- resolve targets, probe them,
    shape the result into the same `has_signal`/`degraded`/`detail`
    envelope every other check in this security module returns, ready
    for `security_rbac.filter_security_response_for_role`.

    Pulled out of routers/security.py so GET /instance-exposed-ports/
    {instance_id} (one instance, on demand) and GET /instance-exposed-
    ports (every instance from topology, for the fleet-wide table) build
    the exact same signal shape from the exact same logic -- the router
    only adds RBAC redaction and, for the list endpoint, running this
    concurrently across instances.

    `instance_name` is an optional override for callers who already know
    it (the list endpoint gets it from `graph_db.fetch_all_instances()`
    and passes it straight through) so this doesn't need a second
    `fetch_instance_reachability` round trip just to redisplay a name the
    caller already had.
    """
    try:
        targets = get_instance_exposure_targets(instance_id, conn=conn)
    except Exception as exc:
        logger.warning("instance-exposed-ports: couldn't resolve targets for %s: %s", instance_id, exc)
        return {
            "instance_id": instance_id,
            "instance_name": instance_name,
            "has_signal": False,
            "degraded": True,
            "detail": "Couldn't reach OpenStack to resolve this instance's IP and security groups.",
        }

    resolved_name = targets["instance_name"] or instance_name

    if targets["reachable_ip"] is None:
        return {
            "instance_id": instance_id,
            "instance_name": resolved_name,
            "has_signal": False,
            "degraded": False,
            "detail": f"No floating or fixed IP found for instance '{instance_id}' -- nothing to probe.",
            "declared_open": targets["declared_open"],
        }

    probe = probe_declared_open_ports(targets["reachable_ip"], targets["declared_open"])
    has_signal = bool(probe["confirmed"])
    if has_signal:
        detail = (
            f"{len(probe['confirmed'])} declared-open port(s) on {targets['reachable_ip']} are "
            "actually reachable from outside this instance's network."
        )
    elif probe["unconfirmed"]:
        detail = (
            f"{len(probe['unconfirmed'])} port(s) are declared open on {targets['reachable_ip']} "
            "but nothing answered when probed -- unconfirmed, not reachable right now."
        )
    else:
        detail = f"No world-open security-group rule for instance '{instance_id}' -- nothing to probe."

    return {
        "has_signal": has_signal,
        "degraded": False,
        "detail": detail,
        "instance_id": instance_id,
        "instance_name": resolved_name,
        "reachable_ip": targets["reachable_ip"],
        "reachable_via": targets["reachable_via"],
        **probe,
    }

