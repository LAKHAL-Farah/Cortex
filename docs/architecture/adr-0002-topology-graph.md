# ADR-0002: Topology graph — auth, deployment, and discovery consolidation

**Status:** Accepted (Phase 0 of the topology-graph feature)
**Related code:** `services/api/app/services/topology_sync.py` (Phase 2),
`services/api/app/services/openstack_discovery.py` (superseded),
`infra/docker-compose.yml`, `infra/docker-compose.prod.yml`, `infra/.env.example`
**Related design doc:** topology graph proposal (Node → Service → Network)

## Context

The topology-graph feature needs Cortex to talk to the OpenStack control plane
(Nova/Neutron/Cinder) on a schedule. Before writing any sync code, four things
had to be decided/fixed, none of which were settled in the codebase:

1. How does the `api` container authenticate to OpenStack?
2. Can the `api` container actually reach the OpenStack API endpoints?
3. What privilege level does the credential need?
4. `openstack_discovery.py` already exists (dead code) — does it get wired up
   as-is, or superseded?

## Decisions

### 1. Auth: `clouds.yaml`, mounted read-only

`openstacksdk` supports both `OS_*` env vars and a `clouds.yaml` file. We use
**`clouds.yaml`**, mounted read-only into the `api` container at
`/etc/openstack/clouds.yaml`, following the exact bind-mount pattern already
used for the Ansible SSH key (`CORTEX_SSH_KEY_HOST_DIR` →
`/root/.ssh:ro`). Rationale:



### 2. Credential privilege: reader-only

The current `openstack_discovery.py` stub connects as `cloud="admin"`, which
is more than discovery needs. Created a dedicated `cortex-reader` account
scoped to the Keystone `reader` role, which is enough for every call
`topology_sync.py` makes (`hypervisors()`, `services()`, `agents()`,
`networks()`, `subnets()`, `routers()`, `ips()` — all list/get, no writes):

```bash
openstack user create --domain Default --password-prompt cortex-reader
openstack role add --user cortex-reader --user-domain Default \
  --project admin --project-domain Default reader
```

(Adjust `--project` to whichever project actually owns the hypervisors/networks
being discovered — `reader` is a system/project-scoped role as of the
`system-scope` work in Keystone; use whatever scope your OpenStack version
supports. Verify with `openstack role list --user cortex-reader`.)

### 3. Discovery consolidation: supersede, don't wire up

`openstack_discovery.discover_new_computes()` is **not** getting wired into
`main.py` as-is. It does one narrow thing (register new hypervisors as
`Node` rows) that is a strict subset of what `topology_sync.py` needs to do
anyway (Phase 2 also pulls hypervisors, plus everything else). Running both
on independent schedules would mean two OpenStack polling loops writing to
two different stores (Postgres `nodes` vs. the Neo4j graph) that could
disagree about what exists.

`topology_sync.py` absorbs `discover_new_computes()`'s logic (new hypervisor
→ `crud.create_node()` → `add_host_to_inventory()` →
`install_node_exporter()` → `regenerate_file_sd()`) as one step in its own
pass, rather than keeping it as a separate function/schedule.
`openstack_discovery.py` is left in place but marked deprecated until Phase 2
lands, then deleted.

## Consequences

- Phase 1/2 code can assume `OS_CLOUD` is set and `openstacksdk.connect()`
  works with no extra arguments: `openstack.connect()`.
- Only one OpenStack polling loop will exist once Phase 2 ships.


## Revisit when

- Cortex is deployed somewhere other than the controller.
- OpenStack is upgraded to a version where the `reader` role scoping works
  differently (re-verify the `openstack role add` commands above).
