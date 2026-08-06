"""Tests for services/topology_sync.py (Phase 2 of the topology-graph
feature -- Nova hypervisors/services + Cinder services ->
Node/Service/RUNS_ON, plus a mark-and-sweep pass over the graph).

OpenStack is faked via _connect(): an object with `.compute.hypervisors()/
.services()` and `.block_storage.services()`, using attribute names that
match openstacksdk's actual resource mapping (checked directly against
openstack.compute.v2.hypervisor.Hypervisor, .service.Service, and
openstack.block_storage.v3.service.Service).

Neo4j is faked with a small in-memory store (_FakeGraphStore) whose
_FakeSession.run() pattern-matches on the handful of Cypher shapes
topology_sync actually issues (MERGE nodes, MERGE services + RUNS_ON,
sweep nodes, sweep services) and applies the same effect a real Neo4j
instance would -- so the mark-and-sweep tests assert on real before/after
graph state, not just "a query was issued".
"""
import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.services import topology_sync


def _db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _hv(name, host_ip="10.0.1.99", vcpus=8, vcpus_used=1, memory_size=16384,
        memory_used=2048, state="up", status="enabled", id="hv-1"):
    return types.SimpleNamespace(
        name=name, host_ip=host_ip, vcpus=vcpus, vcpus_used=vcpus_used,
        memory_size=memory_size, memory_used=memory_used, state=state,
        status=status, id=id,
    )


def _svc(host, binary, availability_zone="nova", status="enabled", state="up", id="svc-1"):
    return types.SimpleNamespace(
        host=host, binary=binary, availability_zone=availability_zone,
        status=status, state=state, id=id,
    )


class _FakeResult:
    def __init__(self, record):
        self._record = record

    def single(self):
        return self._record


