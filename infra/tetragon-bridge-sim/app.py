"""Phase Sec-4: fake Falco-HTTP-output / Tetragon-bridge endpoint for the
sandbox. Not a real eBPF sensor -- this is what `CORTEX_EBPF_ALERTS_URL`
points at when nothing real is deployed, so `services/ebpf_signal.py`
(and, through it, agents/nodes/security.py's `_check_ebpf_signal`) has
something real to poll over HTTP instead of failing the breaker on every
call the way a fresh checkout does today.

Why this is its own container rather than a route bolted onto
openstack-sim (the "less infra" choice Phase Sec-3's package-inventory
collector made): a kernel-level sensor is not an OpenStack API in any
deployment, real or simulated -- Falco/Tetragon run on the node's own
kernel and are a completely separate data plane from Keystone/Nova/
Neutron. Reusing openstack-sim here would blur a distinction the
companion assessment doc (§1) spends a whole section establishing. It's
also architecturally the more honest shape for the real per-node-sensor
-> central-bridge -> single-HTTP-endpoint topology `ebpf_signal.py`
already assumes (one `CORTEX_EBPF_ALERTS_URL`, `host` as a query param,
not one URL per node).

Expected response shape from `GET /alerts?host=<hostname>` --
the exact shape ebpf_signal.get_node_ebpf_alerts already expects and
Falco's own JSON output plugin already produces natively:
    [{"rule": str, "priority": "critical"|"warning"|"notice"|"info",
      "output": str, "time": ISO-8601 str, "host": str}, ...]
(`host` is additionally included on every entry -- ebpf_signal.py's
per-node read ignores it, but list_hosts_with_alerts()'s fleet-wide read
depends on it being present, the same way it would be present on a real
Falco/Tetragon alert.)

Sandbox-only fault injection, same shape and same reasoning as
openstack-sim's own "sandbox-only fault injection" section (see that
module's app.py): `/_sandbox/ebpf/...` isn't part of any real Falco/
Tetragon HTTP surface, only ever call these against the sandbox, and
only from test scripts/ansible (see
infra/ansible-sandbox/roles/ebpf_sensor_simulator), never from the
Cortex API itself.
"""
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

CORTEX_ENV = os.environ.get("CORTEX_ENV", "development").strip().lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_alert() -> dict:
    # One alert seeded by default on compute1-sim (Phase Sec-4's own
    # recommendation: compute-only pilot first, since that's where VM
    # attack surface actually lives -- see §5.2 of the roadmap doc) so
    # `_check_ebpf_signal` always has a genuine `has_signal: True` finding
    # to demonstrate against a fresh sandbox, mirroring how
    # SECURITY_GROUP_RULES seeds one deliberately world-open rule and
    # PACKAGE_INVENTORY seeds one deliberately-vulnerable package version.
    return {
        "id": "seed-alert-compute1-sim",
        "host": "compute1-sim",
        "rule": "Terminal shell in container",
        "priority": "warning",
        "output": (
            "A shell was spawned with a parent process that is not a "
            "shell itself (user=root shell=bash parent=qemu-system-x86_64 "
            "cmdline=bash host=compute1-sim)"
        ),
        "time": _now(),
    }


# In-memory alert store -- `id` -> alert dict. Mutated only through the
# `/_sandbox/ebpf/...` endpoints below (or by restarting this container,
# which resets to the single seed alert).
_ALERTS: dict[str, dict] = {}
_ALERTS[_seed_alert()["id"]] = _seed_alert()


def _serialize(alert: dict) -> dict:
    # Callers only ever need rule/priority/output/time/host -- id is this
    # store's own bookkeeping, not part of the shape ebpf_signal.py or a
    # real Falco/Tetragon feed would ever return.
    return {k: v for k, v in alert.items() if k != "id"}


@app.get("/alerts")
def get_alerts(host: str | None = None):
    """`GET /alerts` (fleet-wide, backs `list_hosts_with_alerts`) or
    `GET /alerts?host=<hostname>` (per-node, backs
    `get_node_ebpf_alerts`) -- both read straight off the in-memory
    store, most-recent-first is not required here since ebpf_signal.py
    itself re-sorts by priority.
    """
    alerts = list(_ALERTS.values())
    if host is not None:
        alerts = [a for a in alerts if a.get("host") == host]
    return [_serialize(a) for a in alerts]


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ---- sandbox-only fault injection ----------------------------------------
# Everything above this line mirrors the real Falco HTTP-output-plugin
# surface (GET /alerts[?host=]) against in-memory seed data. There's no
# way to exercise `_check_ebpf_signal`'s actual has_signal/degraded split
# on demand -- deterministically, without waiting on a real kernel event
# -- without a way to add/remove an alert directly. These two endpoints
# do exactly that.


def _require_sandbox() -> JSONResponse | None:
    if CORTEX_ENV != "sandbox":
        return JSONResponse(
            status_code=404,
            content={
                "error": (
                    "eBPF alert simulation is only available when "
                    "CORTEX_ENV=sandbox (see infra/docker-compose.sandbox.yml)."
                )
            },
        )
    return None


@app.post("/_sandbox/ebpf/trigger")
def trigger_ebpf_alert(body: dict):
    """body: {"host": str, "rule": str?, "priority": str?, "output": str?}
    -- `host` is required, everything else defaults to a realistic
    "unexpected shell spawned" Falco-style finding so a caller (a test, or
    infra/ansible-sandbox/roles/ebpf_sensor_simulator's on-demand trigger
    script) doesn't have to construct a full alert just to demonstrate the
    has_signal path on a host that doesn't have the seed alert. Returns
    the alert's own `id` so a caller can remove exactly this one later via
    `/_sandbox/ebpf/alert/{id}` rather than resetting the whole store.
    """
    guard = _require_sandbox()
    if guard is not None:
        return guard

    host = body.get("host")
    if not host:
        return JSONResponse(status_code=400, content={"error": "body.host is required"})

    alert_id = body.get("id") or f"trigger-{host}-{uuid.uuid4().hex[:8]}"
    alert = {
        "id": alert_id,
        "host": host,
        "rule": body.get("rule", "Terminal shell in container"),
        "priority": body.get("priority", "critical"),
        "output": body.get(
            "output",
            f"An unexpected shell was spawned inside a workload on {host} "
            "(user=root shell=sh parent=unexpected)",
        ),
        "time": _now(),
    }
    _ALERTS[alert_id] = alert
    return {"alert": _serialize(alert), "id": alert_id}


@app.delete("/_sandbox/ebpf/alert/{alert_id}")
def remove_ebpf_alert(alert_id: str):
    """Removes one previously-triggered alert by id -- the other half of
    the on-demand demo, so a test/demo can clean up exactly the alert it
    added without disturbing the default seed alert or any other host's
    alerts (mirrors openstack-sim's own
    `/_sandbox/security-group-rule/remove`).
    """
    guard = _require_sandbox()
    if guard is not None:
        return guard
    removed = _ALERTS.pop(alert_id, None)
    return {"id": alert_id, "removed": removed is not None}


@app.post("/_sandbox/fault/reset")
def fault_reset():
    """Clears every triggered alert and restores the single default seed
    alert on compute1-sim -- same "undo everything back to healthy seed
    state" contract as openstack-sim's own `/_sandbox/fault/reset`, kept
    under the identical path so a test/demo script that resets the whole
    sandbox in one pass (both sims) doesn't need two different endpoint
    shapes to remember.
    """
    guard = _require_sandbox()
    if guard is not None:
        return guard
    _ALERTS.clear()
    seed = _seed_alert()
    _ALERTS[seed["id"]] = seed
    return {"status": "reset"}
