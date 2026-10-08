"""Roadmap 4.2: a proposed fix is simulated against the Living Model and the
expected impact is shown alongside it, before approval
(services/impact_simulator.py, adr-0013).

Neo4j is faked at the one function that reads it (`_fetch_context`, and
`graph_db.driver` for the Cypher result-shape test); the rules themselves are
pure, so most tests hand them a plain context dict.
"""
import pytest

import app.agents.nodes.monitoring as monitoring
import app.agents.nodes.openstack_expert as expert
import app.agents.nodes.remediation as remediation
import app.graph_db as graph_db
import app.services.impact_simulator as sim
from app.agents.graph import app_graph
from app.agents.nodes.critic import critic_check
from app.agents.nodes.openstack_expert_catalog import CATALOG
from app.agents.nodes.remediation import build_fix_proposal, render_fix_proposal, render_impact, remediation_agent

HOST = "compute-02"


def _ctx(**over):
    ctx = {
        "node": {"id": HOST, "health": "up"},
        "services": [
            {"id": f"nova-compute@{HOST}", "binary": "nova-compute", "state": "up", "status": "enabled", "serves": []},
            {"id": f"neutron-l3-agent@{HOST}", "binary": "neutron-l3-agent", "state": "up", "status": "enabled",
             "serves": [{"id": "r1", "label": "Router", "name": "router-main"}]},
            {"id": f"neutron-dhcp-agent@{HOST}", "binary": "neutron-dhcp-agent", "state": "up", "status": "enabled",
             "serves": [{"id": "n1", "label": "Network", "name": "net-app"}]},
        ],
        "containers": [
            {"name": "nova_compute", "state": "running", "service_id": f"nova-compute@{HOST}"},
            {"name": "rabbitmq", "state": "down", "service_id": None},
        ],
        "instances": [
            {"id": "i1", "name": "web-1", "status": "ACTIVE", "vcpus": 4, "ram_mb": 8192},
            {"id": "i2", "name": "db-1", "status": "ACTIVE", "vcpus": 8, "ram_mb": 16384},
            {"id": "i3", "name": "cache-1", "status": "ACTIVE", "vcpus": 2, "ram_mb": 2048},
        ],
        "network_instances": {"n1": [{"id": "i1", "name": "web-1"}, {"id": "i2", "name": "db-1"}]},
        "router_floating_ips": {"r1": 4},
        "compute_fleet": [
            {"id": "nova-compute@compute-01", "state": "up", "status": "enabled", "node_id": "compute-01"},
            {"id": f"nova-compute@{HOST}", "state": "up", "status": "enabled", "node_id": HOST},
        ],
        "control_plane": {"total": 14, "up": 13},
    }
    ctx.update(over)
    return ctx


def _run(command, **ctx_over):
    return sim.simulate(sim.parse_action(command, HOST), _ctx(**ctx_over))


# --------------------------------------------------------------------
# parse_action
# --------------------------------------------------------------------

@pytest.mark.parametrize(
    "command, kind, verb, target",
    [
        ("docker restart nova_compute", "container", "restart", "nova_compute"),
        ("sudo docker stop rabbitmq", "container", "stop", "rabbitmq"),
        ('openstack compute service set --disable --disable-reason "high cpu" compute-02 nova-compute',
         "compute-service", "disable", "nova-compute"),
        ("openstack compute service set --enable compute-02 nova-compute", "compute-service", "enable", "nova-compute"),
        ("openstack server reboot <instance_id>", "instance", "reboot", "<instance_id>"),
        ("openstack server rebuild 0a1b2c3d-1111-2222-3333-444455556666 img", "instance", "rebuild",
         "0a1b2c3d-1111-2222-3333-444455556666"),
        ("sudo reboot", "host-reboot", "reboot", None),
        ("docker system prune -a --volumes", "docker-prune", "prune", None),
        ("journalctl --vacuum-size=500M", "housekeeping", "vacuum", None),
        ("kill -TERM <pid>", "process-kill", "kill", None),
        ("systemctl restart node_exporter", "host-unit", "restart", "node_exporter"),
        ("openstack port set --enable <port_id>", "no-topology-effect", "none", None),
        ("totally-unknown --thing", "unknown", None, None),
    ],
)
def test_commands_are_classified_into_actions(command, kind, verb, target):
    action = sim.parse_action(command, HOST)
    assert (action["kind"], action["verb"], action["target"]) == (kind, verb, target)


