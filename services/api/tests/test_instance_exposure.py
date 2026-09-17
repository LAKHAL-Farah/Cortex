"""Tests for app/services/instance_exposure.py -- Phase Sec-5b's
instance-scope exposed-port check (external reachability probe against a
VM's floating/fixed IP).

`_declared_open_ports` and `probe_declared_open_ports` are exercised
directly: the former is pure (no I/O at all), the latter's only I/O is
`_probe_one`'s `socket.create_connection` call, monkeypatched here the
same way network_latency.py's own tests presumably stub `socket.
create_connection` for `_measure_one` -- no real network access needed to
test the confirmed/unconfirmed classification logic itself.

`get_instance_exposure_targets` is exercised with `graph_db.
fetch_instance_reachability` monkeypatched (its own read path is covered
directly in test_graph_db_instance_reachability.py) and a `_FakeConn`
stand-in for the one remaining live OpenStack SDK read -- security-group
rules, which the topology graph has no source for at all. Same
SimpleNamespace-resource faking style test_network_health.py already
uses for `conn.network`/`conn.compute`.
"""
import types

from app import graph_db
from app.services import instance_exposure


def _rule(port_min=22, port_max=22, remote="0.0.0.0/0", direction="ingress", protocol="tcp"):
    return {
        "id": "r1", "security_group_id": "sg1", "direction": direction, "ethertype": "IPv4",
        "protocol": protocol, "port_range_min": port_min, "port_range_max": port_max,
        "remote_ip_prefix": remote,
    }


# --------------------------------------------------------------------
# _declared_open_ports -- pure, no I/O
# --------------------------------------------------------------------

def test_declared_open_ports_expands_a_single_port_rule():
    declared = instance_exposure._declared_open_ports([_rule(port_min=22, port_max=22)])
    assert [d["port"] for d in declared] == [22]


def test_declared_open_ports_expands_a_port_range():
    declared = instance_exposure._declared_open_ports([_rule(port_min=20, port_max=22)])
    assert sorted(d["port"] for d in declared) == [20, 21, 22]


def test_declared_open_ports_ignores_non_risky_rules():
    # Scoped to a private CIDR -- not world-open, so _risk_reason has
    # nothing to say about it (see security_audit._risk_reason).
    scoped_rule = _rule(remote="10.0.0.0/16")
    assert instance_exposure._declared_open_ports([scoped_rule]) == []


def test_declared_open_ports_ignores_egress_rules():
    egress_rule = _rule(direction="egress")
    assert instance_exposure._declared_open_ports([egress_rule]) == []


def test_declared_open_ports_reports_unscoped_all_ports_rule_with_none_port():
    all_open = _rule(port_min=None, port_max=None, protocol=None)
    declared = instance_exposure._declared_open_ports([all_open])
    assert len(declared) == 1
    assert declared[0]["port"] is None


# --------------------------------------------------------------------
# get_instance_exposure_targets -- IP resolution from the (faked)
# topology graph, security-group rules from a (faked) live SDK conn
# --------------------------------------------------------------------

def _sg_rule_obj(id="r1", security_group_id="sg1", port_min=22, port_max=22, remote="0.0.0.0/0"):
    return types.SimpleNamespace(
        id=id, security_group_id=security_group_id, direction="ingress", ethertype="IPv4",
        protocol="tcp", port_range_min=port_min, port_range_max=port_max, remote_ip_prefix=remote,
    )


def _port_obj(id, security_group_ids=None):
    return types.SimpleNamespace(id=id, security_group_ids=security_group_ids or [])


class _FakeConn:
    """Stands in for the one remaining live OpenStack SDK read this
    function makes -- `conn.network.ports()`/`conn.network.
    security_group_rules()` for security-group data, which (unlike IP
    resolution) has no topology-graph source at all."""

    def __init__(self, ports=None, rules=None):
        self._ports = ports or []
        self._rules = rules or []
        self.network = types.SimpleNamespace(
            ports=lambda **kw: iter(self._ports),
            security_group_rules=lambda **kw: iter(self._rules),
        )


def _facts(port_ids=None, fixed_ips=None, floating_ip=None, name="sandbox-vm-1", hostname="compute1-sim"):
    return {
        "instance_id": "vm-1",
        "instance_name": name,
        "hypervisor_hostname": hostname,
        "port_ids": port_ids or [],
        "fixed_ip_addresses": fixed_ips or [],
        "floating_ip_address": floating_ip,
    }


