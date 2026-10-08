"""Remediation Agent (v1.3, roadmap 4.1): "trigger a known issue -> the agent
proposes the exact fix text".

Three layers, same convention as the other agent tests -- every external
dependency monkeypatched at its own call site, no Postgres/Prometheus/Loki/
NVIDIA involved (the agent itself is deterministic and calls no LLM):

1. the pure helpers (risk, placeholders, undo, note splitting);
2. `build_fix_proposal` / `remediation_agent` against the REAL catalog, with
   property tests over every entry so a newly-added catalog entry that breaks
   a rule here fails loudly instead of shipping a bad proposal;
3. the compiled graph end to end -- the demo scenario itself.
"""
from types import SimpleNamespace

import pytest

import app.agents.intent_router as intent_router
import app.agents.nodes.monitoring as monitoring
import app.agents.nodes.openstack_expert as expert
import app.agents.nodes.remediation as remediation
import app.services.impact_simulator as impact_simulator
from app.agents.graph import app_graph
from app.agents.nodes.critic import critic_check
from app.agents.nodes.openstack_expert_catalog import CATALOG
from app.agents.nodes.remediation import (
    _classify_risk,
    _derive_undo,
    _needs_discovery,
    _placeholders,
    _split_note,
    asks_for_fix,
    build_fix_proposal,
    remediation_agent,
    render_fix_proposal,
    should_propose_fix,
)
from app.services import llm_client

NODE = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
KNOWN_NODES = [NODE]
EVIDENCE = "compute-02's cpu usage is flagged critical (z=4.2, current value 97.3)."
BY_ID = {entry["id"]: entry for entry in CATALOG}


@pytest.fixture(autouse=True)
def _hermetic_expert(monkeypatch):
    """The expert reads published catalog entries from Postgres and falls back
    to docs/web search -- none of which exist in a unit test."""
    monkeypatch.setattr(expert, "load_published_entries", lambda: [])
    monkeypatch.setattr(expert, "search_official_docs", lambda query, top_k=3: [])
    monkeypatch.setattr(expert, "run_web_search", lambda query, max_results=5: [])
    # 4.2: proposals are simulated against the Living Model (Neo4j) -- none here.
    monkeypatch.setattr(impact_simulator, "_fetch_context", lambda host: None)


def _expert_result(symptom_id, evidence=EVIDENCE, hostname="compute-02", diagnosed_by="monitoring"):
    return expert._build_result(
        BY_ID[symptom_id], evidence, hostname, extra_raw={"diagnosed_by": diagnosed_by}
    )


def _chained_state(symptom_id="host-cpu-pressure", query="something's wrong with compute-02", **kw):
    return {
        "user_query": query,
        "known_nodes": KNOWN_NODES,
        "target_agent": "openstack_expert",
        "agent_result": _expert_result(symptom_id, **kw),
        "error": None,
        "failures": [],
    }


# --------------------------------------------------------------------
# 1. Pure helpers
# --------------------------------------------------------------------

@pytest.mark.parametrize(
    "command, expected",
    [
        ("docker restart nova_compute", "medium"),
        ("systemctl restart node_exporter", "medium"),
        ("openstack compute service set --disable compute-02 nova-compute", "medium"),
        ("openstack server reboot <instance_id>", "medium"),  # one guest, not the whole host
        ("kill -TERM <pid>", "medium"),
        ("openstack server rebuild <instance_id> <image>", "high"),  # reimages the root disk
        ("openstack server delete <instance_id>", "high"),
        ("openstack server reset-state --active <instance_id>", "high"),
        ("openstack compute service set --enable compute-02 nova-compute", "low"),
        ("docker system prune -a --volumes", "high"),  # irreversible bulk removal
        ("journalctl --vacuum-size=500M", "low"),
        ("sudo reboot", "high"),  # a bare host reboot takes every guest with it
        ("evacuate_everything --force", "high"),
        ("some-tool --flag  (CAUTION: touches live data)", "medium"),  # catalog's own warning marker
    ],
)
def test_risk_is_judged_from_the_command_text(command, expected):
    assert _classify_risk(command, read_only=False) == expected


def test_a_read_only_step_is_always_low_risk():
    assert _classify_risk("docker restart nova_compute", read_only=True) == "low"