def test_an_api_command_names_its_own_host_but_an_on_host_command_uses_the_proposals():
    api = sim.parse_action("openstack compute service set --disable compute-07 nova-compute", HOST)
    assert api["host"] == "compute-07"
    assert sim.parse_action("docker restart nova_compute", HOST)["host"] == HOST


# --------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------

def test_restarting_nova_libvirt_is_disruptive_because_it_takes_every_guest_down():
    r = _run("docker restart nova_libvirt")
    assert r["verdict"] == "disruptive" and r["reversible"] is False
    assert r["counts"]["instances_interrupted"] == 3
    assert "web-1" in r["headline"] and "db-1" in r["headline"]
    assert sim.ASSUME_GUESTS_IN_LIBVIRT in r["assumptions"]


def test_restarting_nova_compute_leaves_guests_running_but_unmanageable():
    r = _run("docker restart nova_compute")
    assert r["verdict"] == "caution"
    assert r["counts"]["instances_unmanageable"] == 3
    assert "keep running" in r["headline"]


def test_restarting_the_l3_agent_reports_routers_floating_ips_and_is_not_guest_loss():
    r = _run("docker restart neutron_l3_agent")
    assert r["verdict"] == "caution"
    assert (r["counts"]["routers_served"], r["counts"]["floating_ips_behind"]) == (1, 4)
    assert "not lost guests" in r["headline"]


def test_restarting_the_dhcp_agent_counts_instances_on_the_networks_it_serves():
    r = _run("docker restart neutron_dhcp_agent")
    assert r["counts"]["networks_served"] == 1
    assert r["counts"]["instances_on_affected_networks"] == 2


def test_an_agent_that_serves_nothing_is_safe_to_restart():
    r = _run("docker restart neutron_l3_agent", services=[
        {"id": "x", "binary": "neutron-l3-agent", "state": "up", "status": "enabled", "serves": []}])
    assert r["verdict"] == "safe"


@pytest.mark.parametrize("container", ["rabbitmq", "mariadb", "keystone", "haproxy"])
def test_control_plane_containers_blip_the_api_but_not_the_workloads(container):
    r = _run(f"docker restart {container}")
    assert r["verdict"] == "caution"
    assert r["counts"]["services_depending"] == 14
    assert "not touched" in r["headline"]
    assert sim.ASSUME_SINGLE_CONTROL_PLANE in r["assumptions"]


def test_disabling_scheduling_leaves_running_instances_alone_and_reports_remaining_capacity():
    r = _run('openstack compute service set --disable --disable-reason "x" compute-02 nova-compute')
    assert r["verdict"] == "caution" and r["reversible"] is True
    assert r["counts"] == {"instances_on_host": 3, "other_compute_hosts_available": 1, "compute_hosts_total": 2}
    assert all(e["effect"] == "unaffected" for e in r["effects"] if e["entity"] == "instance")


def test_disabling_the_last_available_compute_host_is_disruptive():
    only_me = [{"id": f"nova-compute@{HOST}", "state": "up", "status": "enabled", "node_id": HOST},
               {"id": "nova-compute@compute-01", "state": "down", "status": "enabled", "node_id": "compute-01"}]
    r = _run("openstack compute service set --disable compute-02 nova-compute", compute_fleet=only_me)
    assert r["verdict"] == "disruptive"
    assert "new instances could not be scheduled anywhere" in r["headline"]


def test_enabling_scheduling_is_safe():
    assert _run("openstack compute service set --enable compute-02 nova-compute")["verdict"] == "safe"