def test_unresolved_instance_reports_no_reachable_ip_without_touching_the_sdk(monkeypatch):
    # Instance not (yet) synced into the topology graph at all -- must
    # degrade honestly, not fall back to a live Nova/Neutron listing.
    monkeypatch.setattr(graph_db, "fetch_instance_reachability", lambda instance_id: None)
    conn_calls: list[str] = []
    conn = _FakeConn()
    conn.network.ports = lambda **kw: conn_calls.append("ports") or iter([])

    result = instance_exposure.get_instance_exposure_targets("vm-unknown", conn=conn)

    assert result == {
        "instance_id": "vm-unknown",
        "instance_name": None,
        "reachable_ip": None,
        "reachable_via": None,
        "declared_open": [],
    }
    assert conn_calls == []  # never touched the SDK at all


def test_prefers_floating_ip_over_fixed_ip_from_topology(monkeypatch):
    monkeypatch.setattr(
        graph_db, "fetch_instance_reachability",
        lambda instance_id: _facts(port_ids=["port-1"], fixed_ips=["10.0.1.101"], floating_ip="203.0.113.50"),
    )
    conn = _FakeConn(
        ports=[_port_obj("port-1", security_group_ids=["sg1"])],
        rules=[_sg_rule_obj()],
    )

    result = instance_exposure.get_instance_exposure_targets("vm-1", conn=conn)

    assert result["instance_name"] == "sandbox-vm-1"
    assert result["reachable_ip"] == "203.0.113.50"
    assert result["reachable_via"] == "floating_ip"
    assert [d["port"] for d in result["declared_open"]] == [22]


def test_falls_back_to_fixed_ip_when_no_floating_ip_associated(monkeypatch):
    monkeypatch.setattr(
        graph_db, "fetch_instance_reachability",
        lambda instance_id: _facts(port_ids=["port-1"], fixed_ips=["10.0.1.101"], floating_ip=None),
    )
    conn = _FakeConn(ports=[_port_obj("port-1")], rules=[])

    result = instance_exposure.get_instance_exposure_targets("vm-1", conn=conn)

    assert result["reachable_ip"] == "10.0.1.101"
    assert result["reachable_via"] == "fixed_ip"


def test_only_this_instances_own_port_ids_contribute_security_groups(monkeypatch):
    # A second instance's port ("port-other") sits in the same SDK
    # listing (openstack-sim's list-only surface returns every port,
    # every call) but must not leak its security group into this
    # instance's declared-open set -- the topology graph's port_ids is
    # what scopes the filter, not device_id re-derived from the SDK.
    monkeypatch.setattr(
        graph_db, "fetch_instance_reachability",
        lambda instance_id: _facts(port_ids=["port-1"], fixed_ips=["10.0.1.101"], floating_ip=None),
    )
    conn = _FakeConn(
        ports=[
            _port_obj("port-1", security_group_ids=["sg1"]),
            _port_obj("port-other", security_group_ids=["sg-danger"]),
        ],
        rules=[_sg_rule_obj(id="r1", security_group_id="sg1"), _sg_rule_obj(id="r2", security_group_id="sg-danger")],
    )

    result = instance_exposure.get_instance_exposure_targets("vm-1", conn=conn)

    assert {d["rule"]["security_group_id"] for d in result["declared_open"]} == {"sg1"}


def test_no_ports_in_topology_skips_the_sdk_call_entirely(monkeypatch):
    monkeypatch.setattr(
        graph_db, "fetch_instance_reachability",
        lambda instance_id: _facts(port_ids=[], fixed_ips=[], floating_ip=None),
    )
    conn_calls: list[str] = []
    conn = _FakeConn()
    conn.network.ports = lambda **kw: conn_calls.append("ports") or iter([])

    result = instance_exposure.get_instance_exposure_targets("vm-1", conn=conn)

    assert result["reachable_ip"] is None
    assert result["declared_open"] == []
    assert conn_calls == []


# --------------------------------------------------------------------
# probe_declared_open_ports -- socket I/O stubbed out
# --------------------------------------------------------------------

