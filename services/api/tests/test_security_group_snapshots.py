"""
services/api/tests/test_security_group_snapshots.py

Phase Sec-1: `_check_sec_group_diff` (agents/nodes/security.py) should
answer "did this change since we last looked", not just "does this look
risky right now" (services/security_audit.py's `_risk_reason`). These
tests cover the three new pieces that make that true:

  * security_audit.list_security_groups_by_hostname -- one-pass listing
    across every host, bucketed correctly.
  * security_audit.diff_security_groups -- the pure added/removed-rule
    diff against a stored snapshot.
  * security_snapshot_builder.capture_security_group_snapshots -- the
    periodic job that actually populates `security_group_snapshots`.
  * crud.get_latest_security_group_snapshots -- picks the newest row per
    security group.
  * agents/nodes/security.py's `_check_sec_group_diff` wired end to end,
    including the acceptance criterion from the roadmap: a rule added
    between two snapshot runs shows up as drift even when it isn't
    individually "risky" by the static baseline.

In-memory SQLite DB (same pattern test_baseline_builder.py/
test_topology_sync.py already use) -- no live Postgres/OpenStack needed.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import crud, models, schemas
from app.services import security_audit, security_snapshot_builder


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _rule(rule_id, sg_id="sg1", port=22, cidr="0.0.0.0/0", direction="ingress"):
    return {
        "id": rule_id, "security_group_id": sg_id, "direction": direction,
        "ethertype": "IPv4", "protocol": "tcp",
        "port_range_min": port, "port_range_max": port,
        "remote_ip_prefix": cidr,
    }


def _snapshot(db, hostname, sg_id, sg_name, rules, captured_at):
    row = models.SecurityGroupSnapshot(
        hostname=hostname, security_group_id=sg_id, security_group_name=sg_name,
        rules=rules, captured_at=captured_at,
    )
    db.add(row)
    db.commit()
    return row


# --------------------------------------------------------------------------
# security_audit.list_security_groups_by_hostname
# --------------------------------------------------------------------------

class _FakeConn:
    """Minimal stand-in for an openstacksdk Connection exposing exactly the
    four list calls list_security_groups_by_hostname makes."""

    def __init__(self, servers, ports, sgs, rules):
        self.compute = SimpleNamespace(servers=lambda: servers)
        self.network = SimpleNamespace(
            ports=lambda: ports,
            security_groups=lambda: sgs,
            security_group_rules=lambda: rules,
        )


def test_list_security_groups_by_hostname_buckets_by_host():
    servers = [SimpleNamespace(id="srv-1", hypervisor_hostname="host-a"),
               SimpleNamespace(id="srv-2", hypervisor_hostname="host-b")]
    ports = [SimpleNamespace(device_id="srv-1", security_group_ids=["sg1"]),
             SimpleNamespace(device_id="srv-2", security_group_ids=["sg2"])]
    sgs = [SimpleNamespace(id="sg1", name="default"), SimpleNamespace(id="sg2", name="web")]
    rules = [SimpleNamespace(id="r1", security_group_id="sg1", direction="ingress",
                              ethertype="IPv4", protocol="tcp", port_range_min=22,
                              port_range_max=22, remote_ip_prefix="0.0.0.0/0")]
    conn = _FakeConn(servers, ports, sgs, rules)

    result = security_audit.list_security_groups_by_hostname(conn=conn)

    assert set(result.keys()) == {"host-a", "host-b"}
    assert result["host-a"] == [{"id": "sg1", "name": "default", "rules": [
        {"id": "r1", "security_group_id": "sg1", "direction": "ingress", "ethertype": "IPv4",
         "protocol": "tcp", "port_range_min": 22, "port_range_max": 22, "remote_ip_prefix": "0.0.0.0/0"},
    ]}]
    assert result["host-b"] == [{"id": "sg2", "name": "web", "rules": []}]


def test_list_security_groups_by_hostname_skips_ports_with_no_matching_server():
    # A port whose device_id doesn't map to any listed server (e.g. a
    # router interface port, or a stale port from a deleted instance)
    # shouldn't blow up or show up under some bogus hostname.
    ports = [SimpleNamespace(device_id="unknown-device", security_group_ids=["sg1"])]
    conn = _FakeConn([], ports, [], [])

    result = security_audit.list_security_groups_by_hostname(conn=conn)

    assert result == {}


# --------------------------------------------------------------------------
# security_audit.diff_security_groups
# --------------------------------------------------------------------------

def test_diff_security_groups_skips_a_group_with_no_prior_snapshot():
    current = [{"id": "sg1", "name": "default", "rules": [_rule("r1")]}]
    entries = security_audit.diff_security_groups(current, previous_by_sg_id={})
    assert entries == []


def test_diff_security_groups_reports_added_rule_even_when_not_risky():
    """The Phase Sec-1 acceptance criterion: a newly-opened internal-only
    port (not world-open, so _risk_reason has nothing to say about it)
    still shows up as drift as long as it's new since the last snapshot.
    """
    old_rule = _rule("r1", port=22)  # world-open SSH, present at both times
    new_internal_rule = _rule("r2", port=8080, cidr="10.0.0.0/16")  # new, not risky

    snapshot = SimpleNamespace(rules=[old_rule], captured_at=datetime(2026, 1, 1, 12, 0))
    current = [{"id": "sg1", "name": "default", "rules": [old_rule, new_internal_rule]}]

    entries = security_audit.diff_security_groups(current, previous_by_sg_id={"sg1": snapshot})

    assert len(entries) == 1
    assert entries[0]["security_group"] == "default"
    assert [r["id"] for r in entries[0]["added_rules"]] == ["r2"]
    assert entries[0]["removed_rules"] == []
    assert entries[0]["previous_captured_at"] == "2026-01-01T12:00:00"


def test_diff_security_groups_reports_removed_rule():
    old_rule = _rule("r1")
    snapshot = SimpleNamespace(rules=[old_rule], captured_at=datetime(2026, 1, 1))
    current = [{"id": "sg1", "name": "default", "rules": []}]

    entries = security_audit.diff_security_groups(current, previous_by_sg_id={"sg1": snapshot})

    assert len(entries) == 1
    assert entries[0]["added_rules"] == []
    assert [r["id"] for r in entries[0]["removed_rules"]] == ["r1"]


def test_diff_security_groups_no_entry_when_nothing_changed():
    rule = _rule("r1")
    snapshot = SimpleNamespace(rules=[rule], captured_at=datetime(2026, 1, 1))
    current = [{"id": "sg1", "name": "default", "rules": [rule]}]

    entries = security_audit.diff_security_groups(current, previous_by_sg_id={"sg1": snapshot})

    assert entries == []


# --------------------------------------------------------------------------
# crud.get_latest_security_group_snapshots
# --------------------------------------------------------------------------

def test_get_latest_security_group_snapshots_picks_newest_per_group(db):
    older = datetime(2026, 1, 1, 10, 0)
    newer = datetime(2026, 1, 2, 10, 0)
    _snapshot(db, "host-a", "sg1", "default", [_rule("r1")], older)
    newest_row = _snapshot(db, "host-a", "sg1", "default", [_rule("r1"), _rule("r2")], newer)
    _snapshot(db, "host-a", "sg2", "web", [_rule("r3", sg_id="sg2")], older)
    _snapshot(db, "host-b", "sg1", "default", [_rule("r9")], newer)  # different host, ignored

    result = crud.get_latest_security_group_snapshots(db, "host-a")

    assert set(result.keys()) == {"sg1", "sg2"}
    assert result["sg1"].id == newest_row.id
    assert len(result["sg1"].rules) == 2


def test_get_latest_security_group_snapshots_empty_when_none_taken_yet(db):
    assert crud.get_latest_security_group_snapshots(db, "host-a") == {}


# --------------------------------------------------------------------------
# security_snapshot_builder.capture_security_group_snapshots
# --------------------------------------------------------------------------

def test_capture_security_group_snapshots_writes_one_row_per_group(db, monkeypatch):
    crud.create_node(db, schemas.NodeCreate(hostname="host-a", ip_address="10.0.1.21", role="compute"))

    monkeypatch.setattr(security_audit, "_connect", lambda: object())
    monkeypatch.setattr(security_audit, "list_security_groups_by_hostname", lambda conn=None: {
        "host-a": [
            {"id": "sg1", "name": "default", "rules": [_rule("r1")]},
            {"id": "sg2", "name": "web", "rules": [_rule("r2", sg_id="sg2")]},
        ],
    })

    rows_written = security_snapshot_builder.capture_security_group_snapshots(db)

    assert rows_written == 2
    stored = db.query(models.SecurityGroupSnapshot).all()
    assert {(r.hostname, r.security_group_id) for r in stored} == {("host-a", "sg1"), ("host-a", "sg2")}


def test_capture_security_group_snapshots_skips_hosts_that_arent_known_nodes(db, monkeypatch):
    # No Node rows registered for "host-a" at all -- if there ARE other
    # known nodes, an OpenStack hostname Cortex hasn't seeded yet should
    # be skipped rather than snapshotted under a hostname nothing else
    # recognizes.
    crud.create_node(db, schemas.NodeCreate(hostname="host-known", ip_address="10.0.1.22", role="compute"))

    monkeypatch.setattr(security_audit, "_connect", lambda: object())
    monkeypatch.setattr(security_audit, "list_security_groups_by_hostname", lambda conn=None: {
        "host-a": [{"id": "sg1", "name": "default", "rules": [_rule("r1")]}],
    })

    rows_written = security_snapshot_builder.capture_security_group_snapshots(db)

    assert rows_written == 0
    assert db.query(models.SecurityGroupSnapshot).count() == 0


def test_capture_security_group_snapshots_snapshots_everything_when_no_nodes_registered_yet(db, monkeypatch):
    # A completely fresh, unseeded database: don't filter down to nothing.
    monkeypatch.setattr(security_audit, "_connect", lambda: object())
    monkeypatch.setattr(security_audit, "list_security_groups_by_hostname", lambda conn=None: {
        "host-a": [{"id": "sg1", "name": "default", "rules": [_rule("r1")]}],
    })

    rows_written = security_snapshot_builder.capture_security_group_snapshots(db)

    assert rows_written == 1


def test_capture_security_group_snapshots_returns_zero_on_connect_failure(db, monkeypatch):
    def _boom():
        raise RuntimeError("no route to openstack-sim")
    monkeypatch.setattr(security_audit, "_connect", lambda: _boom())

    rows_written = security_snapshot_builder.capture_security_group_snapshots(db)

    assert rows_written == 0


# --------------------------------------------------------------------------
# End-to-end through agents/nodes/security.py's _check_sec_group_diff
# --------------------------------------------------------------------------

def test_check_sec_group_diff_reports_drift_alongside_risk(monkeypatch):
    """Acceptance criterion, exercised through the actual sub-check
    function the Security Agent calls, not just diff_security_groups
    directly."""
    from app.agents.nodes import security

    old_rule = _rule("r1", port=22)  # world-open SSH -- risky *and* unchanged
    new_internal_rule = _rule("r2", port=8080, cidr="10.0.0.0/16")  # new, not risky

    monkeypatch.setattr(security.security_audit, "get_node_security_groups", lambda hostname, conn=None: {
        "hostname": hostname,
        "security_groups": [{"id": "sg1", "name": "default", "rules": [old_rule, new_internal_rule]}],
        "risky_rules": [{"security_group": "default", "rule": old_rule, "reason": "SSH (port 22) open to the world"}],
    })
    snapshot = SimpleNamespace(rules=[old_rule], captured_at=datetime(2026, 1, 1, 9, 0))
    monkeypatch.setattr(security.crud, "get_latest_security_group_snapshots", lambda db, hostname: {"sg1": snapshot})

    node = {"hostname": "host-a", "role": "compute", "instance": "10.0.1.21:9100"}
    result = security._check_sec_group_diff(node)

    assert result["has_signal"] is True
    assert len(result["risky_rules"]) == 1
    assert len(result["drift"]) == 1
    assert [r["id"] for r in result["drift"][0]["added_rules"]] == ["r2"]
    assert "SSH" in result["detail"]
    assert "drift" in result["detail"].lower()


def test_check_sec_group_diff_signals_on_drift_alone_with_no_risky_rules(monkeypatch):
    """The other half of the acceptance criterion: drift by itself (no
    static-baseline risk at all) is still enough to flag has_signal."""
    from app.agents.nodes import security

    old_rule = _rule("r1", port=22, cidr="0.0.0.0/0")

    monkeypatch.setattr(security.security_audit, "get_node_security_groups", lambda hostname, conn=None: {
        "hostname": hostname,
        "security_groups": [{"id": "sg1", "name": "default", "rules": []}],  # rule was removed
        "risky_rules": [],  # nothing risky left
    })
    snapshot = SimpleNamespace(rules=[old_rule], captured_at=datetime(2026, 1, 1))
    monkeypatch.setattr(security.crud, "get_latest_security_group_snapshots", lambda db, hostname: {"sg1": snapshot})

    node = {"hostname": "host-a", "role": "compute", "instance": "10.0.1.21:9100"}
    result = security._check_sec_group_diff(node)

    assert result["has_signal"] is True
    assert result["risky_rules"] == []
    assert len(result["drift"]) == 1
    assert [r["id"] for r in result["drift"][0]["removed_rules"]] == ["r1"]


def test_check_sec_group_diff_no_signal_when_nothing_changed_and_nothing_risky(monkeypatch):
    from app.agents.nodes import security

    rule = _rule("r1", port=8080, cidr="10.0.0.0/16")
    monkeypatch.setattr(security.security_audit, "get_node_security_groups", lambda hostname, conn=None: {
        "hostname": hostname,
        "security_groups": [{"id": "sg1", "name": "default", "rules": [rule]}],
        "risky_rules": [],
    })
    snapshot = SimpleNamespace(rules=[rule], captured_at=datetime(2026, 1, 1))
    monkeypatch.setattr(security.crud, "get_latest_security_group_snapshots", lambda db, hostname: {"sg1": snapshot})

    node = {"hostname": "host-a", "role": "compute", "instance": "10.0.1.21:9100"}
    result = security._check_sec_group_diff(node)

    assert result["has_signal"] is False
    assert result["drift"] == []
    assert "no drift" in result["detail"].lower()


def test_check_sec_group_diff_degrades_to_no_drift_on_snapshot_read_failure(monkeypatch):
    """A Postgres hiccup reading the snapshot table shouldn't fail the
    whole sub-check -- the static risk read doesn't depend on it."""
    from app.agents.nodes import security

    rule = _rule("r1")
    monkeypatch.setattr(security.security_audit, "get_node_security_groups", lambda hostname, conn=None: {
        "hostname": hostname,
        "security_groups": [{"id": "sg1", "name": "default", "rules": [rule]}],
        "risky_rules": [{"security_group": "default", "rule": rule, "reason": "SSH (port 22) open to the world"}],
    })

    def _boom(db, hostname):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(security.crud, "get_latest_security_group_snapshots", _boom)

    node = {"hostname": "host-a", "role": "compute", "instance": "10.0.1.21:9100"}
    result = security._check_sec_group_diff(node)

    assert result["has_signal"] is True  # still catches the static risk
    assert result["drift"] == []