@pytest.mark.parametrize("verb, verdict, reversible", [
    ("reboot", "caution", True), ("rebuild", "disruptive", False), ("delete", "disruptive", False),
    ("reset-state", "caution", True), ("migrate", "caution", True),
])
def test_instance_actions(verb, verdict, reversible):
    r = _run(f"openstack server {verb} <instance_id>")
    assert (r["verdict"], r["reversible"]) == (verdict, reversible)
    assert r["counts"].get("instances_interrupted", 1) == 1 or "instances_state_changed" in r["counts"]


def test_an_instance_that_is_in_the_graph_is_named():
    r = _run("openstack server reboot i1")
    assert "i1" in r["headline"] or "web-1" in r["headline"]


def test_rebooting_the_host_takes_everything_on_it_down():
    r = _run("sudo reboot")
    assert r["verdict"] == "disruptive"
    assert r["counts"] == {"instances_interrupted": 3, "services_unavailable": 3, "containers_restarted": 2}


def test_pruning_warns_that_the_stopped_container_would_be_deleted():
    r = _run("docker system prune -a --volumes")
    assert r["verdict"] == "disruptive"
    assert r["counts"]["stopped_containers_deleted"] == 1
    assert "rabbitmq" in r["headline"]


def test_restarting_node_exporter_is_a_monitoring_gap_not_an_outage():
    r = _run("systemctl restart node_exporter")
    assert r["verdict"] == "caution" and "blinds Cortex" in r["headline"]


def test_housekeeping_touches_no_openstack_object():
    r = _run("journalctl --vacuum-size=500M")
    assert r["verdict"] == "safe" and r["effects"] == []


def test_a_command_with_no_rule_is_reported_unmodelled_not_safe():
    r = _run("totally-unknown --thing")
    assert r["status"] == "unmodelled"
    assert "no impact model" in r["headline"]


def test_a_container_without_a_rule_is_unmodelled():
    assert _run("docker restart some_custom_thing")["status"] == "unmodelled"


def test_an_on_host_command_to_a_host_the_twin_sees_down_carries_a_warning():
    r = _run("docker restart nova_compute", node={"id": HOST, "health": "down"})
    assert any("currently sees this host as down" in w for w in r["warnings"])


def test_long_effect_lists_are_capped_but_the_total_is_kept():
    many = [{"id": f"i{n}", "name": f"vm-{n}", "status": "ACTIVE", "vcpus": 1, "ram_mb": 1} for n in range(20)]
    r = _run("docker restart nova_libvirt", instances=many)
    assert len(r["effects"]) == sim.MAX_EFFECTS and r["effects_total"] == 21
    assert r["counts"]["instances_interrupted"] == 20
    assert "and others" in r["headline"]


# --------------------------------------------------------------------
# Risk only ever goes up
# --------------------------------------------------------------------

def test_simulation_raises_but_never_lowers_the_command_text_risk():
    disruptive = {"status": "simulated", "verdict": "disruptive"}
    safe = {"status": "simulated", "verdict": "safe"}
    assert sim.effective_risk("medium", disruptive) == "high"
    assert sim.effective_risk("high", safe) == "high"
    assert sim.effective_risk("medium", safe) == "medium"


def test_an_unsimulated_step_keeps_its_static_risk():
    assert sim.effective_risk("medium", {"status": "unavailable", "verdict": None}) == "medium"
    assert sim.effective_risk("medium", None) == "medium"


# --------------------------------------------------------------------
# Failure modes: the proposal always survives
# --------------------------------------------------------------------

def test_an_unreadable_graph_is_reported_unavailable_not_raised(monkeypatch):
    def boom(host):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(sim, "_fetch_context", boom)
    r = sim.simulate_command("docker restart nova_compute", HOST)
    assert r["status"] == "unavailable" and "could not be read" in r["headline"]


def test_a_host_missing_from_the_graph_is_reported_unavailable(monkeypatch):
    monkeypatch.setattr(sim, "_fetch_context", lambda host: None)
    r = sim.simulate_command("docker restart nova_compute", HOST)
    assert r["status"] == "unavailable" and "not in the Living Model" in r["headline"]


