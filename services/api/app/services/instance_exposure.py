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

1. `get_instance_exposure_targets(instance_id, conn=None)` -- an
   OpenStack SDK read (same `security_audit._connect()`/`_rule_to_dict`/
   `_risk_reason` machinery, reused rather than reimplemented) resolving
   one instance's reachable IP (floating IP preferred, since that's what
   an actual outside attacker would connect to; falls back to the first
   fixed IP if there's no floating IP, since a fixed IP on a network with
   an external-gatewayed router -- see network_health.get_
   instance_connectivity -- can still be reachable via SNAT/routing even
   without its own floating IP) and its own security groups' declared
   world-open rules (`security_audit._risk_reason`, the exact same
   baseline Sec-5a cross-checks against, applied to this one instance's
   rules instead of a node's rolled-up set).
2. `probe_declared_open_ports(ip_address, declared_ports, timeout_seconds=2.0)`
   -- a real TCP-connect probe (no mocking, no simulated response baked
   in here) against exactly the ports this instance's own security
   groups already declare open -- not a broad port scan, which would be
   both slow and a genuinely different, more invasive thing than "does
   this declared-open port actually answer".

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
    """
    conn = conn or _connect()

    all_servers = {s.id: s for s in conn.compute.servers()}
    server = all_servers.get(instance_id)

    all_ports = list(conn.network.ports())
    instance_ports = [p for p in all_ports if getattr(p, "device_id", None) == instance_id]

    sg_ids: set[str] = set()
    fixed_ip_addresses: set[str] = set()
    for port in instance_ports:
        sg_ids.update(getattr(port, "security_group_ids", None) or [])
        for fip in getattr(port, "fixed_ips", None) or []:
            addr = fip.get("ip_address") if isinstance(fip, dict) else None
            if addr:
                fixed_ip_addresses.add(addr)

    all_rules = list(conn.network.security_group_rules())
    rule_dicts = [
        security_audit._rule_to_dict(r)
        for r in all_rules
        if getattr(r, "security_group_id", None) in sg_ids
    ]

    floating_ip = next(
        (getattr(f, "floating_ip_address", None) for f in conn.network.ips()
         if getattr(f, "fixed_ip_address", None) in fixed_ip_addresses),
        None,
    )
    reachable_ip = floating_ip or (sorted(fixed_ip_addresses)[0] if fixed_ip_addresses else None)

    return {
        "instance_id": instance_id,
        "instance_name": getattr(server, "name", None) if server is not None else None,
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
