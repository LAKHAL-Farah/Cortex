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


def _network(id, name="net", status="ACTIVE", is_admin_state_up=True, is_shared=False, project_id="proj"):
    return types.SimpleNamespace(
        id=id, name=name, status=status, is_admin_state_up=is_admin_state_up,
        is_shared=is_shared, project_id=project_id,
    )


def _subnet(id, network_id, name="subnet", cidr="10.0.0.0/24", ip_version=4, gateway_ip="10.0.0.1"):
    return types.SimpleNamespace(
        id=id, network_id=network_id, name=name, cidr=cidr,
        ip_version=ip_version, gateway_ip=gateway_ip,
    )


def _router(id, name="router", status="ACTIVE", is_admin_state_up=True, project_id="proj",
            external_gateway_info=None):
    return types.SimpleNamespace(
        id=id, name=name, status=status, is_admin_state_up=is_admin_state_up,
        project_id=project_id, external_gateway_info=external_gateway_info,
    )


def _fip(id, floating_network_id, router_id=None, floating_ip_address="203.0.113.1",
         fixed_ip_address=None, status="ACTIVE"):
    return types.SimpleNamespace(
        id=id, floating_network_id=floating_network_id, router_id=router_id,
        floating_ip_address=floating_ip_address, fixed_ip_address=fixed_ip_address, status=status,
    )


def _agent(id, binary, host, agent_type, is_alive=True, is_admin_state_up=True, availability_zone=None):
    return types.SimpleNamespace(
        id=id, binary=binary, host=host, agent_type=agent_type, is_alive=is_alive,
        is_admin_state_up=is_admin_state_up, availability_zone=availability_zone,
    )


def _server(id, name="vm", status="ACTIVE", project_id="proj", hypervisor_hostname=None, flavor=None):
    # `flavor` mirrors what openstacksdk actually hands back for an
    # embedded flavor -- a dict-like object, not a plain dict, but a
    # plain dict literal here behaves identically for _flavor_fields'
    # purposes (isinstance(..., dict) and .get() both still work).
    return types.SimpleNamespace(
        id=id, name=name, status=status, project_id=project_id,
        hypervisor_hostname=hypervisor_hostname,
        flavor=flavor if flavor is not None else {"original_name": "m1.small", "vcpus": 1, "ram": 2048},
    )