class _FakeGraphStore:
    """Minimal in-memory stand-in for the graph's actual state, keyed the
    same way the real Neo4j vertices are (id)."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.services: dict[str, dict] = {}


class _FakeSession:
    """Applies the same effect the real Cypher would against a
    _FakeGraphStore, dispatching on the (small, fixed) set of query shapes
    topology_sync issues. Also keeps `.calls` for tests that just want to
    assert on what was sent, without caring about resulting state.
    """

    def __init__(self, store: _FakeGraphStore):
        self.store = store
        self.calls = []

    def run(self, query, **kwargs):
        self.calls.append((query, kwargs))

        if "MERGE (node:Node" in query:
            for n in kwargs["nodes"]:
                self.store.nodes[n["id"]] = n
            return _FakeResult(None)

        if "MERGE (s:Service" in query:
            for s in kwargs["services"]:
                self.store.services[s["id"]] = s
            return _FakeResult(None)

        if "MATCH (s:Service)" in query and "DETACH DELETE" in query:
            seen = set(kwargs["seen_ids"])
            stale = [sid for sid in self.store.services if sid not in seen]
            for sid in stale:
                del self.store.services[sid]
            return _FakeResult({"removed": len(stale)})

        if "MATCH (n:Node)" in query and "DETACH DELETE" in query:
            seen = set(kwargs["seen_ids"])
            stale = [nid for nid in self.store.nodes if nid not in seen]
            for nid in stale:
                del self.store.nodes[nid]
            return _FakeResult({"removed": len(stale)})

        return _FakeResult(None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    """Reused across multiple sync_topology() calls in a test so the
    mark-and-sweep tests can see state carry over between passes, exactly
    like the real driver would."""

    def __init__(self):
        self.store = _FakeGraphStore()
        self.last_session = None

    def session(self):
        self.last_session = _FakeSession(self.store)
        return self.last_session


class _FakeConn:
    def __init__(self, hypervisors=None, nova_services=None, cinder_services=None,
                 hypervisors_raise=False, nova_services_raise=False, cinder_services_raise=False):
        def _hypervisors(details=False):
            if hypervisors_raise:
                raise RuntimeError("nova hypervisors unreachable")
            return iter(hypervisors or [])

        def _nova_services():
            if nova_services_raise:
                raise RuntimeError("nova services unreachable")
            return iter(nova_services or [])

        def _cinder_services():
            if cinder_services_raise:
                raise RuntimeError("cinder services unreachable")
            return iter(cinder_services or [])

        self.compute = types.SimpleNamespace(hypervisors=_hypervisors, services=_nova_services)
        self.block_storage = types.SimpleNamespace(services=_cinder_services)


def _patch_graph(monkeypatch):
    fake_driver = _FakeDriver()
    monkeypatch.setattr(topology_sync.graph_db, "driver", fake_driver)
    return fake_driver


def _patch_side_effects(monkeypatch, install_ok=True):
    """Neuter the filesystem/subprocess side effects a real registration
    would trigger, and record whether they ran."""
    calls = {"inventory": [], "installed": [], "file_sd_regenerated": 0}

    def fake_add_host(hostname, ip, role):
        calls["inventory"].append((hostname, ip, role))

    def fake_install(hostname):
        calls["installed"].append(hostname)
        return install_ok

    def fake_regenerate(db):
        calls["file_sd_regenerated"] += 1

    monkeypatch.setattr(topology_sync, "add_host_to_inventory", fake_add_host)
    monkeypatch.setattr(topology_sync, "install_node_exporter", fake_install)
    monkeypatch.setattr(topology_sync, "regenerate_file_sd", fake_regenerate)
    return calls


def _set_conn(monkeypatch, conn):
    monkeypatch.setattr(topology_sync, "_connect", lambda: conn)


# ---------------------------------------------------------------- basics --

def test_new_hypervisor_is_registered_and_synced_to_graph(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    calls = _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute3", host_ip="10.0.1.5")]))

    result = topology_sync.sync_topology(db)

    assert result["new_computes"] == 1
    node = db.query(models.Node).filter_by(hostname="compute3").one()
    assert node.ip_address == "10.0.1.5"
    assert node.role == "compute"
    assert calls["inventory"] == [("compute3", "10.0.1.5", "compute")]
    assert calls["installed"] == ["compute3"]
    assert calls["file_sd_regenerated"] == 1

    assert fake_driver.store.nodes["compute3"]["ip_address"] == "10.0.1.5"
    assert fake_driver.store.nodes["compute3"]["vcpus"] == 8


def test_existing_hypervisor_is_not_re_registered(monkeypatch):
    db = _db()
    db.add(models.Node(hostname="compute1", ip_address="10.0.1.2", role="compute"))
    db.commit()
    fake_driver = _patch_graph(monkeypatch)
    calls = _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute1", host_ip="10.0.1.2")]))

    result = topology_sync.sync_topology(db)

    assert result["new_computes"] == 0
    assert db.query(models.Node).filter_by(hostname="compute1").count() == 1
    assert calls["inventory"] == []
    assert fake_driver.store.nodes["compute1"]["ip_address"] == "10.0.1.2"


# --------------------------------------------------------------- Cinder --

def test_cinder_service_links_to_stripped_hostname_and_keeps_backend(monkeypatch):
    db = _db()
    db.add(models.Node(hostname="storage", ip_address="10.0.2.3", role="storage"))
    db.commit()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(
        cinder_services=[_svc("storage@lvmdriver-1", "cinder-volume", availability_zone="nova")],
    ))

    result = topology_sync.sync_topology(db)

    assert result["cinder_services"] == 1
    assert result["unresolved_hosts"] == 0

    # linked to the physical host, not a synthetic "storage@lvmdriver-1" node
    assert "storage" in fake_driver.store.nodes
    assert "storage@lvmdriver-1" not in fake_driver.store.nodes
    assert fake_driver.store.nodes["storage"]["ip_address"] == "10.0.2.3"

    svc_id = "cinder-volume@storage@lvmdriver-1"
    service = fake_driver.store.services[svc_id]
    assert service["backend"] == "lvmdriver-1"
    assert service["source"] == "cinder"
    assert service["node_id"] == "storage"


def test_cinder_service_without_backend_suffix_links_directly(monkeypatch):
    db = _db()
    db.add(models.Node(hostname="storage", ip_address="10.0.2.3", role="storage"))
    db.commit()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(cinder_services=[_svc("storage", "cinder-scheduler")]))

    topology_sync.sync_topology(db)

    service = fake_driver.store.services["cinder-scheduler@storage"]
    assert service["backend"] is None
    assert service["node_id"] == "storage"


def test_cinder_fetch_failure_does_not_crash_and_skips_sweep(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    # seed the graph with something that would look "stale" this pass if
    # sweeping ran on an incomplete picture
    fake_driver.store.nodes["ghost"] = {"id": "ghost"}
    fake_driver.store.services["ghost-svc"] = {"id": "ghost-svc"}

    _set_conn(monkeypatch, _FakeConn(
        hypervisors=[_hv("compute1")],
        nova_services=[_svc("compute1", "nova-compute")],
        cinder_services_raise=True,
    ))

    result = topology_sync.sync_topology(db)

    assert result["complete_picture"] is False
    assert result["swept_nodes"] == 0
    assert result["swept_services"] == 0
    # nothing was removed despite not being touched by this pass
    assert "ghost" in fake_driver.store.nodes
    assert "ghost-svc" in fake_driver.store.services


def test_unresolved_service_host_becomes_placeholder(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(nova_services=[_svc("ghost-host", "nova-compute")]))

    result = topology_sync.sync_topology(db)

    assert result["unresolved_hosts"] == 1
    assert fake_driver.store.nodes["ghost-host"]["ip_address"] is None


# ---------------------------------------------------------- mark & sweep --

def test_sweep_removes_decommissioned_hypervisor_and_removed_service(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)

    # Pass 1: two hypervisors, one Nova service on compute1.
    _set_conn(monkeypatch, _FakeConn(
        hypervisors=[_hv("compute1"), _hv("compute2", id="hv-2")],
        nova_services=[_svc("compute1", "nova-compute")],
    ))
    topology_sync.sync_topology(db)

    assert set(fake_driver.store.nodes) == {"compute1", "compute2"}
    assert set(fake_driver.store.services) == {"nova-compute@compute1"}

    # Pass 2: compute2 decommissioned, its service list is now empty too.
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute1")]))
    result = topology_sync.sync_topology(db)

    assert result["complete_picture"] is True
    assert result["swept_nodes"] == 1
    assert result["swept_services"] == 1
    assert set(fake_driver.store.nodes) == {"compute1"}
    assert set(fake_driver.store.services) == set()


def test_sweep_keeps_everything_still_reported(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)

    conn = _FakeConn(
        hypervisors=[_hv("compute1")],
        nova_services=[_svc("compute1", "nova-compute")],
        cinder_services=[_svc("storage", "cinder-volume")],
    )
    _set_conn(monkeypatch, conn)

    topology_sync.sync_topology(db)
    first_snapshot = (set(fake_driver.store.nodes), set(fake_driver.store.services))

    # identical second pass -- nothing should be swept
    result = topology_sync.sync_topology(db)

    assert result["swept_nodes"] == 0
    assert result["swept_services"] == 0
    assert (set(fake_driver.store.nodes), set(fake_driver.store.services)) == first_snapshot


def test_failed_registration_does_not_crash_the_pass(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute9", host_ip="10.0.1.9")]))

    def boom(*args, **kwargs):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(topology_sync.crud, "create_node", boom)

    result = topology_sync.sync_topology(db)

    assert result["new_computes"] == 0
    assert db.query(models.Node).filter_by(hostname="compute9").count() == 0
    # still graphed, best-effort, even though Postgres registration failed
    assert fake_driver.store.nodes["compute9"]["role"] == "compute"
    assert fake_driver.store.nodes["compute9"]["ip_address"] is None


def test_summary_counts_reflect_input_sizes(monkeypatch):
    db = _db()
    _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(
        hypervisors=[_hv("compute1"), _hv("compute2", id="hv-2")],
        nova_services=[_svc("compute1", "nova-compute"), _svc("controller", "nova-scheduler")],
        cinder_services=[_svc("storage@lvmdriver-1", "cinder-volume")],
    ))

    result = topology_sync.sync_topology(db)

    assert result["hypervisors"] == 2
    assert result["nova_services"] == 2
    assert result["cinder_services"] == 1
    assert result["graph_nodes"] == 4  # compute1, compute2, controller, storage
    assert result["graph_services"] == 3
