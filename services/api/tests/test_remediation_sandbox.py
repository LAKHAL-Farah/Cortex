"""
services/api/tests/test_remediation_sandbox.py

Roadmap 4.4, sandbox side: the two things the sandbox needed before an
approved fix could *succeed* in it.

1. infra/openstack-sim now accepts the writes the executor's SDK actions make
   (compute service enable/disable, server reboot, agent/port admin state) --
   for the `cortex-operator` identity only, with a log of every write at
   GET /_sandbox/changes.
2. infra/ansible-sandbox's `cortex-docker-sim`: the `docker` the Ansible actions
   call on a sim node (which has no Docker daemon) -- restart flips a simulated
   container from down to healthy and rewrites the metrics textfile.

Both are loaded in-process (no socket, no docker-compose), like the other
sandbox tests. The exact request shapes were taken from what the installed
openstacksdk sends (`PUT /os-services/{id}` at the sim's advertised 2.90,
`POST /servers/{id}/action`, `PUT /agents|ports/{id}`).
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3] / "infra"
SIM_APP_PATH = ROOT / "openstack-sim" / "app.py"
SHIM = ROOT / "ansible-sandbox" / "roles" / "container_metrics_simulator" / "files" / "cortex-docker-sim"

VM = "8f3f0f4a-0000-0000-0000-000000000041"
PORT = "8f3f0f4a-0000-0000-0000-000000000051"


# --------------------------------------------------------------------
# 1. openstack-sim writes
# --------------------------------------------------------------------

@pytest.fixture(scope="module")
def sim():
    spec = importlib.util.spec_from_file_location("openstack_sim_app_writes", SIM_APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["openstack_sim_app_writes"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(sim):
    c = TestClient(sim.app)
    c.post("/_sandbox/fault/reset")
    yield c
    c.post("/_sandbox/fault/reset")


def _token(client, user: str) -> dict:
    body = {"auth": {"identity": {"methods": ["password"], "password": {"user": {"name": user, "password": "x"}}}}}
    resp = client.post("/v3/auth/tokens", json=body)
    return {"X-Auth-Token": resp.headers["X-Subject-Token"]}


def _service(client, host, binary="nova-compute"):
    return next(s for s in client.get("/v2.1/os-services").json()["services"] if s["host"] == host and s["binary"] == binary)


def test_the_operator_can_disable_and_enable_a_compute_service(client):
    op = _token(client, "cortex-operator")
    target = _service(client, "compute2-sim")

    r = client.put(f"/v2.1/os-services/{target['id']}", headers=op, json={"status": "disabled", "disabled_reason": "high CPU"})
    assert r.status_code == 200 and r.json()["service"]["status"] == "disabled"
    after = _service(client, "compute2-sim")
    assert after["status"] == "disabled" and after["disabled_reason"] == "high CPU"
    assert _service(client, "compute1-sim")["status"] == "enabled"  # only the one asked about

    r = client.put(f"/v2.1/os-services/{target['id']}", headers=op, json={"status": "enabled"})
    assert r.status_code == 200
    assert _service(client, "compute2-sim")["status"] == "enabled"
    assert _service(client, "compute2-sim")["disabled_reason"] is None


def test_the_legacy_action_routes_work_too(client):
    op = _token(client, "cortex-operator")
    body = {"host": "compute1-sim", "binary": "nova-compute", "disabled_reason": "maintenance"}
    assert client.put("/v2.1/os-services/disable-log-reason", headers=op, json=body).status_code == 200
    assert _service(client, "compute1-sim")["status"] == "disabled"
    assert client.put("/v2.1/os-services/enable", headers=op, json={"host": "compute1-sim", "binary": "nova-compute"}).status_code == 200
    assert _service(client, "compute1-sim")["status"] == "enabled"


def test_the_read_only_identity_cannot_change_anything(client):
    reader = _token(client, "cortex-reader")
    target = _service(client, "compute1-sim")
    for method, path, body in (
        ("put", f"/v2.1/os-services/{target['id']}", {"status": "disabled"}),
        ("post", f"/v2.1/servers/{VM}/action", {"reboot": {"type": "SOFT"}}),
        ("put", f"/v2.0/ports/{PORT}", {"port": {"admin_state_up": False}}),
        ("put", "/v2.0/agents/a3", {"agent": {"admin_state_up": False}}),
    ):
        r = getattr(client, method)(path, headers=reader, json=body)
        assert r.status_code == 403, path
    assert _service(client, "compute1-sim")["status"] == "enabled"
    log = client.get("/_sandbox/changes").json()
    assert [e["outcome"] for e in log] == ["forbidden"] * 4 and {e["identity"] for e in log} == {"cortex-reader"}


def test_an_unknown_token_gets_401_so_the_sdk_reauthenticates(client):
    r = client.put("/v2.1/os-services/1", headers={"X-Auth-Token": "sim-token-from-before-a-restart"}, json={"status": "disabled"})
    assert r.status_code == 401
    assert client.put("/v2.1/os-services/1", json={"status": "disabled"}).status_code == 401  # no token at all


def test_reading_still_needs_no_particular_identity(client):
    assert client.get("/v2.1/os-services").status_code == 200
    assert client.get("/v2.0/agents").status_code == 200


def test_reboot_is_accepted_and_logged(client):
    op = _token(client, "cortex-operator")
    assert client.post(f"/v2.1/servers/{VM}/action", headers=op, json={"reboot": {"type": "HARD"}}).status_code == 202
    entry = client.get("/_sandbox/changes").json()[-1]
    assert entry["identity"] == "cortex-operator" and entry["change"] == {"reboot": "HARD"} and entry["outcome"] == "applied"


def test_other_server_actions_and_unknown_ids_are_not_pretended(client):
    op = _token(client, "cortex-operator")
    assert client.post(f"/v2.1/servers/{VM}/action", headers=op, json={"os-stop": None}).status_code == 400
    assert client.post(f"/v2.1/servers/{VM}/action", headers=op, json={"reboot": {"type": "WARM"}}).status_code == 400
    assert client.post("/v2.1/servers/nope/action", headers=op, json={"reboot": {"type": "SOFT"}}).status_code == 404
    assert client.put("/v2.1/os-services/999", headers=op, json={"status": "disabled"}).status_code == 404


def test_enabling_a_port_clears_the_injected_down_fault(client):
    op = _token(client, "cortex-operator")
    client.post(f"/_sandbox/fault/port/{PORT}", json={"status": "DOWN"})
    assert next(p for p in client.get("/v2.0/ports").json()["ports"] if p["id"] == PORT)["status"] == "DOWN"

    r = client.put(f"/v2.0/ports/{PORT}", headers=op, json={"port": {"admin_state_up": True}})

    assert r.status_code == 200 and r.json()["port"]["admin_state_up"] is True
    assert next(p for p in client.get("/v2.0/ports").json()["ports"] if p["id"] == PORT)["status"] == "ACTIVE"


def test_agent_admin_state_can_be_changed(client):
    op = _token(client, "cortex-operator")
    assert client.put("/v2.0/agents/a3", headers=op, json={"agent": {"admin_state_up": False}}).status_code == 200
    assert next(a for a in client.get("/v2.0/agents").json()["agents"] if a["id"] == "a3")["admin_state_up"] is False


def test_reset_restores_the_seed_data_and_clears_the_log(client):
    op = _token(client, "cortex-operator")
    client.put(f"/v2.1/os-services/{_service(client, 'compute1-sim')['id']}", headers=op, json={"status": "disabled"})
    client.put("/v2.0/agents/a3", headers=op, json={"agent": {"admin_state_up": False}})
    assert client.get("/_sandbox/changes").json()

    client.post("/_sandbox/fault/reset")

    assert _service(client, "compute1-sim")["status"] == "enabled"
    assert next(a for a in client.get("/v2.0/agents").json()["agents"] if a["id"] == "a3")["admin_state_up"] is True
    assert client.get("/_sandbox/changes").json() == []


def test_the_executors_own_sdk_calls_match_this_surface(client, monkeypatch):
    """Not just similar paths: drive the *real* executor against this app by
    pointing its connection at a TestClient-backed fake that forwards exactly
    the request shapes openstacksdk produced when this was written."""
    from types import SimpleNamespace

    from app.services import remediation_executor as ex

    op = _token(client, "cortex-operator")
    services = client.get("/v2.1/os-services").json()["services"]

    class Compute:
        def services(self, **_filters):  # the sim ignores filters, like the executor must not assume
            return [SimpleNamespace(**s) for s in services]

        def disable_service(self, service, host=None, binary=None, disabled_reason=None):
            body = {"status": "disabled", **({"disabled_reason": disabled_reason} if disabled_reason else {})}
            assert client.put(f"/v2.1/os-services/{service.id}", headers=op, json=body).status_code == 200

        def reboot_server(self, server, reboot_type):
            assert client.post(f"/v2.1/servers/{server}/action", headers=op, json={"reboot": {"type": reboot_type}}).status_code == 202

    monkeypatch.setattr(ex, "_connect", lambda: SimpleNamespace(compute=Compute()))

    assert ex.run_plan(ex.plan_for("openstack compute service set --disable compute2-sim nova-compute", None)).ok
    assert ex.run_plan(ex.plan_for(f"openstack server reboot {VM}", None)).ok
    assert _service(client, "compute2-sim")["status"] == "disabled" and _service(client, "compute1-sim")["status"] == "enabled"


# --------------------------------------------------------------------
# 2. the docker shim
# --------------------------------------------------------------------

@pytest.fixture
def node(tmp_path):
    """A fake sim node: config/state/output under tmp_path, the shim run as a
    subprocess exactly as Ansible runs it."""
    env = {
        **os.environ,
        "CORTEX_SIM_CONFIG": str(tmp_path / "containers.json"),
        "CORTEX_SIM_STATE": str(tmp_path / "state.json"),
        "CORTEX_SIM_OUTPUT": str(tmp_path / "cortex_containers.prom"),
    }

    class Node:
        out = tmp_path / "cortex_containers.prom"

        @staticmethod
        def configure(containers, **scenario):
            (tmp_path / "containers.json").write_text(json.dumps({"containers": containers, **scenario}))

        @staticmethod
        def run(*args, as_name="cortex-docker-sim"):
            exe = SHIM
            if as_name == "docker":
                exe = tmp_path / "docker"
                if not exe.exists():
                    exe.symlink_to(SHIM)
            return subprocess.run([sys.executable, str(exe), *args], env=env, capture_output=True, text=True)

        @staticmethod
        def metric(name):
            return [ln for ln in Node.out.read_text().splitlines() if f'container="{name}"' in ln]

    return Node


CONTAINERS = ["nova_compute", "nova_libvirt", "neutron_openvswitch_agent", "neutron_l3_agent"]


def test_a_down_container_is_rendered_exactly_as_the_old_template_did(node):
    node.configure(CONTAINERS, down=["nova_compute"], unhealthy=["nova_libvirt"], removed=["neutron_l3_agent"])
    assert node.run("seed").returncode == 0
    text = node.out.read_text().splitlines()
    assert 'cortex_container_up{container="nova_compute",state="exited",health="none"} 0' in text
    assert 'cortex_container_up{container="nova_libvirt",state="running",health="unhealthy"} 1' in text
    assert 'cortex_container_up{container="neutron_openvswitch_agent",state="running",health="healthy"} 1' in text
    assert 'cortex_container_restart_count{container="nova_compute"} 3' in text
    assert node.metric("neutron_l3_agent") == []  # `docker rm`: absent entirely
    assert text[0].startswith("# HELP cortex_container_up") and text[-1].startswith("cortex_containers_scrape_timestamp_seconds ")
    assert "" not in text  # no blank lines (the template module used trim_blocks)


def test_restart_brings_a_down_container_back_and_counts_it(node):
    node.configure(CONTAINERS, down=["nova_compute"])
    node.run("seed")

    r = node.run("restart", "nova_compute", as_name="docker")

    assert r.returncode == 0 and r.stdout.strip() == "nova_compute"
    assert node.metric("nova_compute") == [
        'cortex_container_up{container="nova_compute",state="running",health="healthy"} 1',
        'cortex_container_restart_count{container="nova_compute"} 4',
    ]


def test_restart_also_fixes_an_unhealthy_container(node):
    node.configure(CONTAINERS, unhealthy=["nova_libvirt"])
    node.run("seed")
    assert node.run("restart", "nova_libvirt", as_name="docker").returncode == 0
    assert 'health="healthy"} 1' in node.metric("nova_libvirt")[0]


def test_stop_then_start(node):
    node.configure(CONTAINERS)
    node.run("seed")
    assert node.run("stop", "nova_compute", as_name="docker").returncode == 0
    assert 'state="exited",health="none"} 0' in node.metric("nova_compute")[0]
    assert node.run("start", "nova_compute", as_name="docker").returncode == 0
    assert 'state="running",health="healthy"} 1' in node.metric("nova_compute")[0]


def test_restarting_a_container_that_is_not_there_fails_like_docker(node):
    node.configure(CONTAINERS, removed=["neutron_l3_agent"])
    node.run("seed")
    for name in ("neutron_l3_agent", "never_existed"):
        r = node.run("restart", name, as_name="docker")
        assert r.returncode == 1 and f"No such container: {name}" in r.stderr
    assert node.metric("neutron_l3_agent") == []  # a failed restart must not invent it


def test_several_names_act_on_the_ones_that_exist_and_fail_overall(node):
    node.configure(CONTAINERS, down=["nova_compute"])
    node.run("seed")
    r = node.run("restart", "nova_compute", "ghost", as_name="docker")
    assert r.returncode == 1 and "ghost" in r.stderr
    assert 'state="running"' in node.metric("nova_compute")[0]


def test_it_does_not_pretend_to_be_all_of_docker(node):
    node.configure(CONTAINERS)
    node.run("seed")
    for args in (("ps",), ("rm", "nova_compute"), ("system", "prune", "-a"), ("restart",), ("restart", "--time=0", "nova_compute")):
        r = node.run(*args, as_name="docker")
        assert r.returncode == 1, args
    assert 'state="running"' in node.metric("nova_compute")[0]  # nothing changed


def test_reseeding_resets_a_scenario_like_rerunning_the_playbook(node):
    node.configure(CONTAINERS, down=["nova_compute"])
    node.run("seed")
    node.run("restart", "nova_compute", as_name="docker")
    node.configure(CONTAINERS, down=["nova_compute"])  # the playbook re-run with the same -e
    node.run("seed")
    assert 'state="exited"' in node.metric("nova_compute")[0]


def test_the_output_is_replaced_atomically_and_leaves_no_prom_litter(node):
    node.configure(CONTAINERS)
    node.run("seed")
    node.run("restart", "nova_compute", as_name="docker")
    leftovers = [p.name for p in node.out.parent.iterdir() if p.suffix == ".prom" and p != node.out]
    assert leftovers == []  # a half-written second .prom file would be scraped by node_exporter


def test_the_role_installs_the_shim_as_docker_and_no_longer_uses_the_template():
    role = ROOT / "ansible-sandbox" / "roles" / "container_metrics_simulator"
    tasks = (role / "tasks" / "main.yml").read_text()
    assert "dest: /usr/local/bin/docker" in tasks and "state: link" in tasks
    assert "cortex-docker-sim seed" in tasks
    assert not (role / "templates").exists()  # one writer of the textfile, not two that could drift