def test_compound_command_is_rated_by_its_most_dangerous_part():
    assert _classify_risk("openstack server show <id>  (then) openstack server delete <id>", False) == "high"


def test_placeholders_are_distinct_and_in_order():
    cmd = "openstack server rebuild <instance_id> <image> --note <instance_id>"
    assert _placeholders(cmd) == ["<instance_id>", "<image>"]
    assert _placeholders("docker restart nova_compute") == []


def test_undo_for_disable_is_the_exact_enable_without_the_reason():
    cmd = 'openstack compute service set --disable --disable-reason "investigating CPU pressure" compute-02 nova-compute'
    assert _derive_undo(cmd) == "openstack compute service set --enable compute-02 nova-compute"


def test_undo_for_stop_is_start_and_unknown_commands_get_none():
    assert _derive_undo("docker stop nova_compute") == "docker start nova_compute"
    assert _derive_undo("docker restart nova_compute") is None
    assert _derive_undo("openstack server delete <instance_id>") is None


def test_inline_catalog_commentary_is_split_off_the_command():
    assert _split_note("top -o %CPU  (or: ps aux --sort=-%cpu | head -15)") == (
        "top -o %CPU", "or: ps aux --sort=-%cpu | head -15",
    )
    assert _split_note("virsh list --all   # run inside the container") == (
        "virsh list --all", "run inside the container",
    )
    assert _split_note("docker restart nova_compute") == ("docker restart nova_compute", None)
    compound = "openstack server show <id>  (then, if it's the guest) openstack server reboot <id>"
    assert _split_note(compound) == (compound, None)  # no clean split -> left whole, not mangled


@pytest.mark.parametrize(
    "query, expected",
    [
        ("how do I fix a stuck instance", True),
        ("propose a fix for the high CPU on compute-02", True),
        ("what should I do about nova-compute", True),
        ("can you resolve this", True),
        ("how do I check if nova-compute is running", False),
        ("what commands show disk usage on a compute node", False),
        ("how do I confirm neutron-dhcp-agent is up", False),
        ("is compute-02 healthy", False),
    ],
)
def test_fix_intent_detection_does_not_fire_on_pure_check_questions(query, expected):
    assert asks_for_fix(query) is expected


# --------------------------------------------------------------------
# 2a. The proposal, against the real catalog
# --------------------------------------------------------------------

def test_cpu_incident_proposes_containment_first_not_the_unrunnable_guest_reboot():
    proposal = build_fix_proposal(_expert_result("host-cpu-pressure")["raw_data"])
    primary = proposal["primary"]

    # Fully runnable now (host filled in, nothing to guess) beats a reboot of a
    # guest nobody has identified yet (<instance_id>).
    assert primary["command"] == (
        'openstack compute service set --disable --disable-reason "investigating CPU pressure" '
        "compute-02 nova-compute"
    )
    assert primary["placeholders"] == [] and proposal["inputs_needed"] == []
    assert primary["risk"] == "medium"
    assert primary["undo"] == "openstack compute service set --enable compute-02 nova-compute"
    assert primary["run_where"] == "openstack-cli"
    assert proposal["host"] == "compute-02"
    # The guest reboot and the bare kill are still offered, as alternatives.
    assert {a["placeholders"][0] for a in proposal["alternatives"]} == {"<instance_id>", "<pid>"}


def test_service_down_proposal_is_a_restart_that_must_run_on_the_host():
    proposal = build_fix_proposal(_expert_result("nova-compute-down")["raw_data"])
    assert proposal["primary"]["command"] == "docker restart nova_compute"
    assert proposal["primary"]["run_where"] == "on-host"
    text = render_fix_proposal(proposal)
    assert "Run it on the host itself (`compute-02`)" in text
    assert "a restart can simply be repeated" in text


def test_destructive_command_is_never_the_recommended_step_when_a_safer_one_exists():
    proposal = build_fix_proposal(_expert_result("host-disk-pressure", evidence="")["raw_data"])
    assert proposal["primary"]["command"] == "journalctl --vacuum-size=500M"
    prune = next(a for a in proposal["alternatives"] if "prune" in a["command"])
    assert prune["risk"] == "high"
    assert "removes ALL unused" in prune["note"]  # the catalog's warning travels with it
    assert "Heads-up: Removes ALL unused" in render_fix_proposal(proposal)


