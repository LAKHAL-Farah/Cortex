"""Tests for routers/security.py's GET /instance-exposed-ports (no path
param) -- the fleet-wide table variant of GET /instance-exposed-ports/
{instance_id}.

Called directly as a plain function rather than through TestClient/
app.main: this endpoint takes no `db: Session = Depends(get_db)` at all
(unlike most of this router), so there's no database to spin up, and
calling the undecorated function object directly is equivalent to
FastAPI calling it -- `@router.get(...)` registers the function in a
route table, it doesn't wrap or replace the function itself. This keeps
these tests independent of test_security_router.py's TestClient/Postgres
setup, and of `graph_db.fetch_all_instances`/`instance_exposure.
build_instance_exposure_signal`'s own internals, which are covered
directly in test_graph_db_instance_reachability.py and
test_instance_exposure.py.
"""
import types

from app.routers import security


def test_lists_every_instance_topology_knows_about(monkeypatch):
    monkeypatch.setattr(
        security.graph_db, "fetch_all_instances",
        lambda: [{"id": "vm-1", "name": "sandbox-vm-1"}, {"id": "vm-2", "name": "sandbox-vm-2"}],
    )
    monkeypatch.setattr(
        security.instance_exposure, "build_instance_exposure_signal",
        lambda instance_id, instance_name=None, conn=None: {
            "has_signal": False, "degraded": False, "detail": "clean",
            "instance_id": instance_id, "instance_name": instance_name,
        },
    )

    result = security.list_instance_exposed_ports(current_user=types.SimpleNamespace(role="admin"))

    assert [row["instance_id"] for row in result["instances"]] == ["vm-1", "vm-2"]
    # Order preserved despite running signals concurrently across instances.
    assert result["instances"][0]["instance_name"] == "sandbox-vm-1"


def test_no_instances_in_topology_returns_an_empty_table(monkeypatch):
    monkeypatch.setattr(security.graph_db, "fetch_all_instances", lambda: [])

    result = security.list_instance_exposed_ports(current_user=types.SimpleNamespace(role="admin"))

    assert result == {"instances": []}


def test_viewer_gets_restricted_rows_not_full_findings(monkeypatch):
    monkeypatch.setattr(
        security.graph_db, "fetch_all_instances",
        lambda: [{"id": "vm-1", "name": "sandbox-vm-1"}],
    )
    monkeypatch.setattr(
        security.instance_exposure, "build_instance_exposure_signal",
        lambda instance_id, instance_name=None, conn=None: {
            "has_signal": True, "degraded": False,
            "detail": "1 declared-open port on 203.0.113.50 is actually reachable.",
            "instance_id": instance_id, "instance_name": instance_name,
            "reachable_ip": "203.0.113.50", "reachable_via": "floating_ip",
            "confirmed": [{"port": 22, "reason": "SSH open to the world"}],
        },
    )

    result = security.list_instance_exposed_ports(current_user=types.SimpleNamespace(role="viewer"))

    row = result["instances"][0]
    assert row["restricted"] is True
    assert "reachable_ip" not in row
    assert row["has_signal"] is True  # booleans still surface for a viewer


def test_each_instance_still_gets_its_own_signal_independently(monkeypatch):
    # A failure resolving one instance must not affect another's row.
    monkeypatch.setattr(
        security.graph_db, "fetch_all_instances",
        lambda: [{"id": "vm-1", "name": "sandbox-vm-1"}, {"id": "vm-broken", "name": "sandbox-vm-broken"}],
    )

    def _signal(instance_id, instance_name=None, conn=None):
        if instance_id == "vm-broken":
            return {"has_signal": False, "degraded": True, "detail": "couldn't resolve", "instance_id": instance_id}
        return {"has_signal": False, "degraded": False, "detail": "clean", "instance_id": instance_id}

    monkeypatch.setattr(security.instance_exposure, "build_instance_exposure_signal", _signal)

    result = security.list_instance_exposed_ports(current_user=types.SimpleNamespace(role="admin"))

    by_id = {row["instance_id"]: row for row in result["instances"]}
    assert by_id["vm-1"]["degraded"] is False
    assert by_id["vm-broken"]["degraded"] is True
