"""Tests for app/services/exposed_ports.py -- Phase Sec-5a's node-scope
exposed-port cross-check. `find_exposed_port_mismatches` is pure (no
network calls), so these tests exercise it directly with hand-built
listening-port/risky-rule fixtures, the same style
test_security_agent.py::test_version_lt_handles_common_debian_style_
suffixes / test_match_cves_only_flags_known_packages_below_fixed_version
already use for cve_feed.py's own pure functions.
"""
from app.services import exposed_ports


def _risky_rule(security_group="default", reason="SSH (port 22) open to the world", port_min=22, port_max=22):
    return {
        "security_group": security_group,
        "reason": reason,
        "rule": {
            "id": "r1", "security_group_id": "sg1", "direction": "ingress", "ethertype": "IPv4",
            "protocol": "tcp", "port_range_min": port_min, "port_range_max": port_max,
            "remote_ip_prefix": "0.0.0.0/0",
        },
    }


def test_no_mismatches_when_nothing_is_listening():
    assert exposed_ports.find_exposed_port_mismatches([], [_risky_rule()]) == []


def test_no_mismatches_when_no_risky_rules_exist():
    listening = [{"port": 22, "protocol": "tcp", "process": "sshd"}]
    assert exposed_ports.find_exposed_port_mismatches(listening, []) == []


def test_confirmed_mismatch_when_listening_port_falls_inside_risky_rule_range():
    listening = [{"port": 22, "protocol": "tcp", "process": "sshd"}]
    mismatches = exposed_ports.find_exposed_port_mismatches(listening, [_risky_rule()])
    assert len(mismatches) == 1
    assert mismatches[0]["port"] == 22
    assert mismatches[0]["process"] == "sshd"
    assert mismatches[0]["security_group"] == "default"


def test_no_mismatch_when_listening_port_falls_outside_risky_rule_range():
    listening = [{"port": 8080, "protocol": "tcp", "process": "app"}]
    assert exposed_ports.find_exposed_port_mismatches(listening, [_risky_rule()]) == []


def test_all_ports_open_rule_matches_every_listening_port():
    """port_range_min/max both None -- security_audit._risk_reason's
    "all protocols and ports open to the world" case -- should match
    every currently-listening port, not just a specific range."""
    listening = [
        {"port": 22, "protocol": "tcp", "process": "sshd"},
        {"port": 3306, "protocol": "tcp", "process": "mysqld"},
    ]
    all_open = _risky_rule(reason="all protocols and ports open to the world", port_min=None, port_max=None)
    mismatches = exposed_ports.find_exposed_port_mismatches(listening, [all_open])
    assert {m["port"] for m in mismatches} == {22, 3306}


def test_multiple_risky_rules_each_matched_against_the_same_listening_set():
    listening = [
        {"port": 22, "protocol": "tcp", "process": "sshd"},
        {"port": 3306, "protocol": "tcp", "process": "mysqld"},
    ]
    ssh_rule = _risky_rule(security_group="default", reason="SSH (port 22) open to the world", port_min=22, port_max=22)
    mysql_rule = _risky_rule(security_group="database", reason="MySQL (port 3306) open to the world", port_min=3306, port_max=3306)
    mismatches = exposed_ports.find_exposed_port_mismatches(listening, [ssh_rule, mysql_rule])
    assert sorted(m["port"] for m in mismatches) == [22, 3306]
    groups = {m["port"]: m["security_group"] for m in mismatches}
    assert groups[22] == "default"
    assert groups[3306] == "database"


def test_results_are_sorted_by_port_deterministically():
    listening = [
        {"port": 3306, "protocol": "tcp", "process": "mysqld"},
        {"port": 22, "protocol": "tcp", "process": "sshd"},
    ]
    all_open = _risky_rule(port_min=None, port_max=None)
    mismatches = exposed_ports.find_exposed_port_mismatches(listening, [all_open])
    assert [m["port"] for m in mismatches] == [22, 3306]
