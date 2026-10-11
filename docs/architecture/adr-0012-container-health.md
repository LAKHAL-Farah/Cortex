# ADR-0012: Container health in the Living Model (Phase 4b)

**Status:** Accepted
**Related code:** `services/api/app/services/container_health.py`,
`services/api/app/services/prometheus_health.py` (`reconcile_service_state`),
`services/api/app/graph_db.py` (`fetch_containers`),
`infra/ansible/roles/container_metrics/`, `infra/ansible-sandbox/simulate-containers.yml`
**Related ADRs:** adr-0002 (graph), adr-0003 (cross-check it extends)

## Context

On a Kolla deployment every OpenStack service is a Docker container. The twin
learned that a service was down only from OpenStack's own API report plus
host-level `up{job="node_exporter"}`. That left two blind spots:

1. Containers that are **not** an OpenStack API service (`rabbitmq`, `mariadb`,
   `keystone`, `haproxy`, `memcached`, `nova_libvirt`) were not in the graph at
   all. Losing the message bus or the database raised no alert and appeared in
   no agent answer.
2. Containers that **are** a service were noticed only after the service's
   heartbeat timed out, and a crash-looping or unhealthy container was never
   noticed.

## Decisions

1. **Telemetry via node_exporter's textfile collector**, not a new exporter.
   `container_metrics` (ansible, a systemd timer every 20s) runs one
   `docker ps -a`/`inspect` and writes `cortex_container_up`,
   `cortex_container_restart_count` and `cortex_containers_scrape_timestamp_seconds`.
   They ride the existing `node_exporter` scrape, already labelled with `node`
   by `prometheus_sd`, so there is no new port, job or credential. The
   node_exporter unit gains `--collector.textfile.directory`.
2. **Graph model:** `(:Container {id: "name@host"})-[:RUNS_ON]->(:Node)` and
   `(:Service)-[:RUNS_IN]->(:Container)` when the container is a service's
   runtime (Kolla names it after the binary: `nova_compute` ↔ `nova-compute`).
3. **Honest about missing data.** Stale telemetry (collector timestamp older
   than `CONTAINER_STALE_AFTER_SECONDS`) is `unknown` and raises nothing, never
   carried forward as `running`. The script writes *nothing* when Docker is
   unreachable, so that case goes stale rather than looking like "all
   containers removed". A previously-seen container absent from a *fresh* scrape
   is `missing` (critical), aged out after `CONTAINER_MISSING_RETENTION_DAYS`.
   A node with no series at all is `Node.health`'s problem, not dozens of
   container alerts.
4. **Service reconciliation gets a third input** (ADR-0003's own "revisit when"):
   `up` + container `down`/`restarting`/`missing` → `down`. A host that is itself
   `down` still wins (`unreachable`). `unhealthy` does not change the service
   state; it raises its own high-severity alert.
5. **One alert per incident.** A container that implements a service is covered
   by that service's reconciled state (earlier than before); only non-service
   containers get an alert of their own, plus `unhealthy` for either kind. They
   reuse the existing `service_state` alert pipeline (flags, episodes, email),
   keyed `container@host`.
6. **Agents and UI.** `GET /api/v1/topology/containers`; the monitoring agent's
   service answers list non-service containers in a problem state; the topology
   graph renders `Container` vertices and `RUNS_IN` edges.

## Known limits

- The twin reports what Docker reports. A container that is up and healthy by
  Docker's measure but functionally broken is invisible (no application probe).
- Kolla naming is assumed for the container ↔ service link; a custom naming
  scheme leaves containers unlinked (they are then alerted on directly, which
  errs on the side of more signal).

## Documentation gaps found while auditing (not addressed here)

The project doc describes the Living Model as *servers → services → networks →
VMs → security groups → past incidents*, fed by OpenStack + Prometheus + **Loki**.
Today: security groups exist only as a `security_group_ids` property on ports (no
vertices); past incidents are not graph vertices; Loki is queried directly by
agents rather than ingested into the graph. These are separate pieces of work.
