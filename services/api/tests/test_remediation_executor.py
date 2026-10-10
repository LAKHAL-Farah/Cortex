"""
services/api/tests/test_remediation_executor.py

Roadmap 4.4: what `services/remediation_executor.py` will and will not run, and
how it runs it. No database, no OpenStack, no Ansible -- the SDK connection and
`subprocess.run` are faked, so these tests can never touch real infrastructure.

1. plan_for: the allow-list. Every command the reviewed catalog can produce is
   either planned as a typed action or refused with a reason a person can read.
2. The refusals that matter for safety: shell metacharacters, placeholders,
   deletes, protected units, option injection.
3. The SDK backend calls the right method with the right arguments.
4. The Ansible backend passes values as an extra-vars file (never on the
   command line), limits to one host, and does not read "exit 0, nothing ran"
   as success.
5. The kill switch (`execution_mode`).
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

from app.agents.nodes import openstack_expert as expert
from app.agents.nodes.openstack_expert_catalog import CATALOG
from app.agents.nodes.remediation import build_fix_proposal
from app.services import remediation_executor as ex
from app.services.remediation_executor import ExecutionPlan, NotExecutable, plan_for

UUID = "3f2b8c1e-5d4a-4b7e-9a10-0c6d2e8f1a77"


# --------------------------------------------------------------------
# 1. The allow-list
# --------------------------------------------------------------------

@pytest.mark.parametrize(
    "command, host, backend, operation, targets",
    [
        ("openstack compute service set --disable compute-02 nova-compute", None, "openstack_sdk", "compute_service.disable", ("nova-compute",)),
        ('openstack compute service set --disable --disable-reason "investigating CPU pressure" compute-02 nova-compute',
         None, "openstack_sdk", "compute_service.disable", ("nova-compute",)),
        ("openstack compute service set --enable compute-02 nova-compute", None, "openstack_sdk", "compute_service.enable", ("nova-compute",)),
        (f"openstack server reboot {UUID}", None, "openstack_sdk", "server.reboot", (UUID,)),
        (f"openstack server reboot --hard {UUID}", None, "openstack_sdk", "server.reboot", (UUID,)),
        (f"openstack network agent set --enable {UUID}", None, "openstack_sdk", "network_agent.enable", (UUID,)),
        (f"openstack port set --enable {UUID}", None, "openstack_sdk", "port.enable", (UUID,)),
        ("docker restart nova_compute", "compute-02", "ansible", "container.restart", ("nova_compute",)),
        ("docker restart openvswitch_vswitchd openvswitch_db", "compute-02", "ansible", "container.restart",
         ("openvswitch_vswitchd", "openvswitch_db")),
        ("docker stop rabbitmq", "controller", "ansible", "container.stop", ("rabbitmq",)),
        ("sudo docker start rabbitmq", "controller", "ansible", "container.start", ("rabbitmq",)),
        ("systemctl restart node_exporter", "compute-02", "ansible", "unit.restart", ("node_exporter",)),
    ],
)
def test_allow_listed_commands_become_typed_plans(command, host, backend, operation, targets):
    plan = plan_for(command, host)
    assert (plan.backend, plan.operation, plan.targets) == (backend, operation, targets)
    assert plan.summary


def test_compute_service_plan_names_the_host_from_the_command_not_the_proposal():
    plan = plan_for("openstack compute service set --disable compute-02 nova-compute", "someone-else")
    assert plan.host == "compute-02"


def test_disable_reason_is_carried_as_a_parameter():
    plan = plan_for('openstack compute service set --disable --disable-reason "host unreachable, investigating" compute-02 nova-compute', None)
    assert plan.params["reason"] == "host unreachable, investigating"


# --------------------------------------------------------------------
# 2. Refusals
# --------------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "docker restart <service_container>",                        # unresolved placeholder
        "openstack server reboot <instance_id>",
        "openstack server delete " + UUID,                           # destructive
        f"openstack server rebuild {UUID} cirros",
        f"openstack server reset-state --active {UUID}",
        f"openstack server migrate --live compute-03 {UUID}",
        f"openstack volume delete {UUID}",
        f"openstack image delete {UUID}",
        "docker system prune -a --volumes",
        "journalctl --vacuum-size=500M",
        "reboot",
        "kill -TERM 4242",
        "rm -rf /var/lib/nova",
        # compound / injection
        "docker restart nova_compute && rm -rf /",
        "docker restart nova_compute; reboot",
        "docker restart nova_compute | tee /etc/passwd",
        "docker restart $(docker ps -q)",
        "docker restart `id`",
        "docker restart nova_compute\nreboot",
        f"openstack server show {UUID}  (then, if the guest itself is the runaway process) openstack server reboot {UUID}",
        "docker restart nova_compute  (note)",
        # option / name injection
        "docker restart --time=0 nova_compute",
        "docker restart -f nova_compute",
        "docker restart ../etc",
        "docker restart a b c d e",                                  # too many
        "docker exec rabbitmq rabbitmqctl stop",
        # protected units
        "systemctl restart sshd",
        "systemctl restart docker.service",
        "systemctl restart networking",
        "systemctl restart node_exporter nova-compute",              # two units
        "systemctl daemon-reload",
        # SDK shapes it does not accept
        "openstack compute service set --disable compute-02 nova-api",
        "openstack compute service set --disable --enable compute-02 nova-compute",
        "openstack compute service set --disable compute-02 nova-compute --force",
        "openstack compute service set --disable-reason=x --disable compute-02 nova-compute",
        f"openstack server reboot --hard --soft {UUID}",
        "openstack server reboot not-a-uuid",
        f"openstack port set --disable {UUID}",
        "",
        "   ",
    ],
)
def test_everything_else_is_refused_with_a_reason(command):
    with pytest.raises(NotExecutable) as err:
        plan_for(command, "compute-02")
    assert err.value.reason and len(err.value.reason) > 10


def test_an_on_host_command_needs_a_valid_host():
    for host in (None, "", "bad host", "a;b", "*", "compute-02,compute-03", "all", "ALL", "ungrouped", "localhost", "!x", "-x"):
        with pytest.raises(NotExecutable):
            plan_for("docker restart nova_compute", host)


def test_host_pattern_characters_never_reach_the_ansible_limit():
    # The host ends up in `--limit`, where ansible treats `*`, `,`, `:` and `!`
    # as pattern syntax -- so a name containing them must be refused above.
    for evil in ("compute*", "a:b", "a,b", "~.*", "a&b"):
        with pytest.raises(NotExecutable):
            plan_for("systemctl restart node_exporter", evil)


def test_a_catalog_command_is_never_planned_as_something_it_is_not():
    """Walk the real catalog: whatever plans, plans to an operation in the
    documented list -- a new catalog entry cannot sneak in a new verb."""
    allowed = {
        "compute_service.disable", "compute_service.enable", "server.reboot", "network_agent.enable", "port.enable",
        "container.restart", "container.stop", "container.start", "unit.restart", "unit.stop", "unit.start",
    }
    planned = 0
    for entry in CATALOG:
        result = expert._build_result(entry, "compute-02 flagged.", "compute-02", extra_raw={"diagnosed_by": "monitoring"})
        proposal = build_fix_proposal(result["raw_data"])
        if not proposal:
            continue
        for step in [proposal["primary"], *proposal["alternatives"]]:
            try:
                plan = plan_for(step["command"], proposal["host"])
            except NotExecutable:
                continue
            planned += 1
            assert plan.operation in allowed
            assert not step["placeholders"]
    assert planned > 10  # the catalog really does contain runnable fixes


# --------------------------------------------------------------------
# 3. SDK backend
# --------------------------------------------------------------------

class FakeConn:
    def __init__(self, services=None, fail=None):
        self.calls = []
        self._services = services if services is not None else [
            SimpleNamespace(id="svc-0", host="compute-01", binary="nova-compute"),
            SimpleNamespace(id="svc-x", host="compute-02", binary="nova-scheduler"),
            SimpleNamespace(id="svc-1", host="compute-02", binary="nova-compute"),
        ]
        self._fail = fail
        outer = self

        class Compute:
            def services(self, **query):
                # Deliberately ignores the filter, as the sandbox simulator does:
                # the executor must not rely on the server having applied it.
                outer.calls.append(("services", query))
                return iter(outer._services)

            def disable_service(self, service, host=None, binary=None, disabled_reason=None):
                outer._maybe_fail()
                outer.calls.append(("disable_service", service.id, host, binary, disabled_reason))

            def enable_service(self, service, host=None, binary=None):
                outer._maybe_fail()
                outer.calls.append(("enable_service", service.id, host, binary))

            def reboot_server(self, server, reboot_type):
                outer._maybe_fail()
                outer.calls.append(("reboot_server", server, reboot_type))

        class Network:
            def update_agent(self, agent, **attrs):
                outer._maybe_fail()
                outer.calls.append(("update_agent", agent, attrs))

            def update_port(self, port, **attrs):
                outer._maybe_fail()
                outer.calls.append(("update_port", port, attrs))

        self.compute, self.network = Compute(), Network()

    def _maybe_fail(self):
        if self._fail:
            raise self._fail


@pytest.fixture
def conn(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(ex, "_connect", lambda: fake)
    return fake


def test_disable_calls_the_sdk_with_host_binary_and_reason(conn):
    plan = plan_for('openstack compute service set --disable --disable-reason "maintenance" compute-02 nova-compute', None)
    result = ex.run_plan(plan)
    assert result.ok and "disabled" in result.summary
    assert ("services", {"host": "compute-02", "binary": "nova-compute"}) in conn.calls
    assert ("disable_service", "svc-1", "compute-02", "nova-compute", "maintenance") in conn.calls


def test_enable_calls_the_sdk(conn):
    result = ex.run_plan(plan_for("openstack compute service set --enable compute-02 nova-compute", None))
    assert result.ok
    assert ("enable_service", "svc-1", "compute-02", "nova-compute") in conn.calls


def test_a_service_that_is_not_registered_is_a_failure_not_a_success(monkeypatch):
    monkeypatch.setattr(ex, "_connect", lambda: FakeConn(services=[]))
    result = ex.run_plan(plan_for("openstack compute service set --enable compute-02 nova-compute", None))
    assert not result.ok and "No nova-compute service" in result.summary


def test_the_service_is_matched_on_host_and_binary_even_if_the_api_ignores_the_filter(conn):
    """The list contains another host's nova-compute *first*, and this host's
    nova-scheduler: acting on `services[0]` would hit the wrong one."""
    assert ex.run_plan(plan_for("openstack compute service set --disable compute-02 nova-compute", None)).ok
    assert [c[1] for c in conn.calls if c[0] == "disable_service"] == ["svc-1"]


def test_two_matching_services_are_refused_not_guessed(monkeypatch):
    twins = [SimpleNamespace(id=f"s{i}", host="compute-02", binary="nova-compute") for i in (1, 2)]
    monkeypatch.setattr(ex, "_connect", lambda: FakeConn(services=twins))
    result = ex.run_plan(plan_for("openstack compute service set --enable compute-02 nova-compute", None))
    assert not result.ok and "refusing to guess" in result.summary


@pytest.mark.parametrize("flag, kind", [("", "SOFT"), ("--soft ", "SOFT"), ("--hard ", "HARD")])
def test_reboot_uses_the_requested_type(conn, flag, kind):
    assert ex.run_plan(plan_for(f"openstack server reboot {flag}{UUID}", None)).ok
    assert ("reboot_server", UUID, kind) in conn.calls


def test_agent_and_port_enable_set_admin_state_up(conn):
    assert ex.run_plan(plan_for(f"openstack network agent set --enable {UUID}", None)).ok
    assert ex.run_plan(plan_for(f"openstack port set --enable {UUID}", None)).ok
    assert ("update_agent", UUID, {"is_admin_state_up": True}) in conn.calls
    assert ("update_port", UUID, {"is_admin_state_up": True}) in conn.calls


def test_an_sdk_exception_becomes_a_failed_result_not_a_raise(monkeypatch):
    monkeypatch.setattr(ex, "_connect", lambda: FakeConn(fail=RuntimeError("403 Forbidden: policy does not allow this")))
    result = ex.run_plan(plan_for(f"openstack server reboot {UUID}", None))
    assert not result.ok
    assert "403 Forbidden" in (result.error or "")


def test_a_connection_failure_is_also_a_result(monkeypatch):
    def boom():
        raise ConnectionError("keystone unreachable")

    monkeypatch.setattr(ex, "_connect", boom)
    result = ex.run_plan(plan_for(f"openstack server reboot {UUID}", None))
    assert not result.ok and "keystone unreachable" in (result.error or "")


def test_the_sdk_uses_the_operator_profile_not_the_read_only_one(monkeypatch):
    seen = {}
    monkeypatch.setattr(ex.openstack, "connect", lambda **kw: seen.update(kw) or FakeConn())
    ex.run_plan(plan_for(f"openstack server reboot {UUID}", None))
    assert seen == {"cloud": ex.OS_REMEDIATION_CLOUD}
    assert ex.OS_REMEDIATION_CLOUD != "cortex-reader"


# --------------------------------------------------------------------
# 4. Ansible backend
# --------------------------------------------------------------------

def _recap(host, ok=2, changed=1, unreachable=0, failed=0):
    return (
        f"PLAY RECAP *****\n{host}   : ok={ok}    changed={changed}    "
        f"unreachable={unreachable}    failed={failed}    skipped=0    rescued=0    ignored=0\n"
    )


@pytest.fixture
def fake_ansible(monkeypatch, tmp_path):
    """Replaces subprocess.run, and reads the extra-vars file *during* the
    call (it is deleted afterwards)."""
    (tmp_path / "inventory").mkdir()
    (tmp_path / ex.REMEDIATE_PLAYBOOK).write_text("---\n")
    monkeypatch.setattr(ex, "ANSIBLE_DIR", tmp_path)
    state = SimpleNamespace(argv=None, kwargs=None, extra_vars=None, stdout="", stderr="", returncode=0, raises=None)

    def run(argv, **kwargs):
        state.argv, state.kwargs = argv, kwargs
        extra = next(a for a in argv if a.startswith("@"))
        state.extra_vars = json.loads(open(extra[1:]).read())
        state.extra_path = extra[1:]
        if state.raises:
            raise state.raises
        return subprocess.CompletedProcess(argv, state.returncode, state.stdout, state.stderr)

    monkeypatch.setattr(ex.subprocess, "run", run)
    return state


def test_ansible_gets_values_through_an_extra_vars_file_and_one_host(fake_ansible):
    fake_ansible.stdout = _recap("compute-02")
    result = ex.run_plan(plan_for("docker restart nova_compute", "compute-02"))
    assert result.ok
    argv = fake_ansible.argv
    assert argv[0] == "ansible-playbook" and argv[1].endswith(ex.REMEDIATE_PLAYBOOK)
    assert argv[argv.index("--limit") + 1] == "compute-02"
    # The container name is data in a JSON file, never an argument of the command line.
    assert "nova_compute" not in " ".join(argv)
    assert fake_ansible.extra_vars == {
        "remediation_kind": "container", "remediation_action": "restart", "remediation_targets": ["nova_compute"],
        "remediation_host": "compute-02",
    }
    assert fake_ansible.kwargs.get("shell") in (None, False)
    assert fake_ansible.kwargs["timeout"] == ex.ANSIBLE_TIMEOUT_SECONDS


def test_the_extra_vars_file_is_private_and_removed_afterwards(fake_ansible):
    import os
    import stat

    fake_ansible.stdout = _recap("compute-02")
    seen = {}
    real_run = ex.subprocess.run

    def run(argv, **kw):
        path = next(a for a in argv if a.startswith("@"))[1:]
        seen["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        return real_run(argv, **kw)

    ex.subprocess.run = run
    try:
        ex.run_plan(plan_for("systemctl restart node_exporter", "compute-02"))
    finally:
        ex.subprocess.run = real_run
    assert seen["mode"] == 0o600
    assert not os.path.exists(fake_ansible.extra_path)


def test_a_unit_action_is_passed_as_a_unit(fake_ansible):
    fake_ansible.stdout = _recap("compute-02")
    assert ex.run_plan(plan_for("systemctl stop node_exporter", "compute-02")).ok
    assert fake_ansible.extra_vars["remediation_kind"] == "unit"
    assert fake_ansible.extra_vars["remediation_action"] == "stop"


def test_exit_zero_with_no_host_in_the_recap_is_not_success(fake_ansible):
    fake_ansible.stdout = "PLAY RECAP *****\n"  # --limit matched nothing
    result = ex.run_plan(plan_for("docker restart nova_compute", "compute-02"))
    assert not result.ok and "not in the Ansible inventory" in result.summary


def test_a_host_ansible_cannot_match_is_reported_as_not_in_the_inventory(fake_ansible):
    # What a real ansible-playbook prints (and exits 1 with) when --limit matches nothing.
    fake_ansible.stdout = ("[WARNING]: Could not match supplied host pattern, ignoring: compute9-sim\n"
                           "[ERROR]: Specified inventory, host pattern and/or --limit leaves us with no hosts to target.\n")
    fake_ansible.returncode = 1
    result = ex.run_plan(plan_for("docker restart nova_compute", "compute9-sim"))
    assert not result.ok and "not in the Ansible inventory" in result.summary


def test_unreachable_failed_and_nonzero_are_failures(fake_ansible):
    fake_ansible.stdout = _recap("compute-02", unreachable=1)
    fake_ansible.returncode = 4
    assert "unreachable" in ex.run_plan(plan_for("docker restart nova_compute", "compute-02")).summary

    fake_ansible.stdout = _recap("compute-02", failed=1)
    fake_ansible.returncode = 2
    result = ex.run_plan(plan_for("docker restart nova_compute", "compute-02"))
    assert not result.ok and "exit code 2" in result.summary


def test_output_is_kept_but_bounded(fake_ansible):
    fake_ansible.stdout = "x" * 20000 + _recap("compute-02", failed=1)
    fake_ansible.returncode = 2
    result = ex.run_plan(plan_for("docker restart nova_compute", "compute-02"))
    assert 0 < len(result.output) <= ex.OUTPUT_TAIL_CHARS
    assert result.as_details(plan_for("docker restart nova_compute", "compute-02"))["execution"]["ok"] is False


def test_a_timeout_says_the_host_may_be_partly_changed(fake_ansible):
    fake_ansible.raises = subprocess.TimeoutExpired("ansible-playbook", 1)
    result = ex.run_plan(plan_for("docker restart nova_compute", "compute-02"))
    assert not result.ok and result.error == "timeout" and "partly changed" in result.summary


def test_a_missing_playbook_is_a_failed_result(monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "ANSIBLE_DIR", tmp_path)
    result = ex.run_plan(plan_for("docker restart nova_compute", "compute-02"))
    assert not result.ok and "playbook-remediate.yml" in (result.error or "")


# --------------------------------------------------------------------
# 5. Kill switch
# --------------------------------------------------------------------

def test_execution_mode_defaults_by_environment(monkeypatch):
    monkeypatch.delenv("CORTEX_REMEDIATION_EXECUTION", raising=False)
    monkeypatch.setattr(ex, "CORTEX_ENV", "development")
    assert ex.execution_mode() == "live"
    monkeypatch.setattr(ex, "CORTEX_ENV", "production")
    assert ex.execution_mode() == "off"


def test_the_explicit_setting_wins_in_both_directions(monkeypatch):
    monkeypatch.setattr(ex, "CORTEX_ENV", "production")
    monkeypatch.setenv("CORTEX_REMEDIATION_EXECUTION", "live")
    assert ex.execution_mode() == "live"
    monkeypatch.setattr(ex, "CORTEX_ENV", "development")
    monkeypatch.setenv("CORTEX_REMEDIATION_EXECUTION", "off")
    assert ex.execution_mode() == "off"
    monkeypatch.setenv("CORTEX_REMEDIATION_EXECUTION", "nonsense")  # unknown -> the safe default for the env
    assert ex.execution_mode() == "live"


def test_the_playbook_asserts_its_own_allow_list():
    """The playbook is the last line of defence if something upstream is wrong:
    it must refuse unknown kinds/actions by itself."""
    from pathlib import Path

    root = Path(__file__).resolve()
    playbook = next(p / "infra" / "ansible" / ex.REMEDIATE_PLAYBOOK for p in root.parents if (p / "infra" / "ansible" / ex.REMEDIATE_PLAYBOOK).exists())
    text = playbook.read_text()
    assert "remediation_kind in ['container', 'unit']" in text
    assert "remediation_action in ['restart', 'stop', 'start']" in text
    assert "argv:" in text  # docker is run without a shell
    assert "ansible_play_hosts_all | length == 1" in text  # `--limit all` / a group name must not fan out
    assert "inventory_hostname == remediation_host" in text
    sandbox = playbook.parent.parent / "ansible-sandbox" / ex.REMEDIATE_PLAYBOOK
    assert sandbox.read_text() == text  # the sandbox inventory must carry the same playbook
