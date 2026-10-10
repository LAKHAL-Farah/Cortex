# Testing real remediation in the sandbox (roadmap 4.4)

Approved fixes run only after an explicit **Execute** click (adr-0015). In the
sandbox they now *succeed*, and you can watch the effect:

| Fix Cortex proposes | Backend | What happens in the sandbox |
|---|---|---|
| `docker restart nova_compute` (also `rabbitmq`, `nova_libvirt`, ...) | Ansible | The sim node has no Docker, so `cortex-docker-sim` stands in: the simulated container goes **down → running/healthy**, restart count +1, metrics rewritten |
| `systemctl restart node_exporter` | Ansible | Real: the sim nodes are systemd containers |
| `openstack compute service set --disable compute1-sim nova-compute` (and `--enable`) | OpenStack SDK | `openstack-sim` marks that service disabled; `GET /v2.1/os-services` shows it |
| `openstack server reboot <id>` / `port set --enable <id>` | OpenStack SDK | Accepted and logged (ids must be filled in, so these only run when the proposal has no blank) |

Deletes, rebuilds, prunes and compound commands are never run, here or anywhere.

## What changed in the sandbox

- `openstack-sim` accepts the writes above, **only for the `cortex-operator`
  identity** (the `cortex-reader` profile the collectors use gets a 403, as a real
  cloud's policy would). Every write, accepted or refused, is listed at
  `GET /_sandbox/changes`; `POST /_sandbox/fault/reset` restores the seed data and
  clears the log.
- The sandbox `clouds.yaml` has a `cortex-operator` profile (nothing real to protect).
- The sim nodes get a `docker` shim (`roles/container_metrics_simulator`) that
  supports `restart | stop | start` on the simulated containers and nothing else.
- Execution is on by default here (`CORTEX_ENV=sandbox` is not production).

## Setup

```bash
cd infra
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up -d --build   # --build: openstack-sim changed
docker compose exec api alembic upgrade head
```

Your `.env` must already point `CORTEX_ANSIBLE_HOST_DIR` at `infra/ansible-sandbox`
(that is how `site.yml` reaches the sim nodes today). Check:

```bash
docker compose exec api ls /infra/ansible/playbook-remediate.yml      # must exist
docker compose exec api bash -c 'cd /infra/ansible && ansible all -m ping'   # nodes reachable
```

Install the shim and baseline metrics on the sim nodes (re-run any time):

```bash
docker compose exec api bash -c 'cd /infra/ansible && ansible-playbook simulate-containers.yml'
```

## Scenario A -- a container is down, restart it (Ansible)

1. Take it down:
   ```bash
   docker compose exec api bash -c "cd /infra/ansible && ansible-playbook simulate-containers.yml -e '{\"sim_down_containers\": [\"nova_compute\"]}'"
   ```
2. In the Copilot chat ask: **`How do I fix nova-compute being down on compute1-sim`**
3. On the proposal card: *Ask for more info* (try `how do I undo it and what will it affect?`), then
   **Approve**, then **Execute… → Yes, execute now**.
4. The card goes *Executing…* → **Executed**. Check the effect:
   ```bash
   docker compose exec compute1-sim cat /var/lib/node_exporter/textfile/cortex_containers.prom | grep nova_compute
   # before: state="exited" ... 0 / restart_count 3      after: state="running",health="healthy" ... 1 / restart_count 4
   ```
   Prometheus/the Living Model pick it up on the next scrape (the file's timestamp is refreshed by the restart).

## Scenario B -- disable a compute service (OpenStack SDK)

1. Ask: **`Propose a fix for the high CPU on compute1-sim`**
2. Approve, Execute.
3. See what the "cloud" was asked, and the result:
   ```bash
   docker compose exec api curl -s http://openstack-sim:5000/_sandbox/changes | python3 -m json.tool
   docker compose exec api curl -s http://openstack-sim:5000/v2.1/os-services | python3 -m json.tool | grep -B3 -A3 disabled
   ```
4. Put it back with `curl -X POST http://openstack-sim:5000/_sandbox/fault/reset` (from `docker compose exec api`).

## Scenario C -- the guard rails (each should be *refused*)

- **Execute before approving:** no button; via API, `409 ... not been approved yet`.
- **Viewer:** log in as a viewer: the card says only an admin can execute.
- **Reader credentials can't write:** in `docker compose exec api`, run
  `OS_CLOUD=cortex-reader python3 -c "import openstack; c=openstack.connect(); c.compute.disable_service(next(c.compute.services()))"`
  -- expect a `403`, and a `forbidden` row in `/_sandbox/changes`.
- **Kill switch:** set `CORTEX_REMEDIATION_EXECUTION=off` on `api`, restart it: Execute returns `503`
  and the card says why. Approvals still work.
- **Failure path:** repeat Scenario A, but before clicking Execute make the container vanish
  (what `docker rm` looks like):
  `ansible-playbook simulate-containers.yml -e '{"sim_removed_containers": ["nova_compute"]}'`.
  The restart then fails with `No such container`: the card ends *Execution failed*,
  *Execute again…* appears, and the history shows the Ansible output.

## What was verified, and what was not

Verified (automated, `tests/test_remediation_sandbox.py`, plus manual runs): the sim's
write endpoints with the real `openstacksdk` and the real executor; the reader being
refused; real `ansible-playbook` running `playbook-remediate.yml`, including its
single-host guard and the shim flipping a down container to healthy; the shim's output
matching the old template byte for byte.

**Not run here:** the full docker-compose stack and the chat UI (no Docker, no LLM key
in the environment this was built in), so (1) whether the Copilot's diagnosis picks
`compute1-sim` for the prompts above, and (2) SSH from the `api` container to the
sim nodes, rely on your existing sandbox working. If a prompt produces no proposal
card, `docs/architecture/adr-0015-remediation-execution.md` and `tests/test_remediation.py`
list the phrasings that trigger a fix. A proposal can also be created directly:

```bash
docker compose exec api python - <<'PY'
import copy, uuid
from app.agents.nodes import openstack_expert as ex
from app.agents.nodes.openstack_expert_catalog import CATALOG
from app.agents.nodes.remediation import build_fix_proposal
from app.db import SessionLocal
from app.services import remediation_approval as ap
entry = next(e for e in CATALOG if e["id"] == "nova-compute-down")
raw = ex._build_result(entry, "compute1-sim: nova-compute is down.", "compute1-sim",
                       extra_raw={"diagnosed_by": "monitoring"})["raw_data"]
p = copy.deepcopy(build_fix_proposal(raw)); p["proposal_id"] = f"fix-{uuid.uuid4().hex[:12]}"
print("PROPOSAL_ID", ap.record_proposal(SessionLocal(), trace_id=None, proposal=p, requested_by=None).id)
PY
```

then approve and execute it with `POST /api/v1/remediation/proposals/{id}/decision` and `.../execute`.
(That seeding code is the same call the agent route makes; it is what the tests use.)
