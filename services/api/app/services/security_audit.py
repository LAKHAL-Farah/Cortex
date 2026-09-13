"""Neutron security-group audit for the Security Agent's sec-group-diff
sub-check (v0.9, agents/nodes/security.py) -- deliberately its own module
rather than added to network_health.py, which says explicitly in its own
docstring that security groups are "deliberately not modeled here". Same
`openstack.connect(cloud=OS_CLOUD)` / own `_connect()` pattern as that
module and quota_budget_monitor.py, for the same reason both of those give:
an on-demand security question shouldn't block on, or be blocked by, the
periodic topology sync loop.

"diff" here originally meant only against a small built-in baseline of
what "overly permissive" looks like (any world-open ingress rule --
0.0.0.0/0 or ::/0 -- especially onto a sensitive port like SSH/RDP/a
database port, or a rule with no protocol/port restriction at all) --
`_risk_reason`/`get_node_security_groups` below still is exactly that,
"does this look risky right now" regardless of history.

Phase Sec-1 added the other half: `list_security_groups_by_hostname` +
`diff_security_groups` below, backed by the `security_group_snapshots`
table (models.SecurityGroupSnapshot) and populated by
security_snapshot_builder.py's periodic pass, answer "did this change
since we last looked" -- a rule can be flagged here even when it was
never risky enough to trip `_risk_reason` at all (e.g. a newly-opened
internal-only port).

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
    with each group's own rules, which of those rules look overly
    permissive by `_risk_reason`'s baseline, and -- since a group here is
    already a union across every VM scheduled onto this hypervisor, see
    this function's own callers -- which of those instances (by name)
    actually carry it, so a caller isn't left guessing whether a group
    belongs to one VM or all of them. Scoped by `server.hypervisor_
    hostname`, same field network_health.py's Phase C instance reads
    already key on."""
    conn = conn or _connect()

    all_servers = list(conn.compute.servers())
    hosted_names = {
        s.id: (getattr(s, "name", None) or s.id)
        for s in all_servers
        if getattr(s, "hypervisor_hostname", None) == hostname
    }

    all_ports = list(conn.network.ports())
    sg_instances: dict[str, set[str]] = defaultdict(set)
    for port in all_ports:
        instance_name = hosted_names.get(getattr(port, "device_id", None))
        if instance_name is None:
            continue
        for sg_id in getattr(port, "security_group_ids", None) or []:
            sg_instances[sg_id].add(instance_name)

    all_rules = list(conn.network.security_group_rules())
    rules_by_sg: dict[str, list] = defaultdict(list)
    for rule in all_rules:
        rules_by_sg[getattr(rule, "security_group_id", None)].append(rule)

    all_sgs = {sg.id: sg for sg in conn.network.security_groups()}

    groups: list[dict] = []
    risky_rules: list[dict] = []
    for sg_id in sorted(sg_instances):
        sg = all_sgs.get(sg_id)
        if sg is None:
            continue
        sg_name = getattr(sg, "name", None) or sg_id
        rule_dicts = [_rule_to_dict(r) for r in rules_by_sg.get(sg_id, [])]
        groups.append({
            "id": sg_id,
            "name": sg_name,
            "rules": rule_dicts,
            "instances": sorted(sg_instances[sg_id]),
        })
        for rule_dict in rule_dicts:
            reason = _risk_reason(rule_dict)
            if reason:
                risky_rules.append({"security_group": sg_name, "rule": rule_dict, "reason": reason})

    return {"hostname": hostname, "security_groups": groups, "risky_rules": risky_rules}


def list_security_groups_by_hostname(conn=None) -> dict[str, list[dict]]:
    """Same full-listing approach as `get_node_security_groups`, but in one
    pass across every hosted instance on every hypervisor node instead of
    one hostname at a time.

    Added for Phase Sec-1's periodic snapshot job
    (security_snapshot_builder.py): a pass that wants every node's
    security groups in the same run shouldn't re-list
    conn.compute.servers()/conn.network.ports()/conn.network.
    security_group_rules() once per node the way N calls to
    get_node_security_groups() would -- one listing pass, bucketed by
    hostname, same tradeoff topology_sync.py's own single-pass syncs
    already make.
    """
    conn = conn or _connect()

    all_servers = list(conn.compute.servers())
    hostname_by_server_id = {s.id: getattr(s, "hypervisor_hostname", None) for s in all_servers}

    all_ports = list(conn.network.ports())
    sg_ids_by_host: dict[str, set[str]] = defaultdict(set)
    for port in all_ports:
        hostname = hostname_by_server_id.get(getattr(port, "device_id", None))
        if hostname is None:
            continue
        sg_ids_by_host[hostname].update(getattr(port, "security_group_ids", None) or [])

    all_rules = list(conn.network.security_group_rules())
    rules_by_sg: dict[str, list] = defaultdict(list)
    for rule in all_rules:
        rules_by_sg[getattr(rule, "security_group_id", None)].append(rule)

    all_sgs = {sg.id: sg for sg in conn.network.security_groups()}

    result: dict[str, list[dict]] = {}
    for hostname, sg_ids in sg_ids_by_host.items():
        groups = []
        for sg_id in sorted(sg_ids):
            sg = all_sgs.get(sg_id)
            if sg is None:
                continue
            sg_name = getattr(sg, "name", None) or sg_id
            rule_dicts = [_rule_to_dict(r) for r in rules_by_sg.get(sg_id, [])]
            groups.append({"id": sg_id, "name": sg_name, "rules": rule_dicts})
        result[hostname] = groups
    return result


def diff_security_groups(current_groups: list[dict], previous_by_sg_id: dict) -> list[dict]:
    """Phase Sec-1's actual drift diff: compares this pass's live
    `get_node_security_groups()` groups against the most recent stored
    `security_group_snapshots` row per security group (see
    crud.get_latest_security_group_snapshots), complementing
    `_risk_reason`'s static "does this look risky right now" baseline
    with "did this change since <timestamp>".

    A rule can show up here even when `_risk_reason` has nothing to say
    about it at all -- e.g. a newly-opened internal-only port that was
    never world-open and so never trips the static baseline, but is still
    a real configuration change worth surfacing (the acceptance criterion
    Phase Sec-1 was written to satisfy).

    Rules are matched by their own Neutron-assigned `id` (stable across
    reads of the same rule, unlike comparing rule *contents*, which would
    treat two coincidentally-identical rules on different security groups
    as "the same rule"). `previous_by_sg_id` maps security_group_id ->
    the models.SecurityGroupSnapshot row for that group; a group with no
    entry there has nothing to diff against yet and is simply skipped --
    "no prior snapshot" is not the same claim as "no change", so it must
    never be reported as either.
    """
    entries = []
    for group in current_groups:
        sg_id = group["id"]
        snapshot = previous_by_sg_id.get(sg_id)
        if snapshot is None:
            continue

        current_by_id = {r["id"]: r for r in group["rules"] if r.get("id")}
        previous_by_id = {r["id"]: r for r in (snapshot.rules or []) if r.get("id")}

        added_ids = current_by_id.keys() - previous_by_id.keys()
        removed_ids = previous_by_id.keys() - current_by_id.keys()
        if not added_ids and not removed_ids:
            continue

        entries.append({
            "security_group": group["name"],
            "security_group_id": sg_id,
            "previous_captured_at": snapshot.captured_at.isoformat(),
            "added_rules": [current_by_id[rid] for rid in sorted(added_ids)],
            "removed_rules": [previous_by_id[rid] for rid in sorted(removed_ids)],
        })
    return entries