def test_confirmed_when_probe_succeeds(monkeypatch):
    monkeypatch.setattr(instance_exposure, "_probe_one", lambda ip, port: port == 22)
    declared = [{"port": 22, "reason": "SSH (port 22) open to the world", "rule": _rule()}]

    result = instance_exposure.probe_declared_open_ports("203.0.113.10", declared)

    assert result["confirmed"] == [{"port": 22, "reason": "SSH (port 22) open to the world"}]
    assert result["unconfirmed"] == []


def test_unconfirmed_when_probe_fails(monkeypatch):
    monkeypatch.setattr(instance_exposure, "_probe_one", lambda ip, port: False)
    declared = [{"port": 22, "reason": "SSH (port 22) open to the world", "rule": _rule()}]

    result = instance_exposure.probe_declared_open_ports("203.0.113.10", declared)

    assert result["confirmed"] == []
    assert result["unconfirmed"] == [{"port": 22, "reason": "SSH (port 22) open to the world"}]


def test_unscoped_all_ports_rule_is_not_probed_but_is_reported():
    declared = [{"port": None, "reason": "all protocols and ports open to the world", "rule": _rule(port_min=None, port_max=None)}]

    result = instance_exposure.probe_declared_open_ports("203.0.113.10", declared)

    assert result["confirmed"] == []
    assert result["unconfirmed"] == []
    assert result["unscoped_rules"] == [{"reason": "all protocols and ports open to the world"}]


def test_probe_one_returns_false_on_connection_error(monkeypatch):
    def _refused(*a, **k):
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr(instance_exposure.socket, "create_connection", _refused)
    assert instance_exposure._probe_one("203.0.113.10", 22) is False


# --------------------------------------------------------------------
# build_instance_exposure_signal -- combines the two above into the
# has_signal/degraded/detail envelope both router endpoints return
# --------------------------------------------------------------------

def test_signal_unresolved_instance_is_degraded(monkeypatch):
    def _raise(instance_id, conn=None):
        raise RuntimeError("openstack unreachable")

    monkeypatch.setattr(instance_exposure, "get_instance_exposure_targets", _raise)

    signal = instance_exposure.build_instance_exposure_signal("vm-1")

    assert signal["degraded"] is True
    assert signal["has_signal"] is False
    assert signal["instance_id"] == "vm-1"


def test_signal_no_reachable_ip_has_no_signal_but_not_degraded(monkeypatch):
    monkeypatch.setattr(
        instance_exposure, "get_instance_exposure_targets",
        lambda instance_id, conn=None: {
            "instance_id": instance_id, "instance_name": "sandbox-vm-1",
            "reachable_ip": None, "reachable_via": None, "declared_open": [],
        },
    )

    signal = instance_exposure.build_instance_exposure_signal("vm-1")

    assert signal["has_signal"] is False
    assert signal["degraded"] is False
    assert "nothing to probe" in signal["detail"]


def test_signal_confirmed_reachable_port_sets_has_signal(monkeypatch):
    monkeypatch.setattr(
        instance_exposure, "get_instance_exposure_targets",
        lambda instance_id, conn=None: {
            "instance_id": instance_id, "instance_name": "sandbox-vm-1",
            "reachable_ip": "203.0.113.50", "reachable_via": "floating_ip",
            "declared_open": [{"port": 22, "reason": "SSH open to the world", "rule": _rule()}],
        },
    )
    monkeypatch.setattr(instance_exposure, "_probe_one", lambda ip, port: True)

    signal = instance_exposure.build_instance_exposure_signal("vm-1")

    assert signal["has_signal"] is True
    assert signal["reachable_ip"] == "203.0.113.50"
    assert [p["port"] for p in signal["confirmed"]] == [22]


def test_signal_falls_back_to_caller_supplied_name_when_topology_has_none(monkeypatch):
    # The fleet-wide table passes instance_name from graph_db.
    # fetch_all_instances() up front -- if get_instance_exposure_targets
    # itself can't resolve a name, use that instead of showing nothing.
    monkeypatch.setattr(
        instance_exposure, "get_instance_exposure_targets",
        lambda instance_id, conn=None: {
            "instance_id": instance_id, "instance_name": None,
            "reachable_ip": None, "reachable_via": None, "declared_open": [],
        },
    )

    signal = instance_exposure.build_instance_exposure_signal("vm-1", instance_name="sandbox-vm-1")

    assert signal["instance_name"] == "sandbox-vm-1"

