# ADR-0003: Prometheus cross-check — Node health + Service state reconciliation

**Status:** Accepted (Phase 4 of the topology-graph feature)
**Related code:** `services/api/app/services/prometheus_health.py`,
`services/api/app/services/topology_sync.py` (Phase 2/3),
`services/api/app/services/prometheus_client.py`, `services/api/app/main.py`
**Related docs:** `docs/architecture/adr-0002-topology-graph.md` (Phase 2/3
decisions this builds on)

## Context

Phases 2/3 populate the topology graph entirely from what OpenStack
self-reports: a Nova/Cinder/Neutron service is `up` because Nova/Cinder/
Neutron say so. That is one source of truth, and it can be wrong in a
specific way OpenStack itself can't detect — a control-plane service can
still be marked `up` in its own service table for up to that service's own
report interval after the host it runs on has actually gone unreachable.
Cortex already scrapes `node_exporter` on every registered `Node` via
Prometheus (see `infra/prometheus/prometheus.yml`, `metrics_collector.py`);
Phase 4 is about actually using that second, independent signal instead of
letting it sit unused next to the graph.

Two things needed deciding:

1. How does Prometheus-observed liveness get onto the graph at all?
2. When OpenStack's `Service.state` and Prometheus's view of the host it
   runs on disagree, which one does the graph believe?

## Decisions

### 1. `Node.health`, sourced from `up{job="node_exporter"}`, matched on the `node` label

`prometheus_sd.py` already writes a `node` label (the hostname) onto every
file_sd target it generates for Prometheus — the same hostname
`topology_sync.py` uses as a `Node` vertex's `id`. `up{job="node_exporter"}`
is grouped by that label, not `instance` (`ip:port`), so it joins onto
`Node.id` directly with no extra lookup table.

Every `Node` currently in the graph gets an **explicit** value each pass —
`"up"`, `"down"`, or `"unknown"` — never left unset:

- `"up"` — Prometheus reports `up == 1` for that hostname.
- `"down"` — Prometheus reports `up == 0` (the target is known and being
  scraped, but the scrape is failing).
- `"unknown"` — Prometheus has no opinion at all: the query itself failed
  (Prometheus unreachable), or the hostname isn't in the result set yet
  (newly registered, file_sd hasn't picked it up, or its `node` label
  wasn't set for some other reason).

`"unknown"` is a real, distinct state, not a synonym for `"down"`. Treating
"no data" as "down" would turn a Prometheus outage or a fresh, not-yet-scraped
node into a false alarm about the node itself. Keeping it separate means a
consumer can tell "this host is confirmed unreachable" apart from "we simply
don't know yet" — a distinction that also matters for decision 2 below.

Every `Node` is written explicitly (rather than only writing the ones
Prometheus has data for) so a node that Prometheus previously saw as `up`
and then stops reporting on entirely doesn't keep showing a stale `"up"`
from the last successful pass — it goes to `"unknown"` immediately.

### 2. `Service.state` reconciliation: OpenStack's `down` wins; a live host beats no data; a dead host on an `up` service is flagged, not overridden

Phase 2/3 stored OpenStack's own reported value directly as `Service.state`.
Phase 4 splits that into two properties:

- `Service.openstack_state` — OpenStack's raw, unmodified `up`/`down`
  (Nova/Cinder's `state` field, or the up/down Neutron's `is_alive` already
  gets mapped onto in `topology_sync.py`). Untouched by this phase; still
  written every `topology_sync` pass exactly as before, just under this
  name instead of `state`.
- `Service.state` — the **reconciled** value Phase 4 computes from
  `openstack_state` plus the Prometheus `health` of the `Node` the service
  `RUNS_ON`:

  | `openstack_state` | host `health` | reconciled `state` | why |
  |---|---|---|---|
  | `down` | *(any)* | `down` | A service-level heartbeat reporting `down` is more specific than a host-level `up`/`down` — Prometheus has no visibility into whether the individual service process is actually running, so it can never override OpenStack's own `down`. |
  | `up` | `down` | `unreachable` | A genuine disagreement: Nova/Cinder/Neutron still lists the service as `up`, but the host it runs on doesn't answer `node_exporter` scrapes at all. Nova's service table can lag reality by up to its own report interval, so this is treated as its own state rather than silently trusting either source — it's exactly the case this phase exists to surface. |
  | `up` | `up` | `up` | Both sources agree; nothing to reconcile. |
  | `up` | `unknown` | `up` | No cross-check data yet (new node, Prometheus outage) — fall back to OpenStack rather than penalizing a service for the health overlay simply not having run against its host yet. |
  | anything else / `None` | *(any)* | passthrough | No defined cross-check for values outside `up`/`down` (e.g. a service whose host never resolved to a `Node` at all). |

The asymmetry is intentional: a service-level `down` is trusted outright,
but an `up` is only *demoted* (to `unreachable`, not flipped to `down`) when
the host is *confirmed* unreachable — never on `unknown`. This keeps
`Service.state` from getting noisier than either individual source on its
own, while still surfacing the one disagreement that actually matters
operationally: "OpenStack still thinks this is fine, but the host has gone
dark."

`unreachable` is a new value (not reused from either source's own
vocabulary) specifically so a consumer of the graph can distinguish "we
observed a live disagreement" from either source's own `down`.

### 3. A separate periodic loop, not folded into `topology_sync`'s pass

`sync_prometheus_health()` runs on its own schedule
(`PROMETHEUS_HEALTH_SYNC_INTERVAL_SECONDS`, default 30s), independently of
`topology_sync`'s 5-minute OpenStack poll. Prometheus's scrape interval
(`infra/prometheus/prometheus.yml`, 20s) means `up{}` data goes stale far
faster than hypervisor/service listings do, and querying Prometheus doesn't
touch OpenStack at all — tying the two together would either make health
data as slow as OpenStack polling, or make OpenStack polling as frequent as
Prometheus scraping for no reason. This doesn't conflict with adr-0002's
"one and only OpenStack polling loop" decision: Phase 4 never calls
`openstack.connect()`, it only reads from Prometheus and writes to Neo4j.

Every `sync_prometheus_health()` pass recomputes `state` for *every*
`Service` vertex from its stored `openstack_state`, not just the ones whose
host's `health` changed this tick — cheap at this scale (a few hundred
vertices, per adr-0002) and means a newly-`topology_sync`-created `Service`
(which only gets `openstack_state` written, not `state`) picks up its first
reconciled `state` on the next health pass rather than needing its own
special-cased initial value.

## Consequences

- `Service.state` in the graph is now Cortex's own derived judgment, not a
  direct copy of what OpenStack reports — consumers that want OpenStack's
  raw value read `Service.openstack_state` instead.
- A brand-new `Service` (or one whose host is brand-new) shows no `state`
  at all until the next `PROMETHEUS_HEALTH_SYNC_INTERVAL_SECONDS` tick
  (default: up to 30s) — `openstack_state` is available immediately from
  the same `topology_sync` pass that created it.
- `Node.health` and the `unreachable` `Service.state` value are new surface
  area for anything that later reads the graph (dashboards, alerting) —
  none of that exists yet (Phase 4 is graph-only, no new API router), so
  there are no existing consumers to update.

## Revisit when

- A `Service` binary exists whose liveness genuinely doesn't correlate with
  its host's `node_exporter` reachability (e.g. a service reachable over a
  network path Prometheus's scrape doesn't use) — the host-health
  cross-check would misfire for it specifically.
- Cortex grows a second, independent per-process liveness signal (rather
  than only host-level `up`) — the reconciliation table above would need a
  third input.
