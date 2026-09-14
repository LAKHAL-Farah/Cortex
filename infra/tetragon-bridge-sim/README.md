# tetragon-bridge-sim — fake Falco/Tetragon HTTP bridge for the sandbox

Phase Sec-4 of the security-track roadmap. `services/api/app/services/ebpf_signal.py`
already has a real HTTP client for the Security Agent's highest-confidence
sub-check (`_EBPF_SIGNAL_CONFIDENCE = 0.95`), but a fresh checkout has
nothing listening at `CORTEX_EBPF_ALERTS_URL` -- every call fails and
`_check_ebpf_signal` degrades to "unknown" instead of "clean" or "risky".
This gives it something real to poll, the same role `openstack-sim` plays
for the OpenStack SDK calls and `controller-sim`'s Loki plays for
`loki_client.py`.

**Scope decision (per the roadmap doc's open question in §13): compute
nodes only for now.** The seed alert below lives on `compute1-sim`, and
the sensor-simulator Ansible role (`infra/ansible-sandbox/roles/
ebpf_sensor_simulator`) is only deployed to the `[computes]` inventory
group -- matching the project's own stated priority that VM attack
surface, and therefore eBPF sensor coverage, starts on compute hosts.
`controller-sim`/`storage-sim` have no sensor and will always read back
an empty alert list, which is the honest state for a host nothing is
watching yet, not a bug.

## What this is (and isn't)

A ~150-line FastAPI app that implements exactly the one real HTTP surface
`ebpf_signal.py` needs:

- `GET /alerts?host=<hostname>` -- the shape Falco's own JSON HTTP output
  plugin uses natively: `[{"rule", "priority", "output", "time"}, ...]`
  (plus `host` on each entry, needed for the fleet-wide
  `list_hosts_with_alerts()` read).
- `GET /alerts` (no `host`) -- every currently-active alert, fleet-wide.
- `GET /healthz`

**Not a real Falco or Tetragon deployment, and not eBPF.** It doesn't
touch the kernel, doesn't run inside `compute1-sim`/`compute2-sim`, and
doesn't observe anything actually happening on those containers. It's an
in-memory alert store a test or an Ansible-deployed script can push
synthetic alerts into, standing in for wherever a real sensor's bridge
would post to. Swapping this out for a real Falco HTTP output plugin or a
Tetragon gRPC-to-HTTP bridge later is a pure infra change --
`CORTEX_EBPF_ALERTS_URL` is the only thing that needs to move, no code
changes anywhere in `ebpf_signal.py` or `agents/nodes/security.py`.

## Seed data

One alert, on `compute1-sim`, present from container start (so
`_check_ebpf_signal` always has a genuine `has_signal: True` finding to
demonstrate against a fresh sandbox, the same way `openstack-sim`'s
`SECURITY_GROUP_RULES` seeds one deliberately world-open rule):

```json
{
  "host": "compute1-sim",
  "rule": "Terminal shell in container",
  "priority": "warning",
  "output": "A shell was spawned with a parent process that is not a shell itself (user=root shell=bash parent=qemu-system-x86_64 cmdline=bash host=compute1-sim)",
  "time": "<container start time, UTC>"
}
```

`compute2-sim`, `controller-sim`, and `storage-sim` start clean (empty
alert list) -- restart the container to reset back to this single-alert
state.

## Sandbox-only fault injection

Same convention `openstack-sim`'s own `/_sandbox/...` endpoints use --
gated on `CORTEX_ENV=sandbox`, never part of any real Falco/Tetragon
surface, only ever called by test scripts or Ansible, never by the Cortex
API itself:

- `POST /_sandbox/ebpf/trigger` -- body `{"host": str, "rule"?, "priority"?, "output"?}`.
  `host` is the only required field; everything else defaults to a
  realistic "unexpected shell spawned" finding. Returns the new alert's
  `id` so it can be removed individually later.
- `DELETE /_sandbox/ebpf/alert/{id}` -- removes exactly one previously-
  triggered alert.
- `POST /_sandbox/fault/reset` -- clears every triggered alert and
  restores the single default seed alert on `compute1-sim`. Same path
  `openstack-sim` uses for its own full-reset endpoint.

`infra/ansible-sandbox/roles/ebpf_sensor_simulator`'s on-demand trigger
script (`/usr/local/bin/trigger_ebpf_event.sh`, deployed to
`compute1-sim`/`compute2-sim`) is the intended way to exercise this end
to end for Phase Sec-4's own acceptance criterion -- it actually spawns a
detached shell process on the target compute-sim container (a real,
if harmless, "unexpected shell spawned inside a test VM" event) and then
calls `POST /_sandbox/ebpf/trigger` to report it, so a manual test sees
both the real process and the alert it produced, not just a fabricated
API response. See that role's own README/task comments for exact usage.

## Running it as part of the sandbox stack

Already wired into `docker-compose.sandbox.yml` -- see that file's
`tetragon-bridge-sim` service and the `api` service's
`CORTEX_EBPF_ALERTS_URL` override. Bring it up the same way as the rest
of the sandbox:

```bash
cd infra
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up -d --build
```

## Verifying it directly

```bash
# Seed alert, right after startup
curl -s "http://127.0.0.1:9110/alerts?host=compute1-sim" | python3 -m json.tool
# -> one entry, rule "Terminal shell in container"

# Nothing yet on compute2-sim
curl -s "http://127.0.0.1:9110/alerts?host=compute2-sim" | python3 -m json.tool
# -> []

# Trigger one on compute2-sim
curl -s -X POST http://127.0.0.1:9110/_sandbox/ebpf/trigger \
  -H 'Content-Type: application/json' \
  -d '{"host": "compute2-sim"}' | python3 -m json.tool

curl -s "http://127.0.0.1:9110/alerts?host=compute2-sim" | python3 -m json.tool
# -> one entry now, priority "critical"
```

Note this port is only published to the host (`127.0.0.1:9110`) for local
curl testing -- the `api` container reaches it over the internal
`cortex-sandbox` Docker network via its service name, same as
`openstack-sim`.

## Verifying it end to end through the Security Agent

With the sandbox stack up:

```bash
docker compose exec api python3 -c "
from app.services import ebpf_signal
print(ebpf_signal.get_node_ebpf_alerts('compute1-sim'))
print(ebpf_signal.get_node_ebpf_alerts('compute2-sim'))
print(ebpf_signal.list_hosts_with_alerts())
"
```

Expect one alert for `compute1-sim`, an empty list for `compute2-sim`
(until you trigger one), and `list_hosts_with_alerts()` returning
`['compute1-sim']` on a fresh sandbox. Then ask the Security Agent about
`compute1-sim` (via chat, or `GET /api/v1/security/findings/compute1-sim`)
and confirm `raw_data.ebpf_signal.has_signal` is `true` and `confidence`
reflects `_EBPF_SIGNAL_CONFIDENCE` (0.95) rather than the lower
Loki/Neutron-only ceiling a host with no eBPF signal gets.

## Full acceptance test (Phase Sec-4)

Run the Ansible playbook against the live containers, which spawns a real
detached shell process on `compute1-sim` and reports it -- the exact
"deliberately-triggered test event (e.g., an unexpected shell spawned
inside a test VM)" the phase's own acceptance criterion asks for:

```bash
cd infra/ansible-sandbox
ansible compute1-sim -b -m command -a "/usr/local/bin/trigger_ebpf_event.sh"
```

Then re-run the verification above (or just wait for the next
`SECURITY_SCAN_INTERVAL_SECONDS` tick and reload `/security`) and confirm
a second, `critical`-priority alert now shows up for `compute1-sim`
instead of a connection-error degrade.