def _port(id, name="port", status="ACTIVE", is_admin_state_up=True, mac_address="fa:16:3e:00:00:00",
          device_id=None, device_owner=None, fixed_ips=None):
    return types.SimpleNamespace(
        id=id, name=name, status=status, is_admin_state_up=is_admin_state_up,
        mac_address=mac_address, device_id=device_id, device_owner=device_owner,
        fixed_ips=fixed_ips or [],
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
        self.networks: dict[str, dict] = {}
        self.subnets: dict[str, dict] = {}
        self.routers: dict[str, dict] = {}
        self.floating_ips: dict[str, dict] = {}
        # Edges as {source_id: target_id} (single-target CONNECTS edges) or
        # {source_id: {target_id, ...}} (multi-target SERVES edges) -- only
        # ever populated when the fake's MATCH on the target side would
        # actually have found something, same as real Neo4j.
        self.subnet_network_edges: dict[str, str] = {}
        self.router_gateway_edges: dict[str, str] = {}
        self.fip_network_edges: dict[str, str] = {}
        self.fip_router_edges: dict[str, str] = {}
        self.dhcp_serves: dict[str, set] = {}
        self.l3_serves: dict[str, set] = {}
        # Phase 6.
        self.instances: dict[str, dict] = {}
        self.ports: dict[str, dict] = {}
        self.instance_host_edges: dict[str, str] = {}  # instance_id -> node_id
        self.instance_port_edges: dict[str, str] = {}  # port_id -> instance_id
        self.port_subnet_edges: dict[str, set] = {}  # port_id -> {subnet_id, ...}


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

        # ---- Phase 3: Network/Subnet/Router/FloatingIP + CONNECTS/SERVES ----

        if "MERGE (n:Network {id: net.id})" in query:
            for net in kwargs["networks"]:
                self.store.networks[net["id"]] = net
            return _FakeResult(None)

        if "MERGE (s:Subnet {id: sub.id})" in query:
            for sub in kwargs["subnets"]:
                self.store.subnets[sub["id"]] = sub
                if sub["network_id"] in self.store.networks:
                    self.store.subnet_network_edges[sub["id"]] = sub["network_id"]
            return _FakeResult(None)

        if "MERGE (router:Router {id: r.id})" in query:
            for r in kwargs["routers"]:
                self.store.routers[r["id"]] = r
            return _FakeResult(None)

        if "OPTIONAL MATCH (router)-[old:CONNECTS]->(:Network)" in query:
            for r in kwargs["routers"]:
                self.store.router_gateway_edges.pop(r["id"], None)
            return _FakeResult(None)

        if "MATCH (net:Network {id: r.gateway_network_id})" in query:
            for r in kwargs["routers"]:
                if r["id"] in self.store.routers and r["gateway_network_id"] in self.store.networks:
                    self.store.router_gateway_edges[r["id"]] = r["gateway_network_id"]
            return _FakeResult(None)

        if "MERGE (f:FloatingIP {id: fip.id})" in query:
            for fip in kwargs["fips"]:
                self.store.floating_ips[fip["id"]] = fip
                if fip["network_id"] in self.store.networks:
                    self.store.fip_network_edges[fip["id"]] = fip["network_id"]
            return _FakeResult(None)

        if "OPTIONAL MATCH (f)-[old:CONNECTS]->(:Router)" in query:
            for fip in kwargs["fips"]:
                self.store.fip_router_edges.pop(fip["id"], None)
            return _FakeResult(None)

        if "MATCH (r:Router {id: fip.router_id})" in query:
            for fip in kwargs["fips"]:
                if fip["id"] in self.store.floating_ips and fip["router_id"] in self.store.routers:
                    self.store.fip_router_edges[fip["id"]] = fip["router_id"]
            return _FakeResult(None)

        if "OPTIONAL MATCH (s)-[old:SERVES]->(:Network)" in query:
            for aid in kwargs["agent_ids"]:
                self.store.dhcp_serves.pop(aid, None)
            return _FakeResult(None)

        if "MATCH (net:Network {id: h.network_id})" in query:
            for h in kwargs["hosting"]:
                if h["service_id"] in self.store.services and h["network_id"] in self.store.networks:
                    self.store.dhcp_serves.setdefault(h["service_id"], set()).add(h["network_id"])
            return _FakeResult(None)

        if "OPTIONAL MATCH (s)-[old:SERVES]->(:Router)" in query:
            for aid in kwargs["agent_ids"]:
                self.store.l3_serves.pop(aid, None)
            return _FakeResult(None)

        if "MATCH (r:Router {id: h.router_id})" in query:
            for h in kwargs["hosting"]:
                if h["service_id"] in self.store.services and h["router_id"] in self.store.routers:
                    self.store.l3_serves.setdefault(h["service_id"], set()).add(h["router_id"])
            return _FakeResult(None)

        if "MATCH (v:Network)" in query and "DETACH DELETE" in query:
            seen = set(kwargs["seen_ids"])
            stale = [i for i in self.store.networks if i not in seen]
            for i in stale:
                del self.store.networks[i]
            return _FakeResult({"removed": len(stale)})

        if "MATCH (v:Subnet)" in query and "DETACH DELETE" in query:
            seen = set(kwargs["seen_ids"])
            stale = [i for i in self.store.subnets if i not in seen]
            for i in stale:
                del self.store.subnets[i]
                self.store.subnet_network_edges.pop(i, None)
            return _FakeResult({"removed": len(stale)})

        if "MATCH (v:Router)" in query and "DETACH DELETE" in query:
            seen = set(kwargs["seen_ids"])
            stale = [i for i in self.store.routers if i not in seen]
            for i in stale:
                del self.store.routers[i]
                self.store.router_gateway_edges.pop(i, None)
            return _FakeResult({"removed": len(stale)})

        if "MATCH (v:FloatingIP)" in query and "DETACH DELETE" in query:
            seen = set(kwargs["seen_ids"])
            stale = [i for i in self.store.floating_ips if i not in seen]
            for i in stale:
                del self.store.floating_ips[i]
                self.store.fip_network_edges.pop(i, None)
                self.store.fip_router_edges.pop(i, None)
            return _FakeResult({"removed": len(stale)})

        # ---- Phase 6: Instance/Port + RUNS_ON/HAS_PORT/CONNECTS ----

        if "MERGE (i:Instance {id: inst.id})" in query:
            for inst in kwargs["instances"]:
                self.store.instances[inst["id"]] = inst
            return _FakeResult(None)

        if "OPTIONAL MATCH (i)-[old:RUNS_ON]->(:Node)" in query:
            for inst in kwargs["instances"]:
                self.store.instance_host_edges.pop(inst["id"], None)
            return _FakeResult(None)

        if "MATCH (n:Node {id: inst.hypervisor_hostname})" in query:
            for inst in kwargs["instances"]:
                if inst["id"] in self.store.instances and inst["hypervisor_hostname"] in self.store.nodes:
                    self.store.instance_host_edges[inst["id"]] = inst["hypervisor_hostname"]
            return _FakeResult(None)

        if "MERGE (port:Port {id: p.id})" in query:
            for p in kwargs["ports"]:
                self.store.ports[p["id"]] = p
            return _FakeResult(None)

        if "OPTIONAL MATCH (:Instance)-[old:HAS_PORT]->(port)" in query:
            for p in kwargs["ports"]:
                self.store.instance_port_edges.pop(p["id"], None)
            return _FakeResult(None)

        if "MATCH (i:Instance {id: p.device_id})" in query:
            for p in kwargs["ports"]:
                if p["id"] in self.store.ports and p["device_id"] in self.store.instances:
                    self.store.instance_port_edges[p["id"]] = p["device_id"]
            return _FakeResult(None)

        if "OPTIONAL MATCH (port)-[old:CONNECTS]->(:Subnet)" in query:
            for p in kwargs["ports"]:
                self.store.port_subnet_edges.pop(p["id"], None)
            return _FakeResult(None)

        if "MATCH (sub:Subnet {id: e.subnet_id})" in query:
            for e in kwargs["edges"]:
                if e["port_id"] in self.store.ports and e["subnet_id"] in self.store.subnets:
                    self.store.port_subnet_edges.setdefault(e["port_id"], set()).add(e["subnet_id"])
            return _FakeResult(None)

        if "MATCH (v:Instance)" in query and "DETACH DELETE" in query:
            seen = set(kwargs["seen_ids"])
            stale = [i for i in self.store.instances if i not in seen]
            for i in stale:
                del self.store.instances[i]
                self.store.instance_host_edges.pop(i, None)
                for port_id, instance_id in list(self.store.instance_port_edges.items()):
                    if instance_id == i:
                        del self.store.instance_port_edges[port_id]
            return _FakeResult({"removed": len(stale)})

        if "MATCH (v:Port)" in query and "DETACH DELETE" in query:
            seen = set(kwargs["seen_ids"])
            stale = [i for i in self.store.ports if i not in seen]
            for i in stale:
                del self.store.ports[i]
                self.store.port_subnet_edges.pop(i, None)
                self.store.instance_port_edges.pop(i, None)
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
                 hypervisors_raise=False, nova_services_raise=False, cinder_services_raise=False,
                 networks=None, subnets=None, routers=None, floating_ips=None, neutron_agents=None,
                 networks_raise=False, subnets_raise=False, routers_raise=False,
                 floating_ips_raise=False, neutron_agents_raise=False,
                 dhcp_hosting=None, dhcp_hosting_raise_for=None,
                 l3_hosting=None, l3_hosting_raise_for=None,
                 instances=None, ports=None, instances_raise=False, ports_raise=False):
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

        def _servers(details=False):
            if instances_raise:
                raise RuntimeError("nova servers unreachable")
            return iter(instances or [])

        self.compute = types.SimpleNamespace(hypervisors=_hypervisors, services=_nova_services, servers=_servers)
        self.block_storage = types.SimpleNamespace(services=_cinder_services)

        def _networks(**kw):
            if networks_raise:
                raise RuntimeError("neutron networks unreachable")
            return iter(networks or [])

        def _subnets(**kw):
            if subnets_raise:
                raise RuntimeError("neutron subnets unreachable")
            return iter(subnets or [])

        def _routers(**kw):
            if routers_raise:
                raise RuntimeError("neutron routers unreachable")
            return iter(routers or [])

        def _ips(**kw):
            if floating_ips_raise:
                raise RuntimeError("neutron floating ips unreachable")
            return iter(floating_ips or [])

        def _agents(**kw):
            if neutron_agents_raise:
                raise RuntimeError("neutron agents unreachable")
            return iter(neutron_agents or [])

        _dhcp_hosting = dhcp_hosting or {}
        _dhcp_hosting_raise_for = dhcp_hosting_raise_for or set()
        _l3_hosting = l3_hosting or {}
        _l3_hosting_raise_for = l3_hosting_raise_for or set()

        def _dhcp_agent_hosting_networks(agent, **kw):
            if agent.id in _dhcp_hosting_raise_for:
                raise RuntimeError(f"dhcp hosting unreachable for {agent.id}")
            return iter(_dhcp_hosting.get(agent.id, []))

        def _agent_hosted_routers(agent, **kw):
            if agent.id in _l3_hosting_raise_for:
                raise RuntimeError(f"l3 hosting unreachable for {agent.id}")
            return iter(_l3_hosting.get(agent.id, []))

        def _ports(**kw):
            if ports_raise:
                raise RuntimeError("neutron ports unreachable")
            return iter(ports or [])

        self.network = types.SimpleNamespace(
            networks=_networks,
            subnets=_subnets,
            routers=_routers,
            ips=_ips,
            agents=_agents,
            dhcp_agent_hosting_networks=_dhcp_agent_hosting_networks,
            agent_hosted_routers=_agent_hosted_routers,
            ports=_ports,
        )


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


