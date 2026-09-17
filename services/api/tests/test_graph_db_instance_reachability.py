"""Tests for graph_db.fetch_instance_reachability -- the topology-graph
read services/instance_exposure.py's Sec-5b check uses to resolve an
instance's own ports/fixed IPs/hypervisor and any associated floating IP,
instead of re-querying Nova/Neutron for facts topology_sync.py's Phase 6
already synced into the graph. Neo4j is faked the same way
test_topology_router.py/test_topology_sync.py fake it -- a small
in-memory result set per query shape, matched on a distinctive substring
of the Cypher text, no real Neo4j instance required.
"""
from app import graph_db


class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    """Backs one small sample: instance "vm-1" has two ports ("port-1"
    with fixed IP 10.0.1.101, "port-2" with fixed IP 10.0.1.102), and
    RUNS_ON hypervisor "compute1-sim". Instance "vm-unsynced" has no
    :Instance vertex at all -- the "not yet synced" case.
    `has_floating_ip` controls whether the second query (FloatingIP
    lookup by fixed_ip_address) reports a match for port-1's address.
    """

    def __init__(self, has_floating_ip: bool = True):
        self._has_floating_ip = has_floating_ip

    def run(self, query, **kwargs):
        if "MATCH (i:Instance {id: $instance_id})" in query:
            instance_id = kwargs["instance_id"]
            if instance_id != "vm-1":
                return _FakeResult([])
            return _FakeResult([{
                "instance": {"id": "vm-1", "name": "sandbox-vm-1", "status": "ACTIVE"},
                "ports": [
                    {"id": "port-1", "fixed_ip_address": "10.0.1.101", "device_owner": "compute:nova"},
                    {"id": "port-2", "fixed_ip_address": "10.0.1.102", "device_owner": "compute:nova"},
                ],
                "hypervisor_hostname": "compute1-sim",
            }])
        if "UNWIND $fixed_ips AS fixed_ip" in query:
            if self._has_floating_ip and "10.0.1.101" in kwargs["fixed_ips"]:
                return _FakeResult([{"floating_ip_address": "203.0.113.50"}])
            return _FakeResult([])
        raise AssertionError(f"unexpected query in fake session: {query}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self, has_floating_ip: bool = True):
        self._has_floating_ip = has_floating_ip

    def session(self):
        return _FakeSession(self._has_floating_ip)


def test_returns_none_for_an_instance_not_yet_synced_into_the_graph(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver())

    assert graph_db.fetch_instance_reachability("vm-unsynced") is None


def test_resolves_ports_hypervisor_and_associated_floating_ip(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver())

    facts = graph_db.fetch_instance_reachability("vm-1")

    assert facts["instance_id"] == "vm-1"
    assert facts["instance_name"] == "sandbox-vm-1"
    assert facts["hypervisor_hostname"] == "compute1-sim"
    assert facts["port_ids"] == ["port-1", "port-2"]
    assert facts["fixed_ip_addresses"] == ["10.0.1.101", "10.0.1.102"]
    assert facts["floating_ip_address"] == "203.0.113.50"


def test_no_floating_ip_associated_leaves_it_none(monkeypatch):
    # Neither fixed IP has a matching :FloatingIP.fixed_ip_address --
    # the caller falls back to the first fixed IP instead.
    monkeypatch.setattr(graph_db, "driver", _FakeDriver(has_floating_ip=False))

    facts = graph_db.fetch_instance_reachability("vm-1")

    assert facts["floating_ip_address"] is None
    assert facts["fixed_ip_addresses"] == ["10.0.1.101", "10.0.1.102"]