def test_when_every_step_needs_input_the_lowest_risk_one_wins_and_says_what_is_needed():
    proposal = build_fix_proposal(_expert_result("instance-stuck-in-error")["raw_data"])
    assert proposal["inputs_needed"] == proposal["primary"]["placeholders"] != []
    assert "needs a value from you first" in proposal["plain_language"]
    assert "Fill in before running" in render_fix_proposal(proposal)
    ranks = {"low": 0, "medium": 1, "high": 2}
    assert all(ranks[proposal["primary"]["risk"]] <= ranks[a["risk"]] for a in proposal["alternatives"]
               if _needs_discovery(a))


def test_no_hostname_known_leaves_the_host_placeholder_for_the_person_to_fill():
    proposal = build_fix_proposal(_expert_result("host-cpu-pressure", evidence="", hostname=None)["raw_data"])
    assert proposal["host"] is None
    assert proposal["inputs_needed"] == ["<host>"]
    # A missing host name is trivial to supply; an unidentified guest is not --
    # so containment is still the recommendation, same as when the host is known.
    assert proposal["primary"]["command"].startswith("openstack compute service set --disable")
    assert not _needs_discovery(proposal["primary"])


def test_proposal_is_always_proposal_only():
    proposal = build_fix_proposal(_expert_result("host-ram-pressure")["raw_data"])
    assert (proposal["status"], proposal["executed"], proposal["requires_approval"]) == ("proposed", False, True)
    assert "Proposal only" in render_fix_proposal(proposal)


def test_proposal_id_is_deterministic_for_the_same_incident_and_differs_across_hosts():
    a = build_fix_proposal(_expert_result("nova-compute-down")["raw_data"])
    b = build_fix_proposal(_expert_result("nova-compute-down")["raw_data"])
    c = build_fix_proposal(_expert_result("nova-compute-down", hostname="compute-07")["raw_data"])
    assert a["proposal_id"] == b["proposal_id"] != c["proposal_id"]


@pytest.mark.parametrize("raw", [
    {"source": "official_docs", "matched_symptom_id": None},
    {"source": "web_search"},
    {"source": "catalog", "matched_symptom_id": None},
    {},
])
def test_no_proposal_unless_it_is_a_real_catalog_match(raw):
    assert build_fix_proposal(raw) is None


def test_no_proposal_for_an_entry_with_only_read_only_steps():
    raw = _expert_result("host-cpu-pressure")["raw_data"]
    raw["remediation_commands"] = [{**c, "read_only": True} for c in raw["remediation_commands"]]
    assert build_fix_proposal(raw) is None


@pytest.mark.parametrize("entry", CATALOG, ids=lambda e: e["id"])
def test_every_catalog_entry_yields_a_sound_proposal(entry):
    result = expert._build_result(entry, EVIDENCE, "compute-02", {"diagnosed_by": "anomaly"})
    proposal = build_fix_proposal(result["raw_data"])
    assert proposal is not None, f"{entry['id']} has no state-changing remediation step"

    primary = proposal["primary"]
    assert not primary["read_only"]
    # The recommended step is the best available: nothing in the alternatives is
    # both fully runnable AND lower risk when the primary isn't runnable.
    ranks = {"low": 0, "medium": 1, "high": 2}
    if _needs_discovery(primary):
        assert all(_needs_discovery(a) for a in proposal["alternatives"])
    for alt in proposal["alternatives"]:
        if _needs_discovery(alt) == _needs_discovery(primary):
            assert ranks[primary["risk"]] <= ranks[alt["risk"]]
    # Copy-pasteable: no leftover inline commentary glued onto the command.
    assert "  (" not in primary["command"] and "  #" not in primary["command"]
    # Anything it would run is rendered, and rendering never raises.
    assert primary["command"] in render_fix_proposal(proposal)


