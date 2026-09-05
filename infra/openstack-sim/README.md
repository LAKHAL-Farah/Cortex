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
  pointing back at itself for compute/block-storage/network), `GET
  /v3/projects` (one seed project, `id: sandbox-project` / `name: admin`
  -- what `quota_budget_monitor.py`'s project loop iterates over)
- Nova: `GET /os-hypervisors[/detail]`, `GET /os-services`, `GET /limits`
  (absolute quota usage/caps for the seed project -- what
  `quota_budget_monitor.py`'s capacity_cap check reads)
- Cinder: `GET /volume/v3` (version discovery), `GET /volume/v3/os-services`
  -- catalog `type` is `block-storage` (that's the literal string
  `openstacksdk`'s `conn.block_storage` proxy looks up, not `cinder` or
  `volume`), `GET /volume/v3/limits` (volume/gigabyte quota usage/caps)
- Neutron: `GET /networks`, `/subnets`, `/routers`, `/floatingips`, `/agents`,
  `/agents/{id}/dhcp-networks`, `/agents/{id}/l3-routers` (the DHCP/L3
  hosting-endpoint calls `topology_sync.py`'s Phase 3 needs), `GET /ports`
- Nova (continued): `GET /servers[/detail]` -- `topology_sync.py`'s Phase 6
  needs

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

Plus three VMs (`SERVERS`) on `sandbox-net`, wired to it via three ports
(`PORTS`) -- `sandbox-vm-1` on `compute1-sim`, `sandbox-vm-2` on
`compute2-sim` (so a sync exercises the Instance-[:RUNS_ON]->Node edge
against both hypervisors), and `sandbox-vm-3-broken`, deliberately seeded
in `ERROR` status with its port `DOWN`/`admin_state_up: false` -- a
realistic broken pairing (a failed port bind commonly leaves the instance
stuck in `ERROR` too) so there's always a real problem for the planned
network-topology visualization to show, the same way `ROUTERS[0]`/
`NEUTRON_AGENTS` above always have one real, healthy structural edge to
test against.

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

## Testing the workload topology (Phase 6) against this sim

With the sandbox stack up, trigger a sync pass the same way as the Phase
2/3 section above:

```bash
docker compose exec api python3 -c "
from app.db import SessionLocal
from app.services.topology_sync import sync_topology
import json
with SessionLocal() as db:
    print(json.dumps(sync_topology(db), indent=2))
"
```

Expect `instances: 3, ports: 3, workload_topology_ok: true` added to the
summary alongside the Phase 2/3 counts. Then, via `docker compose exec
neo4j cypher-shell`:

```cypher
// Every VM, which hypervisor it's on, and its flavor.
MATCH (i:Instance) RETURN i.name, i.status, i.flavor_name;

// RUNS_ON: which VM is on which hypervisor -- sandbox-vm-1/compute1-sim,
// sandbox-vm-2/compute2-sim, sandbox-vm-3-broken/compute1-sim.
MATCH (i:Instance)-[:RUNS_ON]->(n:Node) RETURN i.name, n.id;

// HAS_PORT + CONNECTS chained: VM -> its port -> the subnet it's on.
MATCH (i:Instance)-[:HAS_PORT]->(p:Port)-[:CONNECTS]->(s:Subnet)
RETURN i.name, i.status, p.status, p.admin_state_up, s.id;
```

The last query is the one to look at for the deliberately-broken case:
`sandbox-vm-3-broken` should show `i.status: "ERROR"` alongside its port's
`p.status: "DOWN"`, `p.admin_state_up: false` -- a real, correlated
instance+port problem for whatever reads this later (the network agent,
the planned topology visualization) to have something to actually surface.

To see the mark-and-sweep in action here too, comment out `SERVERS[2]`
(and its matching `PORTS[2]`) in `app.py`, restart `openstack-sim`, run
the sync again, and confirm both the Instance and Port vertex (and the
RUNS_ON/HAS_PORT/CONNECTS edges pointing at them) are gone from the graph.

To see a live migration in action, leave the seed data alone and instead
change `SERVERS[0]`'s `"OS-EXT-SRV-ATTR:hypervisor_hostname"` from
`"compute1-sim"` to `"compute2-sim"`, restart, and re-sync -- the first
query above should now show `sandbox-vm-1` on `compute2-sim`, with no
leftover edge to `compute1-sim`.

## Testing the Prometheus cross-check (Phase 4) against this sim

Phase 4 (`prometheus_health.py`, see
`docs/architecture/adr-0003-prometheus-cross-check.md`) doesn't talk to
`openstack-sim` at all -- it only reads `up{job="node_exporter"}` from the
`prometheus` container and writes `Node.health`/`Service.state` to Neo4j.
It does depend on a Phase 2/3 sync having already run at least once (so
there's a `:Node`/`:Service` graph for it to overlay onto), and on
`compute1-sim`/`compute2-sim`/`controller-sim`/`storage-sim` actually
running `node_exporter` and being in Prometheus's file_sd targets --
both already true once you've registered the sandbox hypervisors the
normal way (a `sync_topology()` pass installs `node_exporter` on any new
hypervisor and regenerates file_sd; see `ansible_runner.install_node_exporter`).

With the sandbox stack up and at least one `sync_topology()` pass done:

```bash
# Run one Phase 4 pass (same call main.py's scheduler makes every
# PROMETHEUS_HEALTH_SYNC_INTERVAL_SECONDS, default 30s)
docker compose exec api python3 -c "
from app.services.prometheus_health import sync_prometheus_health
import json
print(json.dumps(sync_prometheus_health(), indent=2))
"
```

Then check the graph, e.g. via `docker compose exec neo4j cypher-shell`:

```cypher
// Every Node should have an explicit health -- 'up' for every sandbox
// node once node_exporter is installed and Prometheus has scraped it.
MATCH (n:Node) RETURN n.id, n.health, n.health_checked_at;

// openstack_state (OpenStack's raw report) vs. the reconciled state
// (cross-checked against the host's Node.health) -- should agree ('up'/
// 'up') for everything healthy in the sandbox.
MATCH (s:Service) RETURN s.id, s.openstack_state, s.state;
```

To exercise the actual disagreement case (`openstack_state: up`,
`Node.health: down` -> reconciled `state: unreachable`), stop one of the
sim containers so Prometheus can no longer scrape its `node_exporter`
while `openstack-sim` still reports its services as `up` (it's just
static seed data in `app.py`, unaffected by the container's real state):

```bash
docker compose stop compute1-sim
# wait for Prometheus's scrape_interval (20s) + a bit, then re-run the
# Phase 4 pass above
```

Expect `nova-compute@compute1-sim` (and `neutron-openvswitch-agent@compute1-sim`)
to show `openstack_state: up`, `state: unreachable`, and
`compute1-sim`'s `Node.health` to read `down`. `docker compose start
compute1-sim` and run another pass to see it flip back to `up`/`up`.

## Testing the topology API (Phase 5) against this sim

`routers/topology.py` is the read-only HTTP surface over everything
Phases 2-4 write to Neo4j, plus a `GET /health` backed by the new
`topology_sync_runs` Postgres table (see `models.TopologySyncRun` and
`main.py`'s `_run_periodic_recorded`). It doesn't talk to `openstack-sim`
directly, but it's only interesting to look at once at least one Phase 2/3
sync (and ideally one Phase 4 pass) has actually run against this sim --
otherwise the graph endpoints just return empty lists and `/health`
reports `"unknown"` for both sync types.

With the sandbox stack up and at least one of each pass done (see the two
sections above), curl the five endpoints from the host (the base compose
file already publishes `api` on `127.0.0.1:8000`):

```bash
# Whole graph, flattened for a generic graph-viz client
curl -s http://127.0.0.1:8000/api/v1/topology/graph | python3 -m json.tool

# One vertex (any label -- a hypervisor Node, a Service, a Network, ...)
# plus its immediate neighbors
curl -s http://127.0.0.1:8000/api/v1/topology/nodes/compute1-sim | python3 -m json.tool

# Every Service, with the Node it RUNS_ON and both openstack_state (raw,
# Phase 2/3) and state (Prometheus-reconciled, Phase 4)
curl -s http://127.0.0.1:8000/api/v1/topology/services | python3 -m json.tool

# Every Network with its subnets/gateway routers/floating IPs/serving
# DHCP+L3 agents nested inline
curl -s http://127.0.0.1:8000/api/v1/topology/networks | python3 -m json.tool

# Sync-loop health -- from topology_sync_runs, not a live Neo4j query
curl -s http://127.0.0.1:8000/api/v1/topology/health | python3 -m json.tool
```

Expected, once both sync loops have completed at least one pass against
this sim: `/topology/graph`'s `nodes` includes `compute1-sim` (label
`Node`) and `sandbox-net` (label `Network`); `/topology/nodes/compute1-sim`
lists `nova-compute@compute1-sim` and its OVS agent among its incoming
`RUNS_ON` neighbors; `/topology/networks` shows `sandbox-net` with
`sandbox-router` in `gateway_routers` and the DHCP agent (`a2`, see the
seed data above) in `serving_agents` (the L3 agent `a1` SERVES the
*router*, not the network, so look for it via
`/topology/nodes/sandbox-router` or `/topology/services` instead); and
`/topology/health` reports `"status": "ok"` for both `openstack` and
`prometheus_health` (assuming both sims are up and unmodified).

To see `/topology/health` report something other than `"ok"`, stop
`openstack-sim` (`docker compose stop openstack-sim`) and wait for the
next `TOPOLOGY_SYNC_INTERVAL_SECONDS` tick (or trigger one manually, same
as the Phase 2/3 section above). Every OpenStack listing call in
`sync_topology` is wrapped individually (see that function's docstring),
so the pass doesn't raise -- it still returns a summary, just with
`complete_picture: false` -- which `main.py`'s `_topology_sync_status`
classifies as `status: "degraded"` rather than `"failed"` (see
`_run_periodic_recorded`). `"failed"` is reserved for the rarer case of
the pass raising before returning any summary at all (e.g. a Postgres
outage, since `sync_topology` also reads/writes `nodes` there). `docker
compose start openstack-sim` and wait for/trigger another pass to see
`openstack`'s status recover to `"ok"` on its own.

## Testing the quota/budget monitor against this sim

`services/quota_budget_monitor.py` (Phase 7) doesn't touch Neo4j/Postgres'
topology tables at all -- it only reads `GET /v3/projects` + `GET
/v2.1/limits` + `GET /volume/v3/limits` from `openstack-sim` and writes
`quota_alerts` rows to Postgres. It has no dependency on a Phase 2/3 sync
having run first.

With the sandbox stack up (`docker compose -f docker-compose.yml -f
docker-compose.sandbox.yml up -d`), the `api` service's
`QUOTA_PROJECT_BUDGETS_EUR` override (see `docker-compose.sandbox.yml`)
gives the seed project a 5 EUR/month budget, deliberately low so both
alert kinds have something to fire on immediately:

```bash
docker compose exec api python3 -c "
from app.db import SessionLocal
from app.services.quota_budget_monitor import check_quota_and_budget
import json
with SessionLocal() as db:
    print(json.dumps(check_quota_and_budget(db), indent=2))
"
```

Expect `{"projects_checked": 1, "warning_count": 1, "critical_count": 1}`
-- `NOVA_ABSOLUTE_LIMITS`'s `totalCoresUsed: 18` against `maxTotalCores:
20` (90%) is a capacity_cap **warning**, and the estimated cost of that
same usage (18 vCPUs + 24 GB RAM + 160 GB volumes, at
`QUOTA_COST_PER_*_MONTH_EUR`'s default rates) comes out well over the 5
EUR budget, so it's also a budget_cap **critical**. Then hit the API
directly:

```bash
curl -s http://127.0.0.1:8000/api/v1/quotas/alerts | python3 -m json.tool
```

Expect two rows for `project_id: "sandbox-project"` -- one
`breach_type: "capacity_cap"`, `resource: "vcpus"`, `severity: "warning"`,
message starting `CAPACITY CAP breach:`; one `breach_type: "budget_cap"`,
`resource: "estimated_cost_eur"`, `severity: "critical"`, message starting
`BUDGET CAP breach:`. Edit `NOVA_ABSOLUTE_LIMITS`/`CINDER_ABSOLUTE_LIMITS`
in `app.py` (or `QUOTA_PROJECT_BUDGETS_EUR` in `docker-compose.sandbox.yml`)
and re-run the check to see a row settle back to `severity: "normal"`
(`message: null`) instead of disappearing -- same upsert-not-delete
convention as `anomaly_flags`.

## Testing the quota/budget frontend against this sim

`services/web/app/quotas/page.tsx` (`components/QuotaBudgetView.tsx`) is a
browser client for the API above -- summary tiles, a filterable/searchable
list or per-project grouped view of current breaches, and a "Check now"
button (`components/QuotaResyncButton.tsx`) that triggers
`POST /api/v1/quotas/resync` on demand, via three proxy routes:

| Proxy route                          | Backend endpoint                        |
|----------------------------------------|-------------------------------------------|
| `GET /api/quotas/alerts`               | `GET /api/v1/quotas/alerts`               |
| `GET /api/quotas/alerts/{projectId}`   | `GET /api/v1/quotas/alerts/{project_id}`  |
| `POST /api/quotas/resync`              | `POST /api/v1/quotas/resync`              |

With the sandbox stack up and at least one `check_quota_and_budget()` pass
done (see above -- or just click "Check now" on the page itself), open
`http://127.0.0.1:3000/quotas`. Expect the "Critical breaches" and
"Warnings" tiles to both read 1, one card for `sandbox-project`'s vCPUs
(capacity cap, ~90%) and one for its estimated cost (budget cap, well over
100%), and each card's message to explicitly say `CAPACITY CAP` or
`BUDGET CAP` rather than a generic "threshold exceeded".



`services/web/app/topology/page.tsx` is a browser client for the Phase 5
API above -- a force-directed graph (`components/TopologyGraph.tsx`, via
`react-force-graph-2d`) with a click-through detail panel
(`components/TopologyDetailPanel.tsx`) and a sync-staleness badge
(`components/TopologyHealthBadge.tsx`). It's all read-only, same as the
API it calls, via three new Next.js proxy routes that mirror the existing
`/api/dashboard`, `/api/anomalies`, etc. pattern:

| Proxy route                        | Backend endpoint                     |
|-------------------------------------|---------------------------------------|
| `GET /api/topology`                 | `GET /api/v1/topology/graph`          |
| `GET /api/topology/health`          | `GET /api/v1/topology/health`         |
| `GET /api/topology/nodes/{id}`      | `GET /api/v1/topology/nodes/{id}`     |

With the sandbox stack up and at least one Phase 2/3 sync pass done (see
above -- the page renders an empty-state message rather than an error if
the graph is empty, but it's more interesting to look at with real data):

```bash
cd infra
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up -d --build web
```

Then open `http://127.0.0.1:3000/topology` in a browser (the base compose
file already publishes `web` there). Expect to see:

- `compute1-sim`/`compute2-sim` (role `compute`, `--role-compute` orange)
  and `controller-sim` (role `controller`, `--role-controller` purple)
  as circular markers, colored the same way `NodeCard.tsx` already colors
  them on the dashboard.
- `nova-compute@compute1-sim` and friends as smaller `Service`-colored
  circles, connected to their hypervisor by a solid `RUNS_ON` edge.
- `sandbox-net`/`sandbox-storage-net` as square `Network` markers, with
  `sandbox-router` connected by a solid `CONNECTS` edge and the DHCP
  agent's `Service` vertex connected by a dashed `SERVES` edge.
- A "Synced Xm ago" badge in the top-right, colored `--ok` green -- backed
  by `/topology/health`, so it reflects actual sync-run history rather
  than just "the page loaded fine".
- Clicking any vertex opens a right-hand detail panel (properties +
  incoming/outgoing neighbors, via `/api/topology/nodes/{id}`) -- e.g.
  clicking `compute1-sim` should list `nova-compute@compute1-sim` and its
  OVS agent among its incoming `RUNS_ON` neighbors, matching the
  `/topology/nodes/compute1-sim` API example above.

To see the staleness badge turn amber/red, stop `openstack-sim` the same
way as the Phase 5 section above and wait for/trigger a sync pass -- the
badge should flip to "Degraded" (amber, `--warn`) and the tooltip should
name which of the two sync loops (`openstack` vs `prometheus_health`) is
behind.

No live Neo4j/Postgres needed to sanity-check the frontend code itself
(lint/typecheck/build) without the rest of the sandbox stack running:

```bash
cd services/web
npm ci
npm run lint
npx tsc --noEmit
npm run build
```

