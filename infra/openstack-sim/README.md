# openstack-sim — fake OpenStack control plane for the sandbox

Same idea as `infra/ansible-sandbox`'s `controller-sim`/`compute*-sim`
containers (fake infra nodes so Ansible has something real to SSH into
without a live cluster), but for the OpenStack side: `topology_sync.py`
(Phase 2) needs Keystone/Nova/Neutron to talk to, and there's no real
OpenStack in the sandbox. This gives it one.

It's a small FastAPI app (`app.py`) that implements just enough of the
Keystone v3 / Nova v2.1 / Cinder v3 / Neutron v2.0 REST surface for
`openstacksdk`'s list calls to work:

- Keystone: `POST /v3/auth/tokens` (issues a fake token + service catalog
  pointing back at itself for compute/block-storage/network)
- Nova: `GET /os-hypervisors[/detail]`, `GET /os-services`
- Cinder: `GET /volume/v3` (version discovery), `GET /volume/v3/os-services`
  -- catalog `type` is `block-storage` (that's the literal string
  `openstacksdk`'s `conn.block_storage` proxy looks up, not `cinder` or
  `volume`)
- Neutron: `GET /networks`, `/subnets`, `/routers`, `/floatingips`, `/agents`,
  `/agents/{id}/dhcp-networks`, `/agents/{id}/l3-routers` (the DHCP/L3
  hosting-endpoint calls `topology_sync.py`'s Phase 3 needs)

**Not a real OpenStack** — no writes, no auth checks (any username/password
in `clouds.sandbox.yaml` is accepted), no other endpoints. It exists purely
so `topology_sync.py` has something to call and Neo4j has something real to
`MERGE` during local testing.

## Seed data

The hypervisors/agents intentionally reuse the hostnames already defined in
`infra/ansible-sandbox/inventory/hosts.ini`, so a topology sync in the
sandbox produces a graph that lines up with what Prometheus/the node
registry already know about:

| Hypervisor      | host_ip    | matches                          |
|------------------|-----------|-----------------------------------|
| compute1-sim     | 10.0.1.21 | `[computes]` in ansible-sandbox   |
| compute2-sim     | 10.0.1.22 | `[computes]` in ansible-sandbox   |

Cinder services reuse `controller-sim`/`storage-sim` the same way -- one
`cinder-scheduler` on `controller-sim`, and two on `storage-sim`
(`cinder-backup` with a plain host, `cinder-volume` as `storage-sim@lvmdriver-1`
so `topology_sync._parse_cinder_host`'s `host@backend` split has something
real to exercise).

Plus one network/subnet pair per sandbox subnet (`10.0.1.0/24`,
`10.0.2.0/24`), one router (gatewayed onto `sandbox-net`, so `topology_sync`
has something real to build a Router-[:CONNECTS]->Network edge from), and
one floating IP (associated with both `sandbox-net` and the router, for the
FloatingIP CONNECTS edges). The DHCP agent (`a2`) hosts both networks and
the L3 agent (`a1`) hosts the router -- `DHCP_AGENT_NETWORKS`/
`L3_AGENT_ROUTERS` at the top of `app.py`, backing the two hosting-endpoint
routes above -- so a sync produces DHCP/L3 agent Service-[:SERVES]->
Network/Router edges too. The two `neutron-openvswitch-agent` agents (`a3`,
`a4`, one per compute node) intentionally have no hosting-endpoint mapping
-- OVS agents don't have one in real Neutron either, they're only synced as
plain RUNS_ON services. Edit the lists at the top of `app.py` directly if
you need different/more topology to test against -- there's no database
backing this, it's just Python literals.

## Running it standalone (without the rest of the sandbox stack)

```bash
cd infra/openstack-sim
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 5000
```

Then point `openstacksdk` at it with `clouds.sandbox.yaml` in this
directory (set `OS_CLIENT_CONFIG_FILE` to its path, `OS_CLOUD=cortex-reader`,
and change `auth_url` in the file to `http://127.0.0.1:5000/v3` if you're
not going through Docker's DNS).

## Running it as part of the sandbox stack

Already wired into `docker-compose.sandbox.yml` — see that file's
`openstack-sim` service and the `api` service's `clouds.yaml` mount. Bring
it up the same way as the rest of the sandbox:

```bash
cd infra
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up -d
```

`api` gets `OS_CLIENT_CONFIG_FILE=/etc/openstack/clouds.yaml` (mounted from
`clouds.sandbox.yaml` in this directory) and `OS_CLOUD=cortex-reader` from
the base compose file, so `openstack.connect()` inside the `api` container
resolves straight to `openstack-sim` with zero extra setup — no real
`clouds.yaml`, no real `CORTEX_OPENSTACK_HOST_DIR` needed for sandbox
testing.

## Verifying it from inside the api container

```bash
docker compose exec api python3 -c "
import openstack
conn = openstack.connect()
print([h.name for h in conn.compute.hypervisors()])
print([n.name for n in conn.network.networks()])
print([(s.binary, s.host) for s in conn.block_storage.services()])
routers = list(conn.network.routers())
print([(r.name, r.external_gateway_info['network_id']) for r in routers])
for a in conn.network.agents():
    if a.agent_type == 'DHCP agent':
        print('DHCP', a.host, '->', [n.name for n in conn.network.dhcp_agent_hosting_networks(a)])
    if a.agent_type == 'L3 agent':
        print('L3', a.host, '->', [r.name for r in conn.network.agent_hosted_routers(a)])
"
```

Expected: `['compute1-sim', 'compute2-sim']`,
`['sandbox-net', 'sandbox-storage-net']`,
`[('cinder-scheduler', 'controller-sim'), ('cinder-backup', 'storage-sim'), ('cinder-volume', 'storage-sim@lvmdriver-1')]`,
`[('sandbox-router', '8f3f0f4a-0000-0000-0000-000000000001')]`,
`DHCP controller-sim -> ['sandbox-net', 'sandbox-storage-net']`, and
`L3 controller-sim -> ['sandbox-router']`.

## Testing the full topology sync (Phases 2 + 3) against this sim

With the sandbox stack up (`docker compose -f docker-compose.yml -f
docker-compose.sandbox.yml up -d`), trigger a sync pass and inspect what
landed in Neo4j:

```bash
# Run one sync pass (same call main.py's scheduler makes on its interval)
docker compose exec api python3 -c "
from app.db import SessionLocal
from app.services.topology_sync import sync_topology
import json
with SessionLocal() as db:
    print(json.dumps(sync_topology(db), indent=2))
"
```

Expect `networks: 2, subnets: 2, routers: 1, floating_ips: 1,
dhcp_hosting_edges: 2, l3_hosting_edges: 1, network_topology_ok: true` in
the summary. Then, e.g. via `docker compose exec neo4j cypher-shell`:

```cypher
// Structural CONNECTS edges: Subnet->Network, Router->Network (gateway),
// FloatingIP->Network/Router
MATCH (a)-[:CONNECTS]->(b) RETURN labels(a), a.name, labels(b), b.name;

// Agent hosting: DHCP/L3 agent Service -[:SERVES]-> Network/Router
MATCH (s:Service)-[:SERVES]->(t) RETURN s.id, labels(t), t.name;
```

To see the mark-and-sweep in action, comment out `ROUTERS[0]` (or any
other entity) in `app.py`, restart the `openstack-sim` container, run the
sync again, and confirm the corresponding vertex (and any CONNECTS/SERVES
edge pointing at it) is gone from the graph.
