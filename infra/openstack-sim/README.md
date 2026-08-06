# openstack-sim — fake OpenStack control plane for the sandbox

Same idea as `infra/ansible-sandbox`'s `controller-sim`/`compute*-sim`
containers (fake infra nodes so Ansible has something real to SSH into
without a live cluster), but for the OpenStack side: `topology_sync.py`
(Phase 2) needs Keystone/Nova/Neutron to talk to, and there's no real
OpenStack in the sandbox. This gives it one.

It's a small FastAPI app (`app.py`) that implements just enough of the
Keystone v3 / Nova v2.1 / Neutron v2.0 REST surface for `openstacksdk`'s
list calls to work:

- Keystone: `POST /v3/auth/tokens` (issues a fake token + service catalog
  pointing back at itself for compute/network)
- Nova: `GET /os-hypervisors[/detail]`, `GET /os-services`
- Neutron: `GET /networks`, `/subnets`, `/routers`, `/floatingips`, `/agents`

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

Plus one network/subnet pair per sandbox subnet (`10.0.1.0/24`,
`10.0.2.0/24`), one router, and one floating IP. Edit the lists at the top
of `app.py` directly if you need different/more topology to test against —
there's no database backing this, it's just Python literals.

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
"
```

Expected: `['compute1-sim', 'compute2-sim']` and
`['sandbox-net', 'sandbox-storage-net']`.

## Once topology_sync.py exists (Phase 2)

This is what it's for: point `topology_sync.py`'s sync pass at the sandbox
stack and confirm it `MERGE`s the hypervisors/networks/subnets/routers/
floating IPs above into Neo4j correctly, and that a second run (with one
hypervisor removed from the seed list) correctly sweeps the stale vertex —
all without touching the real RIF SAS OpenStack cluster.