def test_an_unfilled_host_placeholder_is_unavailable_without_touching_the_graph(monkeypatch):
    monkeypatch.setattr(sim, "_fetch_context", lambda host: pytest.fail("must not read the graph"))
    r = sim.simulate_command("openstack compute service set --disable <host> nova-compute", None)
    assert r["status"] == "unavailable" and "host is not known" in r["headline"]


def test_the_graph_is_read_once_per_host_per_proposal(monkeypatch):
    calls = []
    monkeypatch.setattr(sim, "_fetch_context", lambda host: calls.append(host) or _ctx())
    cache: dict = {}
    sim.simulate_command("docker restart nova_compute", HOST, cache)
    sim.simulate_command("docker restart rabbitmq", HOST, cache)
    assert calls == [HOST]


# --------------------------------------------------------------------
# On a real proposal
# --------------------------------------------------------------------

BY_ID = {e["id"]: e for e in CATALOG}
EVIDENCE = "compute-02's cpu usage is flagged critical (z=4.2, current value 97.3)."


def _proposal(symptom="host-cpu-pressure", ctx=None, evidence=EVIDENCE):
    result = expert._build_result(BY_ID[symptom], evidence, HOST, {"diagnosed_by": "monitoring"})
    return build_fix_proposal(result["raw_data"]), result


@pytest.fixture
def graph(monkeypatch):
    monkeypatch.setattr(sim, "_fetch_context", lambda host: _ctx())
    monkeypatch.setattr(expert, "load_published_entries", lambda: [])
    monkeypatch.setattr(expert, "search_official_docs", lambda query, top_k=3: [])
    monkeypatch.setattr(expert, "run_web_search", lambda query, max_results=5: [])


def test_every_step_of_a_proposal_carries_a_simulation_and_an_effective_risk(graph):
    proposal, _ = _proposal()
    out = sim.simulate_proposal(proposal)
    assert out["simulation"] is out["primary"]["simulation"]
    for step in [out["primary"], *out["alternatives"]]:
        assert step["simulation"]["status"] in ("simulated", "unmodelled", "unavailable")
        assert step["effective_risk"]
    assert out["primary"]["simulation"]["verdict"] == "caution"
    assert "simulation" not in proposal["primary"]  # the 4.1 object is not mutated


def test_a_gentler_runnable_alternative_is_surfaced(graph, monkeypatch):
    proposal, _ = _proposal("nova-compute-down", evidence="nova-compute is down on compute-02.")
    # restart nova_compute (caution) vs. a made-up safe alternative
    proposal["alternatives"] = [{**proposal["primary"], "command": "openstack compute service set --enable compute-02 nova-compute",
                                 "description": "Re-enable scheduling", "placeholders": []}]
    out = sim.simulate_proposal(proposal)
    assert out["safer_alternative"]["verdict"] == "safe"
    assert "Re-enable scheduling" in render_impact(out)


def test_an_alternative_needing_discovery_is_never_called_safer(graph):
    proposal, _ = _proposal("nova-compute-down", evidence="x")
    proposal["alternatives"] = [{**proposal["primary"], "command": "openstack server reset-state <instance_id>",
                                 "description": "d", "placeholders": ["<instance_id>"]}]
    assert sim.simulate_proposal(proposal)["safer_alternative"] is None


def test_the_rendered_impact_names_the_verdict_effects_and_reversibility(graph):
    proposal, _ = _proposal()
    text = render_impact(sim.simulate_proposal(proposal))
    assert text.startswith("#### Expected impact (simulated on the Living Model)")
    assert "Workloads keep running, but check the effects below." in text
    assert "web-1" in text and "unaffected" in text
    assert "Reversible." in text and "Lasts: until re-enabled." in text


