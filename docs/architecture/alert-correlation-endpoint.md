# Graph-correlated alerts (Phase 6) — endpoint contract

**Related code:** `services/api/app/services/alert_correlation.py`,
`services/api/app/routers/anomalies.py` (`GET /incidents`),
`services/web/components/AlertsView.tsx` (incident cards),
`services/web/components/TopologyView.tsx` (`?highlight=` deep link)

## Why this doc exists

Same reason `topology-api-endpoints.md` exists: this endpoint isn't a
transcription of an external spec, it's this implementation's own
interpretation of the action plan doc for "Graph-Correlated Alerts",
derived directly from what Phases 2-5 already shipped (the topology
graph and its five read-only endpoints) plus what Phase 1's anomaly
detector already produces (`AnomalyFlag` rows, served by
`/api/v1/anomalies`). If a future doc supersedes this one, treat that as
authoritative and update this file (and the router) to match.

## The endpoint

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/anomalies/incidents` | Every currently-open `AnomalyFlag`, grouped into incidents via the topology graph. |

`GET /api/v1/anomalies` and `/history` are unchanged by this phase --
they're still the ungrouped, per-metric views. `/incidents` is a grouped
*view* over the exact same open-alert set `/api/v1/anomalies` returns,
not a replacement for it: every open alert appears in exactly one
incident, so a client can group by `incident_id` uniformly instead of
special-casing "this alert has no correlated peer". A singleton incident
(`member_count: 1`) is meant to render identically to how a standalone
alert reads today -- see `AlertsView.tsx`, which only shows the new
incident-card UI once `member_count > 1`.

### What "correlated" means

Two open alerts are correlated when the vertices they anchor to in the
graph are the same vertex, or are connected by a path of length <=2 over
`RUNS_ON`/`SERVES`/`CONNECTS` edges, direction ignored. An `AnomalyFlag`'s
`hostname` is resolved to a graph vertex the same way `/nodes/{vertex_id}`
resolves any id: `MATCH (v) WHERE v.id = $hostname`, whatever label that
vertex turns out to have. In practice this is almost always a `:Node`
(today's only detector scores node_exporter metrics), so the mapping is
the identity function -- but nothing about `AnomalyFlag` ties `hostname`
to `:Node` specifically, so a synthetic or future alert anchored directly
at a `:Service`/`:Network` vertex id is grouped the same way with no
extra code. An alert whose `hostname` doesn't resolve to any vertex (e.g.
topology sync hasn't run yet), or whose vertex has no path within 2 hops
to any other open alert's vertex, stays its own singleton incident --
same as today.

### Response shape

```json
[
  {
    "incident_id": "3f1a9c2b7e04",
    "severity": "critical",
    "member_count": 2,
    "root_cause_guess": { "vertex_id": "nova-compute@compute-02", "label": "Service" },
    "narrative": "compute-02 is under CPU pressure and its Nova compute service has gone unreachable.",
    "members": [ { "hostname": "...", "metric_name": "...", "severity": "...", "...": "..." } ],
    "graph_path": {
      "vertex_ids": ["compute-02", "nova-compute@compute-02"],
      "edges": [{ "type": "RUNS_ON", "source": "nova-compute@compute-02", "target": "compute-02" }]
    }
  }
]
```

- `incident_id` is derived deterministically from the member set (a short
  hash of their `hostname`/`metric_name` pairs), not a random uuid --
  incidents aren't persisted anywhere (see "out of scope", below), so
  this is what keeps the id stable across the web app's 5s poll as long
  as the underlying open-alert set hasn't changed, without a database row
  to key off of.
- `severity` is the worst severity among the incident's members.
- `root_cause_guess` is a best-effort guess (most severe member's vertex,
  earliest-detected as the tiebreak), not a certainty -- named
  `_guess` deliberately.
- `narrative` is templated off the vertex labels/properties in the
  incident, not free-text generated -- deterministic and unit-testable
  (see `test_alert_correlation.py`).
- `graph_path` is `null` if the root cause's alert never resolved to a
  vertex (nothing to highlight) or the graph was unreachable (below).
  Otherwise it's the union of shortest paths from the root cause's vertex
  out to every other distinct anchor in the incident -- enough for the
  web app's "View on graph" link (`/topology?highlight=<vertex_ids>`) to
  highlight the connected region.

### Graph-unreachable fallback

Unlike the four Phase 5 graph reads (which return `503` on a Neo4j
failure), `/incidents` degrades to one incident per open alert --
identical to `/api/v1/anomalies`'s own ungrouped shape, just wrapped --
rather than failing the whole request. Postgres-backed alerting has to
keep working even when Neo4j, or just its sync loop, is down; a
correlation feature going degraded shouldn't take basic alerting down
with it.

## Testing

`services/api/tests/test_alert_correlation.py` covers the grouping logic
itself (union-find over a small hand-built graph, narrative templating,
the 2-hop boundary, and the graph-unreachable fallback) against a fake
Neo4j session capable of answering reachability/shortest-path queries,
not just fixed row sets -- same fake-session convention as
`test_topology_sync.py`/`test_prometheus_health.py`, extended because this
module's queries are traversals rather than direct lookups.
`services/api/tests/test_anomalies_router.py` covers the endpoint itself
the same way `test_topology_router.py` covers its four graph-backed
endpoints: real Postgres via `SessionLocal`, faked Neo4j.

For an end-to-end check against the sandbox, seed two correlated
`AnomalyFlag` rows directly (a compute node's `cpu_usage` anomaly plus its
Nova service's `state` flipping to `unreachable`, reusing whatever the
Phase 4 Prometheus cross-check already writes for that case) and confirm
`GET /api/v1/anomalies/incidents` returns one incident, `/alerts` in the
web app renders it as a single expandable card, and its "View on graph"
link highlights the Node and Service vertices on `/topology`.