@pytest.mark.parametrize("entry", CATALOG, ids=lambda e: e["id"])
def test_proposal_text_never_trips_the_critics_numeric_grounding(entry):
    """The critic flags any number in the summary that isn't in raw_data. The
    proposal must add none of its own (boilerplate stays digit-free)."""
    state = {
        "user_query": "something's wrong with compute-02", "known_nodes": KNOWN_NODES,
        "target_agent": "openstack_expert", "error": None, "failures": [],
        "agent_result": expert._build_result(entry, EVIDENCE, "compute-02", {"diagnosed_by": "anomaly"}),
    }
    before = critic_check(dict(state))["critic_verdict"]["flagged_claims"]
    after_state = remediation_agent(dict(state))
    after = critic_check(after_state)["critic_verdict"]["flagged_claims"]
    assert [claim for claim in after if claim not in before] == []


# --------------------------------------------------------------------
# 2b. should_propose_fix (the conditional edge)
# --------------------------------------------------------------------

def test_a_chained_incident_always_gets_a_proposal():
    assert should_propose_fix(_chained_state(query="something's wrong with compute-02")) is True


def test_a_standalone_check_question_stays_a_pure_check_answer():
    state = _chained_state(query="how do I check if nova-compute is running", diagnosed_by=None)
    assert should_propose_fix(state) is False


def test_a_standalone_question_that_asks_for_a_fix_gets_one():
    state = _chained_state(query="how do I fix nova-compute being down", diagnosed_by=None)
    assert should_propose_fix(state) is True


def test_no_proposal_on_error_missing_result_fallback_tier_or_repeat():
    assert should_propose_fix({**_chained_state(), "error": "boom"}) is False
    assert should_propose_fix({**_chained_state(), "agent_result": None}) is False

    fallback = _chained_state()
    fallback["agent_result"]["raw_data"]["source"] = "official_docs"
    assert should_propose_fix(fallback) is False

    done = remediation_agent(_chained_state())
    assert should_propose_fix(done) is False  # idempotent if the edge is ever re-entered


# --------------------------------------------------------------------
# 2c. The node
# --------------------------------------------------------------------

def test_chained_node_appends_the_proposal_after_the_experts_walkthrough():
    state = _chained_state()
    expert_summary = state["agent_result"]["summary"]
    original_result = state["agent_result"]

    out = remediation_agent(state)

    summary = out["agent_result"]["summary"]
    assert summary.startswith(expert_summary)  # the expert's answer is untouched, not replaced
    assert "### Proposed fix: Host CPU usage flagged high/critical" in summary
    assert summary.index("What's usually done about it") < summary.index("Proposed fix")
    assert out["target_agent"] == "remediation"
    raw = out["agent_result"]["raw_data"]
    assert raw["fix_proposal"]["primary"]["risk"] == "medium"
    assert raw["matched_symptom_id"] == "host-cpu-pressure"  # expert fields survive for the existing panel
    assert out["agent_result"]["confidence"] == original_result["confidence"]
    assert "fix_proposal" not in original_result["raw_data"]  # input result not mutated in place


def test_a_bug_building_the_proposal_never_costs_the_turn_its_diagnosis(monkeypatch):
    def boom(raw):
        raise RuntimeError("bug in the proposal builder")

    monkeypatch.setattr(remediation, "build_fix_proposal", boom)
    state = _chained_state()
    expert_summary = state["agent_result"]["summary"]

    out = remediation_agent(state)

    assert out["agent_result"]["summary"] == expert_summary
    assert out["target_agent"] == "openstack_expert"
    assert out.get("error") is None


def test_direct_route_runs_the_catalog_match_itself_then_proposes():
    state = {
        "user_query": "propose a fix for the high CPU on compute-02",
        "known_nodes": KNOWN_NODES, "target_agent": "remediation", "error": None, "failures": [],
    }
    out = remediation_agent(state)

    raw = out["agent_result"]["raw_data"]
    assert out["target_agent"] == "remediation"
    assert raw["matched_symptom_id"] == "host-cpu-pressure"
    assert raw["fix_proposal"]["host"] == "compute-02"
    assert "compute-02 nova-compute" in raw["fix_proposal"]["primary"]["command"]


def test_direct_route_with_no_catalog_match_says_so_instead_of_inventing_a_command():
    state = {
        "user_query": "fix the flux capacitor on compute-02",
        "known_nodes": KNOWN_NODES, "target_agent": "remediation", "error": None, "failures": [],
    }
    out = remediation_agent(state)

    summary = out["agent_result"]["summary"]
    assert "No vetted fix on file" in summary
    assert "fix_proposal" not in out["agent_result"]["raw_data"]
    assert out["target_agent"] == "remediation"