def test_a_disruptive_verdict_is_rendered_as_a_warning_callout(graph, monkeypatch):
    proposal, _ = _proposal("nova-compute-down", evidence="nova-compute is down on compute-02.")
    proposal["primary"] = {**proposal["primary"], "command": "docker restart nova_libvirt"}
    text = render_impact(sim.simulate_proposal(proposal))
    assert "> [!WARNING]" in text and "Disrupts running workloads." in text
    assert "Rated high risk after simulation" in text


def test_an_unavailable_simulation_is_rendered_as_not_simulated(monkeypatch):
    monkeypatch.setattr(sim, "_fetch_context", lambda host: None)
    proposal, _ = _proposal()
    text = render_impact(sim.simulate_proposal(proposal))
    assert "**Not simulated.**" in text and "not in the Living Model" in text


@pytest.mark.parametrize("entry", CATALOG, ids=lambda e: e["id"])
def test_every_simulated_proposal_passes_the_critics_numeric_grounding(entry, graph):
    result = expert._build_result(entry, EVIDENCE, HOST, {"diagnosed_by": "anomaly"})
    state = {"user_query": "something's wrong with compute-02", "known_nodes": [], "target_agent": "openstack_expert",
             "agent_result": result, "error": None, "failures": []}
    before = critic_check(dict(state))["critic_verdict"]["flagged_claims"]
    after_state = remediation_agent(dict(state))
    assert "Expected impact" in after_state["agent_result"]["summary"]
    after = critic_check(after_state)["critic_verdict"]["flagged_claims"]
    assert [c for c in after if c not in before] == []


def test_a_simulator_bug_costs_the_proposal_its_impact_section_not_the_proposal(monkeypatch):
    def boom(proposal):
        raise RuntimeError("bug")

    monkeypatch.setattr(remediation, "simulate_proposal", boom)
    proposal, result = _proposal()
    state = {"user_query": "q", "known_nodes": [], "target_agent": "openstack_expert",
             "agent_result": result, "error": None, "failures": []}
    out = remediation_agent(state)
    assert "### Proposed fix" in out["agent_result"]["summary"]
    assert "Expected impact" not in out["agent_result"]["summary"]


def test_plain_language_risk_follows_the_simulated_risk(graph):
    proposal, result = _proposal("nova-compute-down", evidence="nova-compute is down on compute-02.")
    state = {"user_query": "q", "known_nodes": [], "target_agent": "openstack_expert", "error": None,
             "failures": [], "agent_result": result}
    raw = remediation_agent(state)["agent_result"]["raw_data"]["fix_proposal"]
    assert raw["primary"]["effective_risk"] == "medium"  # caution simulates to medium: unchanged
    assert remediation._RISK_PHRASE["medium"] in raw["plain_language"]


def test_plain_language_is_rewritten_when_simulation_raises_the_risk(graph):
    proposal, result = _proposal("nova-compute-down", evidence="nova-compute is down on compute-02.")
    result["raw_data"]["remediation_commands"] = [
        {"command": "docker restart nova_libvirt", "description": "Restart libvirt", "read_only": False}]
    state = {"user_query": "q", "known_nodes": [], "target_agent": "openstack_expert", "error": None,
             "failures": [], "agent_result": result}
    raw = remediation_agent(state)["agent_result"]["raw_data"]["fix_proposal"]
    assert raw["primary"]["risk"] == "medium" and raw["primary"]["effective_risk"] == "high"
    assert remediation._RISK_PHRASE["high"] in raw["plain_language"]
    assert remediation._RISK_PHRASE["medium"] not in raw["plain_language"]


# --------------------------------------------------------------------
# graph_db.fetch_impact_context result shape (Cypher faked)
# --------------------------------------------------------------------

class _Records(list):
    def single(self):
        return self[0] if self else None