# ---------------------------------------------------------------------------
# Phase 3: Neutron networks/subnets/routers/floating IPs, agents, SERVES/
# CONNECTS edges
# ---------------------------------------------------------------------------

def test_networks_subnets_routers_floating_ips_synced_with_connects_edges(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1 = _network("net-1")
    router1 = _router("router-1", external_gateway_info={"network_id": "net-1"})
    _set_conn(monkeypatch, _FakeConn(
        networks=[net1],
        subnets=[_subnet("subnet-1", network_id="net-1")],
        routers=[router1],
        floating_ips=[_fip("fip-1", floating_network_id="net-1", router_id="router-1")],
    ))

    result = topology_sync.sync_topology(db)

    assert result["networks"] == 1
    assert result["subnets"] == 1
    assert result["routers"] == 1
    assert result["floating_ips"] == 1
    assert result["network_topology_ok"] is True

    store = fake_driver.store
    assert "net-1" in store.networks
    assert "subnet-1" in store.subnets
    assert "router-1" in store.routers
    assert "fip-1" in store.floating_ips
    # Subnet -[:CONNECTS]-> Network
    assert store.subnet_network_edges["subnet-1"] == "net-1"
    # Router -[:CONNECTS]-> Network (external gateway)
    assert store.router_gateway_edges["router-1"] == "net-1"
    # FloatingIP -[:CONNECTS]-> Network and -[:CONNECTS]-> Router
    assert store.fip_network_edges["fip-1"] == "net-1"
    assert store.fip_router_edges["fip-1"] == "router-1"


def test_router_without_gateway_gets_no_connects_edge(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(
        networks=[_network("net-1")],
        routers=[_router("router-1", external_gateway_info=None)],
    ))

    topology_sync.sync_topology(db)

    assert "router-1" not in fake_driver.store.router_gateway_edges


def test_router_gateway_switching_networks_clears_old_edge(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1, net2 = _network("net-1"), _network("net-2")
    router = _router("router-1", external_gateway_info={"network_id": "net-1"})
    _set_conn(monkeypatch, _FakeConn(networks=[net1, net2], routers=[router]))
    topology_sync.sync_topology(db)
    assert fake_driver.store.router_gateway_edges["router-1"] == "net-1"

    # Same router, now gatewayed onto net-2 instead.
    router.external_gateway_info = {"network_id": "net-2"}
    topology_sync.sync_topology(db)

    assert fake_driver.store.router_gateway_edges["router-1"] == "net-2"


def test_router_gateway_detached_removes_edge(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1 = _network("net-1")
    router = _router("router-1", external_gateway_info={"network_id": "net-1"})
    _set_conn(monkeypatch, _FakeConn(networks=[net1], routers=[router]))
    topology_sync.sync_topology(db)
    assert "router-1" in fake_driver.store.router_gateway_edges

    router.external_gateway_info = None
    topology_sync.sync_topology(db)

    assert "router-1" not in fake_driver.store.router_gateway_edges


def test_floating_ip_reassociated_to_different_router_clears_old_edge(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1 = _network("net-1")
    router1, router2 = _router("router-1"), _router("router-2")
    fip = _fip("fip-1", floating_network_id="net-1", router_id="router-1")
    _set_conn(monkeypatch, _FakeConn(networks=[net1], routers=[router1, router2], floating_ips=[fip]))
    topology_sync.sync_topology(db)
    assert fake_driver.store.fip_router_edges["fip-1"] == "router-1"

    fip.router_id = "router-2"
    topology_sync.sync_topology(db)

    assert fake_driver.store.fip_router_edges["fip-1"] == "router-2"


def test_dhcp_agent_serves_its_hosted_networks(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1, net2 = _network("net-1"), _network("net-2")
    dhcp_agent = _agent("a1", "neutron-dhcp-agent", "controller", "DHCP agent")
    _set_conn(monkeypatch, _FakeConn(
        networks=[net1, net2],
        neutron_agents=[dhcp_agent],
        dhcp_hosting={"a1": [net1, net2]},
    ))

    result = topology_sync.sync_topology(db)

    assert result["neutron_agents"] == 1
    assert result["dhcp_hosting_edges"] == 2
    service_id = "neutron-dhcp-agent@controller"
    assert service_id in fake_driver.store.services
    assert fake_driver.store.dhcp_serves[service_id] == {"net-1", "net-2"}


def test_l3_agent_serves_its_hosted_routers(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    router1 = _router("router-1")
    l3_agent = _agent("a1", "neutron-l3-agent", "controller", "L3 agent")
    _set_conn(monkeypatch, _FakeConn(
        routers=[router1],
        neutron_agents=[l3_agent],
        l3_hosting={"a1": [router1]},
    ))

    result = topology_sync.sync_topology(db)

    assert result["l3_hosting_edges"] == 1
    service_id = "neutron-l3-agent@controller"
    assert fake_driver.store.l3_serves[service_id] == {"router-1"}


def test_dhcp_hosting_edge_removed_when_agent_stops_hosting_a_network(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1, net2 = _network("net-1"), _network("net-2")
    dhcp_agent = _agent("a1", "neutron-dhcp-agent", "controller", "DHCP agent")
    conn = _FakeConn(
        networks=[net1, net2],
        neutron_agents=[dhcp_agent],
        dhcp_hosting={"a1": [net1, net2]},
    )
    _set_conn(monkeypatch, conn)
    topology_sync.sync_topology(db)
    service_id = "neutron-dhcp-agent@controller"
    assert fake_driver.store.dhcp_serves[service_id] == {"net-1", "net-2"}

    # Agent now only hosts net-1.
    conn.network.dhcp_agent_hosting_networks = lambda agent, **kw: iter([net1])
    topology_sync.sync_topology(db)

    assert fake_driver.store.dhcp_serves[service_id] == {"net-1"}


def test_one_agents_hosting_failure_does_not_touch_its_stale_edges_or_other_agents(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1 = _network("net-1")
    router1 = _router("router-1")
    dhcp_agent = _agent("a1", "neutron-dhcp-agent", "controller", "DHCP agent")
    l3_agent = _agent("a2", "neutron-l3-agent", "controller", "L3 agent")
    dhcp_service_id = "neutron-dhcp-agent@controller"
    l3_service_id = "neutron-l3-agent@controller"

    # First pass: both agents' hosting calls succeed.
    _set_conn(monkeypatch, _FakeConn(
        networks=[net1], routers=[router1], neutron_agents=[dhcp_agent, l3_agent],
        dhcp_hosting={"a1": [net1]}, l3_hosting={"a2": [router1]},
    ))
    topology_sync.sync_topology(db)
    assert fake_driver.store.dhcp_serves[dhcp_service_id] == {"net-1"}
    assert fake_driver.store.l3_serves[l3_service_id] == {"router-1"}

    # Second pass: the DHCP agent's hosting call now fails; the L3 agent's
    # still succeeds.
    _set_conn(monkeypatch, _FakeConn(
        networks=[net1], routers=[router1], neutron_agents=[dhcp_agent, l3_agent],
        dhcp_hosting={"a1": [net1]}, dhcp_hosting_raise_for={"a1"},
        l3_hosting={"a2": [router1]},
    ))
    result = topology_sync.sync_topology(db)

    # The DHCP agent's edge from the last successful pass is left alone
    # (not wrongly cleared), and the L3 agent is unaffected.
    assert fake_driver.store.dhcp_serves[dhcp_service_id] == {"net-1"}
    assert fake_driver.store.l3_serves[l3_service_id] == {"router-1"}
    assert result["dhcp_hosting_edges"] == 0
    assert result["l3_hosting_edges"] == 1


def test_neutron_agent_synced_as_service_running_on_its_host(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    ovs_agent = _agent("a3", "neutron-openvswitch-agent", "compute3", "Open vSwitch agent",
                        is_alive=True, is_admin_state_up=True)
    _set_conn(monkeypatch, _FakeConn(neutron_agents=[ovs_agent]))

    result = topology_sync.sync_topology(db)

    service_id = "neutron-openvswitch-agent@compute3"
    svc = fake_driver.store.services[service_id]
    assert svc["node_id"] == "compute3"
    assert svc["source"] == "neutron"
    assert svc["status"] == "enabled"
    assert svc["state"] == "up"
    # compute3 wasn't a known hypervisor, so it's graphed as a bare
    # placeholder Node, same as an unresolved Nova/Cinder service host.
    assert "compute3" in fake_driver.store.nodes
    assert result["unresolved_hosts"] == 1


def test_neutron_agent_down_and_disabled_maps_to_down_disabled(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    dead_agent = _agent("a1", "neutron-dhcp-agent", "controller", "DHCP agent",
                         is_alive=False, is_admin_state_up=False)
    _set_conn(monkeypatch, _FakeConn(neutron_agents=[dead_agent]))

    topology_sync.sync_topology(db)

    svc = fake_driver.store.services["neutron-dhcp-agent@controller"]
    assert svc["state"] == "down"
    assert svc["status"] == "disabled"


def test_network_topology_sweep_removes_deleted_entities(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1 = _network("net-1")
    _set_conn(monkeypatch, _FakeConn(
        networks=[net1],
        subnets=[_subnet("subnet-1", network_id="net-1")],
        routers=[_router("router-1")],
        floating_ips=[_fip("fip-1", floating_network_id="net-1")],
    ))
    topology_sync.sync_topology(db)
    store = fake_driver.store
    assert set(store.subnets) == {"subnet-1"}
    assert set(store.routers) == {"router-1"}
    assert set(store.floating_ips) == {"fip-1"}

    # Next pass: only the network remains; subnet/router/floating IP were
    # deleted in OpenStack.
    _set_conn(monkeypatch, _FakeConn(networks=[net1]))
    result = topology_sync.sync_topology(db)

    assert result["swept_subnets"] == 1
    assert result["swept_routers"] == 1
    assert result["swept_floating_ips"] == 1
    assert result["swept_networks"] == 0
    assert set(store.networks) == {"net-1"}
    assert store.subnets == {}
    assert store.routers == {}
    assert store.floating_ips == {}


def test_neutron_listing_failure_skips_only_network_topology_sweep(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    # First pass: a healthy picture across the board, including a compute
    # node that's about to disappear.
    _set_conn(monkeypatch, _FakeConn(
        hypervisors=[_hv("compute1")],
        networks=[_network("net-1")],
        subnets=[_subnet("subnet-1", network_id="net-1")],
    ))
    topology_sync.sync_topology(db)
    assert "compute1" in fake_driver.store.nodes
    assert "subnet-1" in fake_driver.store.subnets

    # Second pass: Neutron subnets is unreachable, but Nova is fine and
    # compute1 is now gone.
    _set_conn(monkeypatch, _FakeConn(
        hypervisors=[],
        networks=[_network("net-1")],
        subnets_raise=True,
    ))
    result = topology_sync.sync_topology(db)

    assert result["network_topology_ok"] is False
    assert result["complete_picture"] is True
    # Compute sweep still ran (independent failure domain).
    assert "compute1" not in fake_driver.store.nodes
    # But the network-topology sweep did not run, so the now-unreported
    # subnet is left alone rather than being deleted on a partial picture.
    assert "subnet-1" in fake_driver.store.subnets
    assert result["swept_subnets"] == 0


def test_neutron_agents_failure_blocks_compute_sweep_too(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute1")]))
    topology_sync.sync_topology(db)
    assert "compute1" in fake_driver.store.nodes

    # Neutron agents unreachable this pass; compute1 no longer reported.
    _set_conn(monkeypatch, _FakeConn(hypervisors=[], neutron_agents_raise=True))
    result = topology_sync.sync_topology(db)

    assert result["complete_picture"] is False
    # Node/Service sweep is shared across all sources (including Neutron
    # agents, since they can populate placeholder Nodes too), so it's
    # skipped entirely on a partial picture -- compute1 survives.
    assert "compute1" in fake_driver.store.nodes


# ------------------------------------------------------ Phase 6: instances/ports --

def test_instance_and_port_synced_with_edges(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    server = _server("vm-1", hypervisor_hostname="compute1")
    port = _port("port-1", device_id="vm-1", device_owner="compute:nova",
                 fixed_ips=[{"subnet_id": "subnet-1", "ip_address": "10.0.1.101"}])
    _set_conn(monkeypatch, _FakeConn(
        hypervisors=[_hv("compute1")],
        networks=[_network("net-1")],
        subnets=[_subnet("subnet-1", network_id="net-1")],
        instances=[server],
        ports=[port],
    ))

    result = topology_sync.sync_topology(db)

    store = fake_driver.store
    assert result["instances"] == 1
    assert result["ports"] == 1
    assert store.instances["vm-1"]["flavor_name"] == "m1.small"
    assert store.instances["vm-1"]["flavor_vcpus"] == 1
    assert store.ports["port-1"]["fixed_ip_address"] == "10.0.1.101"
    # RUNS_ON: instance -> its hypervisor.
    assert store.instance_host_edges["vm-1"] == "compute1"
    # HAS_PORT: instance -> its port.
    assert store.instance_port_edges["port-1"] == "vm-1"
    # CONNECTS: port -> the subnet its fixed IP lives on.
    assert store.port_subnet_edges["port-1"] == {"subnet-1"}


def test_instance_with_no_visible_hypervisor_hostname_gets_no_runs_on_edge(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    # hypervisor_hostname=None -- the real-world case of a cloud that
    # gates OS-EXT-SRV-ATTR:hypervisor_hostname behind an admin-only
    # policy cortex-reader doesn't have (see topology_sync.py's Phase 6
    # docstring).
    server = _server("vm-1", hypervisor_hostname=None)
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute1")], instances=[server]))

    result = topology_sync.sync_topology(db)

    assert result["instances"] == 1
    assert "vm-1" in fake_driver.store.instances
    assert "vm-1" not in fake_driver.store.instance_host_edges


def test_instance_migration_moves_runs_on_edge(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    server = _server("vm-1", hypervisor_hostname="compute1")
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute1"), _hv("compute2")], instances=[server]))
    topology_sync.sync_topology(db)
    assert fake_driver.store.instance_host_edges["vm-1"] == "compute1"

    # Same VM, now live-migrated onto compute2.
    server.hypervisor_hostname = "compute2"
    topology_sync.sync_topology(db)

    assert fake_driver.store.instance_host_edges["vm-1"] == "compute2"


def test_non_compute_port_gets_no_has_port_edge(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    server = _server("vm-1")
    # A DHCP port -- exists in real Neutron, but this graph only models
    # the VM-facing side of ports (see topology_sync.py's Phase 6
    # docstring), so it gets a Port vertex and nothing else.
    dhcp_port = _port("port-1", device_id="agent-1", device_owner="network:dhcp")
    _set_conn(monkeypatch, _FakeConn(instances=[server], ports=[dhcp_port]))

    result = topology_sync.sync_topology(db)

    assert result["ports"] == 1
    assert "port-1" in fake_driver.store.ports
    assert "port-1" not in fake_driver.store.instance_port_edges


def test_port_ip_reassignment_to_different_subnet_clears_old_edge(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    net1 = _network("net-1")
    port = _port("port-1", device_id="vm-1", device_owner="compute:nova",
                 fixed_ips=[{"subnet_id": "subnet-1", "ip_address": "10.0.1.101"}])
    _set_conn(monkeypatch, _FakeConn(
        instances=[_server("vm-1")],
        networks=[net1],
        subnets=[_subnet("subnet-1", network_id="net-1"), _subnet("subnet-2", network_id="net-1")],
        ports=[port],
    ))
    topology_sync.sync_topology(db)
    assert fake_driver.store.port_subnet_edges["port-1"] == {"subnet-1"}

    # Same port, IP reassigned onto subnet-2 instead.
    port.fixed_ips = [{"subnet_id": "subnet-2", "ip_address": "10.0.1.201"}]
    topology_sync.sync_topology(db)

    assert fake_driver.store.port_subnet_edges["port-1"] == {"subnet-2"}


def test_workload_topology_sweep_removes_deleted_instance_and_port(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    server = _server("vm-1", hypervisor_hostname="compute1")
    port = _port("port-1", device_id="vm-1", device_owner="compute:nova")
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute1")], instances=[server], ports=[port]))
    topology_sync.sync_topology(db)
    store = fake_driver.store
    assert set(store.instances) == {"vm-1"}
    assert set(store.ports) == {"port-1"}
    assert "vm-1" in store.instance_host_edges
    assert "port-1" in store.instance_port_edges

    # Next pass: the VM and its port were both deleted in OpenStack.
    _set_conn(monkeypatch, _FakeConn(hypervisors=[_hv("compute1")]))
    result = topology_sync.sync_topology(db)

    assert result["swept_instances"] == 1
    assert result["swept_ports"] == 1
    assert store.instances == {}
    assert store.ports == {}
    assert store.instance_host_edges == {}
    assert store.instance_port_edges == {}


def test_instances_listing_failure_skips_only_workload_sweep(monkeypatch):
    db = _db()
    fake_driver = _patch_graph(monkeypatch)
    _patch_side_effects(monkeypatch)
    _set_conn(monkeypatch, _FakeConn(
        hypervisors=[_hv("compute1")],
        networks=[_network("net-1")],
        instances=[_server("vm-1")],
    ))
    topology_sync.sync_topology(db)
    assert "vm-1" in fake_driver.store.instances
    assert "net-1" in fake_driver.store.networks

    # Second pass: Nova servers is unreachable, but Neutron is fine and
    # vm-1 is now gone.
    _set_conn(monkeypatch, _FakeConn(
        hypervisors=[_hv("compute1")],
        networks=[_network("net-1")],
        instances_raise=True,
    ))
    result = topology_sync.sync_topology(db)

    assert result["workload_topology_ok"] is False
    assert result["network_topology_ok"] is True
    assert result["complete_picture"] is True
    # Workload sweep did not run on a partial picture, so the
    # now-unreported instance is left alone rather than deleted.
    assert "vm-1" in fake_driver.store.instances
    assert result["swept_instances"] == 0