def test_direct_route_hands_back_a_clean_error_turn_untouched():
    state = {"user_query": "x", "known_nodes": [], "target_agent": "remediation",
             "error": "something upstream", "failures": [], "agent_result": None}
    assert remediation_agent(state)["error"] == "something upstream"


# --------------------------------------------------------------------
# 2d. Registration
# --------------------------------------------------------------------

def test_router_can_route_to_remediation():
    assert "remediation" in intent_router.AgentName.__args__
    assert "remediation:" in intent_router._SYSTEM_PROMPT


def test_remediation_is_registered_as_a_no_llm_agent_for_the_cost_rollup():
    assert llm_client.AGENT_TIERS["remediation"].startswith("n/a")


# --------------------------------------------------------------------
# 3. The demo, through the compiled graph
# --------------------------------------------------------------------

def _route_to(monkeypatch, agent, confidence=0.9):
    classification = SimpleNamespace(agent=agent, confidence=confidence)

    class _FakeStructured:
        def invoke(self, messages):
            return classification

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FakeStructured()

    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM())


def _invoke(query):
    return app_graph.invoke({"user_query": query, "known_nodes": KNOWN_NODES, "failures": []})


def test_demo_known_issue_in_chat_produces_the_exact_fix_text(monkeypatch):
    """Roadmap 4.1 demo: trigger a known issue -> agent proposes exact fix
    text. Real graph; the only fake is the metrics source, which reports a
    critically hot compute-02."""
    monkeypatch.setattr(
        monitoring, "collect_metrics",
        lambda: [{
            "instance": NODE["instance"], "node": NODE["hostname"], "role": NODE["role"],
            "cpu_percent": 96, "memory_percent": 40, "disk_percent": 30,
            "status": "up", "health": "critical",
        }],
    )

    result = _invoke("how is compute-02 doing")

    answer = result["final_answer"]
    assert result["target_agent"] == "remediation"
    assert "### Proposed fix: Host CPU usage flagged high/critical" in answer
    assert "Proposal only -- nothing has been run." in answer
    assert (
        'openstack compute service set --disable --disable-reason "investigating CPU pressure" '
        "compute-02 nova-compute"
    ) in answer
    assert "openstack compute service set --enable compute-02 nova-compute" in answer  # the undo
    assert result["critic_verdict"]["status"] == "pass"  # nothing in the proposal is ungrounded

    steps = [e["node"] for e in result["trace_events"]]
    assert steps.index("openstack_expert") < steps.index("remediation") < steps.index("critic")
    event = next(e for e in result["trace_events"] if e["node"] == "remediation")
    assert event["detail"]["chained_from"] == "openstack_expert"
    assert event["detail"]["proposal"]["risk"] == "medium"
    assert event["detail"]["proposal"]["requires_approval"] is True


def test_demo_asking_for_a_fix_directly_routes_straight_to_the_remediation_agent(monkeypatch):
    _route_to(monkeypatch, "remediation")

    result = _invoke("propose a fix for nova-compute being down on compute-02")

    assert result["target_agent"] == "remediation"
    assert "docker restart nova_compute" in result["final_answer"]
    assert "Run it on the host itself (`compute-02`)" in result["final_answer"]
    assert result["agent_result"]["raw_data"]["fix_proposal"]["host"] == "compute-02"
    assert result["critic_verdict"]["status"] == "pass"


def test_a_plain_how_do_i_check_question_does_not_grow_a_fix_proposal(monkeypatch):
    _route_to(monkeypatch, "openstack_expert")

    result = _invoke("how do I check if nova-compute is running")

    assert result["target_agent"] == "openstack_expert"
    assert "Proposed fix" not in result["final_answer"]
    assert "fix_proposal" not in result["agent_result"]["raw_data"]


def test_a_standalone_expert_question_that_asks_for_a_fix_gets_the_proposal_too(monkeypatch):
    _route_to(monkeypatch, "openstack_expert")

    result = _invoke("how do I fix nova-compute being down on compute-02")

    assert result["target_agent"] == "remediation"
    assert "### Proposed fix" in result["final_answer"]