class _Session:
    def __init__(self, node_exists=True):
        self.node_exists = node_exists

    def run(self, query, **params):
        q = " ".join(query.split())
        if q.startswith("MATCH (n:Node {id: $id}) RETURN properties(n)"):
            return _Records([{"node": {"id": params["id"], "health": "up"}}] if self.node_exists else [])
        if "MATCH (s:Service)-[:RUNS_ON]->(:Node" in q:
            return [{"id": "neutron-dhcp-agent@h", "binary": "neutron-dhcp-agent", "state": "up", "status": "enabled",
                     "serves": [{"id": "n1", "label": "Network", "name": "net"}, {"id": "r1", "label": "Router", "name": "rt"}]}]
        if "MATCH (k:Container)-[:RUNS_ON]" in q:
            return [{"name": "rabbitmq", "state": "down", "service_id": None}]
        if "MATCH (i:Instance)-[:RUNS_ON]" in q:
            return [{"id": "i1", "name": "web-1", "status": "ACTIVE", "vcpus": 2, "ram_mb": 512}]
        if "HAS_PORT" in q:
            assert params["ids"] == ["n1"]
            return [{"network_id": "n1", "id": "i1", "name": "web-1"}]
        if "FloatingIP" in q:
            assert params["ids"] == ["r1"]
            return [{"router_id": "r1", "floating_ips": 4}]
        if "binary: 'nova-compute'" in q:
            return [{"id": "nova-compute@h", "state": "up", "status": "enabled", "node_id": "h"}]
        if "count(s) AS total" in q:
            return _Records([{"total": 9, "up": 8}])
        raise AssertionError(f"unexpected query: {q}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Driver:
    def __init__(self, session):
        self._s = session

    def session(self):
        return self._s


def test_fetch_impact_context_assembles_the_whole_picture(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _Driver(_Session()))
    ctx = graph_db.fetch_impact_context("h")
    assert ctx["node"]["id"] == "h"
    assert ctx["services"][0]["serves"][0]["label"] == "Network"
    assert ctx["containers"] == [{"name": "rabbitmq", "state": "down", "service_id": None}]
    assert ctx["instances"][0]["name"] == "web-1"
    assert ctx["network_instances"] == {"n1": [{"id": "i1", "name": "web-1"}]}
    assert ctx["router_floating_ips"] == {"r1": 4}
    assert ctx["compute_fleet"][0]["node_id"] == "h"
    assert ctx["control_plane"] == {"total": 9, "up": 8}
    # ...and the simulator accepts exactly this shape.
    assert sim.simulate(sim.parse_action("docker restart neutron_dhcp_agent", "h"), ctx)["counts"]["networks_served"] == 1


def test_fetch_impact_context_returns_none_for_an_unknown_node(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _Driver(_Session(node_exists=False)))
    assert graph_db.fetch_impact_context("ghost") is None


# --------------------------------------------------------------------
# The demo, through the compiled graph
# --------------------------------------------------------------------

def test_demo_the_fix_arrives_with_its_simulated_impact(graph, monkeypatch):
    """Roadmap 4.2 demo: proposed fix + simulation output shown alongside it."""
    monkeypatch.setattr(
        monitoring, "collect_metrics",
        lambda: [{"instance": "10.0.1.12:9100", "node": HOST, "role": "compute", "cpu_percent": 96,
                  "memory_percent": 40, "disk_percent": 30, "status": "up", "health": "critical"}],
    )
    result = app_graph.invoke({"user_query": "how is compute-02 doing", "failures": [],
                               "known_nodes": [{"hostname": HOST, "role": "compute", "instance": "10.0.1.12:9100"}]})

    answer = result["final_answer"]
    assert result["target_agent"] == "remediation"
    assert answer.index("### Proposed fix") < answer.index("#### Expected impact (simulated on the Living Model)")
    assert "3 running instance(s) are not touched" in answer
    assert "1 other compute host(s) remain available" in answer
    assert result["critic_verdict"]["status"] == "pass"
    proposal = result["agent_result"]["raw_data"]["fix_proposal"]
    assert proposal["simulation"]["status"] == "simulated"
    event = next(e for e in result["trace_events"] if e["node"] == "remediation")
    assert event["detail"]["proposal"]["simulation"] == "simulated"
    assert event["detail"]["proposal"]["verdict"] == "caution"
