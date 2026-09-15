"""Tests for app/services/instance_exposure.py -- Phase Sec-5b's
instance-scope exposed-port check (external reachability probe against a
VM's floating/fixed IP).

`_declared_open_ports` and `probe_declared_open_ports` are exercised
directly: the former is pure (no I/O at all), the latter's only I/O is
`_probe_one`'s `socket.create_connection` call, monkeypatched here the
same way network_latency.py's own tests presumably stub `socket.
create_connection` for `_measure_one` -- no real network access needed to
test the confirmed/unconfirmed classification logic itself.
"""
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
