# Topology API (Phase 5) — endpoint contract

**Related code:** `services/api/app/routers/topology.py`,
`services/api/app/graph_db.py` (read helpers),
`services/api/app/models.py` (`TopologySyncRun`)

## Why this doc exists

The topology-graph feature's task list (Phase 5) points at "the 5
endpoints from §7" of a design doc that isn't checked into this repo --
only `adr-0002-topology-graph.md` (auth/deployment) and
`adr-0003-prometheus-cross-check.md` (Phase 4's reconciliation rules) are.
Rather than guess at an external contract this repo has no copy of, this
implementation derives the 5 endpoints directly from the graph
Phases 2-4 already build (six vertex labels -- `Node`, `Service`,
`Network`, `Subnet`, `Router`, `FloatingIP` -- and three relationship
types -- `RUNS_ON`, `SERVES`, `CONNECTS`; see adr-0002/adr-0003) and from
the one concrete requirement given alongside it: a `/health` endpoint
backed by a sync-run metadata table. If the actual §7 contract turns up
later and differs, treat *that* as authoritative and update this doc (and
the router) to match -- everything below is this implementation's own
interpretation, not a transcription of an external spec.

## Endpoints

All five are read-only and live under `/api/v1/topology`. None of them
write to Neo4j or Postgres.

| Method | Path | Purpose |
|---|---|---|
| GET | `/graph` | The whole graph, flattened to `{nodes: [...], edges: [...]}` for a generic graph-visualization client. |
| GET | `/nodes/{vertex_id}` | One vertex of *any* label plus its immediate neighbors in both directions. |
| GET | `/services` | Every `:Service` vertex, with the `:Node` it `RUNS_ON` and both `openstack_state`/`state`. |
| GET | `/networks` | Every `:Network` vertex with its subnets/gateway routers/floating IPs/serving DHCP+L3 agents nested inline. |
| GET | `/health` | Latest run of each sync loop (`openstack`, `prometheus_health`), read from Postgres, not a live Neo4j query. |

### `/graph`, `/nodes/{vertex_id}`, `/services`, `/networks`

These are generic reads over the property graph, not four different
serializations invented independently -- `/graph` is the whole thing,
`/nodes/{id}` is one vertex's local neighborhood, and `/services`/
`/networks` are label-scoped views with the structurally-relevant
neighbors (per adr-0002/adr-0003's edges) nested inline so a client
doesn't have to make N+1 calls to render one network's subnets.

`vertex_id` matches whatever `id` topology_sync.py already gives that
vertex: a hostname for `:Node`, `{binary}@{host}` for `:Service` (Neutron
agents included), and the OpenStack resource UUID for `:Network`/
`:Subnet`/`:Router`/`:FloatingIP`. There's no separate "type" path
segment (e.g. `/nodes/nodes/{id}` vs. `/nodes/services/{id}`) because ids
are unique across the whole graph already (they come from different
OpenStack ID spaces) and a UI exploring the graph rarely knows a vertex's
label ahead of clicking into it.

A Neo4j query failure on any of these four returns `503`, not a `500` or
a `200` with an empty/partial body -- the graph being temporarily
unreachable is a distinct condition from "the graph is empty" (`200`
with `[]`/`{"nodes": [], "edges": []}`) or "that vertex doesn't exist"
(`404` from `/nodes/{vertex_id}`).

### `/health`

Deliberately *not* a live Neo4j query. The graph can look perfectly
healthy (last successful pass's data still sitting there) even if the
sync loop producing it has been silently failing for an hour -- there's
no "last updated" signal on the graph itself that would catch that. So
`/health` instead reads `topology_sync_runs` (see
`models.TopologySyncRun`), which `main.py`'s `_run_periodic_recorded`
appends to after *every* pass of either loop:

- `sync_type`: `"openstack"` (`topology_sync.sync_topology`, Phases 2/3)
  or `"prometheus_health"` (`prometheus_health.sync_prometheus_health`,
  Phase 4).
- `status`: `"ok"` (ran, everything it depends on succeeded), `"degraded"`
  (ran, but at least one dependency was skipped/unreachable -- summary
  still reflects whatever partial picture it got), or `"failed"` (raised
  before producing any summary).
- `summary`/`error`: whatever the pass returned, or the exception if it
  raised.

`GET /health`'s response is `{"status": ..., "syncs": {"openstack": ...,
"prometheus_health": ...}}` -- `status` is the worst of the two loops'
*latest* run (`"unknown"` if a loop has never completed a pass, e.g. right
after a fresh deploy), and `syncs` gives the latest run per loop so a
caller can see *which* one is degraded rather than just that something is.

This is intentionally a small, append-only table rather than one row per
sync_type upserted in place -- see `models.TopologySyncRun`'s docstring
for why a short history (not just "current status") is what actually
makes "did this recover on its own, or has it been down for an hour"
answerable from the endpoint.

## Testing

`services/api/tests/test_topology_router.py` covers all five endpoints:
the four graph reads against a small fake Neo4j session (same pattern as
`test_topology_sync.py`/`test_prometheus_health.py` -- no real Neo4j in
CI, see `.github/workflows/ci.yml`), and `/health` against real Postgres
the same way `test_baselines_router.py` does.

For an end-to-end check against the sandbox (`infra/openstack-sim` fake
OpenStack control plane + real Postgres/Neo4j), see "Testing the topology
API (Phase 5) against this sim" in `infra/openstack-sim/README.md`.
