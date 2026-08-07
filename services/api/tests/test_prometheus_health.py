"""Tests for services/prometheus_health.py (Phase 4 of the topology-graph
feature -- overlaying `up{job="node_exporter"}` onto :Node.health and
reconciling :Service.state against it; see
docs/architecture/adr-0003-prometheus-cross-check.md for the decisions
being tested here).

Neo4j is faked with a small in-memory store, same spirit as
test_topology_sync.py's _FakeGraphStore/_FakeSession but scoped to just
the two query shapes prometheus_health.py issues (bulk Node.health SET,
bulk Service.state recompute via CASE) -- real graph state in, real graph
state out, not just "a query was issued".
"""
import app.services.prometheus_health as mod
from app.services.prometheus_health import (
    fetch_node_health,
    reconcile_service_state,
    sync_prometheus_health,
)


# ------------------------------------------------------------- fake graph --

class _FakeGraphStore:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.services: dict[str, dict] = {}
        self.runs_on: dict[str, str] = {}  # service_id -> node_id


class _FakeSession:
    def __init__(self, store: _FakeGraphStore):
        self.store = store

    def run(self, query, **kwargs):
        if "SET n.health = h" in query:
            up_ids = set(kwargs["up_ids"])
            down_ids = set(kwargs["down_ids"])
            for node_id, node in self.store.nodes.items():
                if node_id in up_ids:
                    node["health"] = "up"
                elif node_id in down_ids:
                    node["health"] = "down"
                else:
                    node["health"] = "unknown"
            return None

        if "SET s.state = CASE" in query:
            for service_id, svc in self.store.services.items():
                node_id = self.store.runs_on.get(service_id)
                node_health = self.store.nodes.get(node_id, {}).get("health", "unknown") if node_id else "unknown"
                svc["state"] = reconcile_service_state(svc.get("openstack_state"), node_health)
            return None

        raise AssertionError(f"unexpected query in fake session: {query}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self, store: _FakeGraphStore):
        self.store = store

    def session(self):
        return _FakeSession(self.store)


def _up_metric(hostname, value="1", instance=None):
    return {
        "metric": {"instance": instance or f"{hostname}:9100", "node": hostname, "job": "node_exporter"},
        "value": [1700000000, value],
    }


# ----------------------------------------------------------- fetch_node_health --

def test_fetch_node_health_maps_prometheus_value_to_up_down(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda promql: [
        _up_metric("compute1-sim", value="1"),
        _up_metric("compute2-sim", value="0"),
    ])

    health = fetch_node_health()

    assert health == {"compute1-sim": "up", "compute2-sim": "down"}


def test_fetch_node_health_skips_series_with_no_node_label(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda promql: [
        {"metric": {"instance": "10.0.1.9:9100", "job": "node_exporter"}, "value": [1700000000, "1"]},
        _up_metric("compute1-sim"),
    ])

    health = fetch_node_health()

    # Only the series with a `node` label -- the one thing that can be
    # joined onto a Node vertex's `id` -- makes it into the result.
    assert health == {"compute1-sim": "up"}


def test_fetch_node_health_empty_result_is_empty_dict(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda promql: [])
    assert fetch_node_health() == {}


# ------------------------------------------------------- reconcile_service_state --
# The decision table from adr-0003, decision 2, exercised row by row.

def test_reconcile_openstack_down_always_wins():
    assert reconcile_service_state("down", "up") == "down"
    assert reconcile_service_state("down", "down") == "down"
    assert reconcile_service_state("down", "unknown") == "down"
    assert reconcile_service_state("down", None) == "down"


def test_reconcile_up_against_down_host_is_flagged_unreachable():
    assert reconcile_service_state("up", "down") == "unreachable"


def test_reconcile_up_against_up_host_stays_up():
    assert reconcile_service_state("up", "up") == "up"


def test_reconcile_up_against_unknown_host_falls_back_to_up():
    # No cross-check data yet -- don't penalize the service for the
    # health overlay simply not having run against its host yet.
    assert reconcile_service_state("up", "unknown") == "up"
    assert reconcile_service_state("up", None) == "up"


def test_reconcile_passes_through_unrecognized_openstack_state():
    assert reconcile_service_state(None, "up") is None
    assert reconcile_service_state("disabled", "down") == "disabled"


# --------------------------------------------------------- sync_prometheus_health --

def _patch_graph(monkeypatch, store):
    fake_driver = _FakeDriver(store)
    monkeypatch.setattr(mod.graph_db, "driver", fake_driver)
    return fake_driver


def test_sync_sets_explicit_health_up_down_unknown_for_every_node(monkeypatch):
    store = _FakeGraphStore()
    store.nodes = {
        "compute1-sim": {"id": "compute1-sim"},
        "compute2-sim": {"id": "compute2-sim"},
        "controller-sim": {"id": "controller-sim"},  # Prometheus has no data for this one
    }
    _patch_graph(monkeypatch, store)
    monkeypatch.setattr(mod, "query", lambda promql: [
        _up_metric("compute1-sim", value="1"),
        _up_metric("compute2-sim", value="0"),
    ])

    result = sync_prometheus_health()

    assert store.nodes["compute1-sim"]["health"] == "up"
    assert store.nodes["compute2-sim"]["health"] == "down"
    # No Prometheus series at all for controller-sim this pass -- explicit
    # "unknown", not left unset and not assumed "down".
    assert store.nodes["controller-sim"]["health"] == "unknown"
    assert result == {"queried": True, "nodes_up": 1, "nodes_down": 1}


def test_sync_recomputes_service_state_unreachable_when_host_confirmed_down(monkeypatch):
    store = _FakeGraphStore()
    store.nodes = {"compute1-sim": {"id": "compute1-sim"}}
    store.services = {"nova-compute@compute1-sim": {"openstack_state": "up"}}
    store.runs_on = {"nova-compute@compute1-sim": "compute1-sim"}
    _patch_graph(monkeypatch, store)
    monkeypatch.setattr(mod, "query", lambda promql: [_up_metric("compute1-sim", value="0")])

    sync_prometheus_health()

    assert store.nodes["compute1-sim"]["health"] == "down"
    assert store.services["nova-compute@compute1-sim"]["state"] == "unreachable"


def test_sync_service_reported_down_stays_down_even_if_host_is_up(monkeypatch):
    store = _FakeGraphStore()
    store.nodes = {"compute1-sim": {"id": "compute1-sim"}}
    store.services = {"nova-compute@compute1-sim": {"openstack_state": "down"}}
    store.runs_on = {"nova-compute@compute1-sim": "compute1-sim"}
    _patch_graph(monkeypatch, store)
    monkeypatch.setattr(mod, "query", lambda promql: [_up_metric("compute1-sim", value="1")])

    sync_prometheus_health()

    assert store.nodes["compute1-sim"]["health"] == "up"
    assert store.services["nova-compute@compute1-sim"]["state"] == "down"


def test_sync_service_up_against_no_prometheus_data_stays_up(monkeypatch):
    store = _FakeGraphStore()
    store.nodes = {"compute1-sim": {"id": "compute1-sim"}}
    store.services = {"nova-compute@compute1-sim": {"openstack_state": "up"}}
    store.runs_on = {"nova-compute@compute1-sim": "compute1-sim"}
    _patch_graph(monkeypatch, store)
    monkeypatch.setattr(mod, "query", lambda promql: [])  # Prometheus reachable, just no data yet

    sync_prometheus_health()

    assert store.nodes["compute1-sim"]["health"] == "unknown"
    assert store.services["nova-compute@compute1-sim"]["state"] == "up"


def test_sync_skips_pass_and_leaves_existing_state_when_prometheus_unreachable(monkeypatch):
    store = _FakeGraphStore()
    store.nodes = {"compute1-sim": {"id": "compute1-sim", "health": "up"}}
    store.services = {"nova-compute@compute1-sim": {"openstack_state": "up", "state": "up"}}
    store.runs_on = {"nova-compute@compute1-sim": "compute1-sim"}
    _patch_graph(monkeypatch, store)

    def _boom(promql):
        raise RuntimeError("prometheus unreachable")

    monkeypatch.setattr(mod, "query", _boom)

    result = sync_prometheus_health()

    # Existing values from the last successful pass are untouched, not
    # overwritten with a guess.
    assert store.nodes["compute1-sim"]["health"] == "up"
    assert store.services["nova-compute@compute1-sim"]["state"] == "up"
    assert result == {"queried": False, "nodes_up": 0, "nodes_down": 0}


def test_sync_accepts_and_ignores_a_db_argument(monkeypatch):
    # main.py's _run_periodic(fn, interval, name) always calls fn(db) --
    # sync_prometheus_health must accept (and ignore) that positional arg
    # even though it never touches Postgres.
    store = _FakeGraphStore()
    _patch_graph(monkeypatch, store)
    monkeypatch.setattr(mod, "query", lambda promql: [])

    result = sync_prometheus_health(db=object())

    assert result["queried"] is True
