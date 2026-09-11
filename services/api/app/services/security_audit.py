"""Neutron security-group audit for the Security Agent's sec-group-diff
sub-check (v0.9, agents/nodes/security.py) -- deliberately its own module
rather than added to network_health.py, which says explicitly in its own
docstring that security groups are "deliberately not modeled here". Same
`openstack.connect(cloud=OS_CLOUD)` / own `_connect()` pattern as that
module and quota_budget_monitor.py, for the same reason both of those give:
an on-demand security question shouldn't block on, or be blocked by, the
periodic topology sync loop.

"diff" here means against a small built-in baseline of what "overly
permissive" looks like (any world-open ingress rule -- 0.0.0.0/0 or ::/0
-- especially onto a sensitive port like SSH/RDP/a database port, or a
rule with no protocol/port restriction at all), not against a stored
snapshot of this project's own security groups from an earlier point in
time. A real per-project baseline-drift diff (this rule wasn't here
yesterday) would need a stored snapshot table this codebase doesn't have
yet -- worth adding if sec-group-diff needs to catch "something changed"
specifically, rather than "something looks wrong right now", which is
what this catches today.

Same "list everything, filter in Python" rule every other function in
this package follows, forced by openstack-sim's list-only API surface
(see network_health.py's own docstring on this).
"""
import logging
import os
from collections import defaultdict

import openstack

logger = logging.getLogger(__name__)

OS_CLOUD = os.environ.get("OS_CLOUD", "cortex-reader")

# Ingress to any of these ports, world-open, gets flagged regardless of
# protocol restrictions -- the common "oops, left SSH/RDP/a database open
# to the internet" misconfiguration a sec-group audit exists to catch.
_SENSITIVE_PORTS = {
    22: "SSH", 3389: "RDP", 3306: "MySQL", 5432: "PostgreSQL",
    6379: "Redis", 27017: "MongoDB", 9200: "Elasticsearch", 2379: "etcd",
}
_WORLD_OPEN_PREFIXES = {"0.0.0.0/0", "::/0"}


def _connect():
    """Thin wrapper so tests can monkeypatch the connection, same as
    network_health._connect() / topology_sync._connect()."""
    return openstack.connect(cloud=OS_CLOUD)


def _rule_to_dict(rule) -> dict:
    return {
        "id": getattr(rule, "id", None),
        "security_group_id": getattr(rule, "security_group_id", None),
        "direction": getattr(rule, "direction", None),
        "ethertype": getattr(rule, "ethertype", None),
        "protocol": getattr(rule, "protocol", None),
        "port_range_min": getattr(rule, "port_range_min", None),
        "port_range_max": getattr(rule, "port_range_max", None),
        "remote_ip_prefix": getattr(rule, "remote_ip_prefix", None),
    }


def _risk_reason(rule: dict) -> str | None:
    """None if this rule isn't a concern; otherwise a short human reason
    it was flagged. Ingress-only -- an open egress rule isn't the "left a
    door open to the internet" misconfiguration this audit targets."""
    if rule["direction"] != "ingress":
        return None
    remote = rule.get("remote_ip_prefix")
    if remote is not None and remote not in _WORLD_OPEN_PREFIXES:
        return None  # scoped to a specific CIDR, not world-open

    port_min = rule.get("port_range_min")
    port_max = rule.get("port_range_max")
    if rule.get("protocol") is None and port_min is None:
        return "all protocols and ports open to the world"

    if port_min is not None:
        for port, label in _SENSITIVE_PORTS.items():
            if port_min <= port <= (port_max if port_max is not None else port_min):
                return f"{label} (port {port}) open to the world"
    return None


def get_node_security_groups(hostname: str, conn=None) -> dict:
    """Every security group attached to any instance hosted on this node,
    with each group's own rules, plus which of those rules look overly
    permissive by `_risk_reason`'s baseline. Scoped by
    `server.hypervisor_hostname`, same field network_health.py's
    Phase C instance reads already key on."""
    conn = conn or _connect()

    all_servers = list(conn.compute.servers())
    hosted_ids = {s.id for s in all_servers if getattr(s, "hypervisor_hostname", None) == hostname}

    all_ports = list(conn.network.ports())
    sg_ids: set[str] = set()
    for port in all_ports:
        if getattr(port, "device_id", None) in hosted_ids:
            sg_ids.update(getattr(port, "security_group_ids", None) or [])

    all_rules = list(conn.network.security_group_rules())
    rules_by_sg: dict[str, list] = defaultdict(list)
    for rule in all_rules:
        rules_by_sg[getattr(rule, "security_group_id", None)].append(rule)

    all_sgs = {sg.id: sg for sg in conn.network.security_groups()}

    groups: list[dict] = []
    risky_rules: list[dict] = []
    for sg_id in sorted(sg_ids):
        sg = all_sgs.get(sg_id)
        if sg is None:
            continue
        sg_name = getattr(sg, "name", None) or sg_id
        rule_dicts = [_rule_to_dict(r) for r in rules_by_sg.get(sg_id, [])]
        groups.append({"id": sg_id, "name": sg_name, "rules": rule_dicts})
        for rule_dict in rule_dicts:
            reason = _risk_reason(rule_dict)
            if reason:
                risky_rules.append({"security_group": sg_name, "rule": rule_dict, "reason": reason})

    return {"hostname": hostname, "security_groups": groups, "risky_rules": risky_rules}
