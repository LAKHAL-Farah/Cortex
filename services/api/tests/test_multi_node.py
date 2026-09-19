"""Multi-node questions (v1.1): resolve_nodes, the monitoring/prediction
fleet answers, the anomaly-dispatch scope, and the router's network/security
override. No LLM, Prometheus, DB or forecast model -- everything faked."""
import pytest

from app.agents import intent_router
from app.agents.node_resolver import MAX_MULTI_NODES, resolve_nodes
from app.agents.nodes import anomaly, critic, monitoring, openstack_expert, prediction


def _n(host, role, ip):
    return {"hostname": host, "role": role, "instance": f"{ip}:9100"}


CTRL = _n("controller-01", "controller", "10.0.0.10")
C1 = _n("compute1-sim", "compute", "10.0.0.21")
C2 = _n("compute2-sim", "compute", "10.0.0.22")
ST = _n("storage-09", "storage", "10.0.0.30")
KNOWN = [CTRL, C1, C2, ST]


def _names(nodes):
    return [n["hostname"] for n in nodes]


# ------------------------------------------------------------ resolve_nodes --

@pytest.mark.parametrize(
    "query, expected",
    [
        ("compare compute1-sim and compute2-sim", ["compute1-sim", "compute2-sim"]),
        ("compare compute1 and compute2", ["compute1-sim", "compute2-sim"]),  # dropped suffix, unambiguous
        ("status of controller-01 and storage 09", ["controller-01", "storage-09"]),  # spaced hostname
        ("how are all my compute nodes doing", ["compute1-sim", "compute2-sim"]),
        ("status of the storage and controller nodes", ["controller-01", "storage-09"]),
        ("is everything ok on all nodes", _names(KNOWN)),
        ("how is the cluster", _names(KNOWN)),
    ],
)
def test_resolve_nodes_finds_several(query, expected):
    assert _names(resolve_nodes(query, KNOWN)) == expected


@pytest.mark.parametrize(
    "query",
    ["cpu on compute2-sim", "how's compute doing", "the compute node", "what is nova-compute", "hello"],
)
def test_resolve_nodes_is_empty_for_a_single_or_no_node(query):
    assert resolve_nodes(query, KNOWN) == []


def test_resolve_nodes_include_all_false_leaves_fleet_questions_alone():
    assert resolve_nodes("check all nodes", KNOWN, include_all=False) == []
    assert _names(resolve_nodes("check compute1-sim and storage-09", KNOWN, include_all=False)) == [
        "compute1-sim", "storage-09",
    ]


def test_resolve_nodes_follow_up_uses_last_nodes_but_revalidates_them():
    memory = {"last_nodes": [C1, ST, {"hostname": "decommissioned-07"}]}
    assert _names(resolve_nodes("and what about them?", KNOWN, session_memory=memory)) == ["compute1-sim", "storage-09"]
    assert resolve_nodes("and what about them?", KNOWN) == []


def test_resolve_nodes_is_capped_and_needs_two_registered_nodes():
    many = [_n(f"n{i:02d}", "compute", f"10.0.1.{i}") for i in range(30)]
    assert len(resolve_nodes("all nodes", many)) == MAX_MULTI_NODES
    assert resolve_nodes("all nodes", [C1]) == []


# ----------------------------------------------------------------- monitoring --

def _live(host, cpu, mem, disk, status="up"):
    peak = max(cpu, mem, disk)
    return {
        "instance": next(n["instance"] for n in KNOWN if n["hostname"] == host),
        "node": host, "role": "x", "cpu_percent": cpu, "memory_percent": mem, "swap_percent": 0,
        "disk_percent": disk, "load1": 1.5, "uptime": "1j 2h", "status": status,
        "health": "critical" if peak > 90 else "warning" if peak > 70 else "healthy",
    }


@pytest.fixture
def no_llm(monkeypatch):
    def _raise(**kwargs):
        raise monitoring.LLMConfigError("no key")
    monkeypatch.setattr(monitoring, "get_chat_model", _raise)


def _monitor(monkeypatch, query, live):
    monkeypatch.setattr(monitoring, "collect_metrics", lambda: live)
    return monitoring.monitoring_agent({"user_query": query, "known_nodes": KNOWN, "resolved_entities": {}})


def test_monitoring_compares_named_nodes_worst_first(monkeypatch, no_llm):
    live = [_live("compute1-sim", 95, 50, 40), _live("compute2-sim", 10, 20, 30), _live("storage-09", 5, 5, 5)]
    state = _monitor(monkeypatch, "compare compute1-sim and compute2-sim", live)

    raw = state["agent_result"]["raw_data"]
    assert raw["scope"] == "multi"
    assert [r["node"] for r in raw["nodes"]] == ["compute1-sim", "compute2-sim"]  # critical first, storage not asked
    assert raw["concerning"] == ["compute1-sim"]
    assert raw["counts"]["total"] == 2
    assert raw["aggregates"]["cpu_percent"]["max_node"] == "compute1-sim"
    summary = state["agent_result"]["summary"]
    assert "| compute1-sim | compute | up · critical | 95%" in summary
    assert "Uneven compute load" in summary
    assert state["resolved_entities"]["last_nodes"] == [C1, C2]
    assert state["error"] is None


def test_monitoring_fleet_answer_passes_the_critic(monkeypatch, no_llm):
    live = [_live(n["hostname"], 95 if n is C1 else 20, 40, 30) for n in KNOWN]
    state = _monitor(monkeypatch, "how are all nodes", live)
    state = critic.critic_check(state)
    assert state["critic_verdict"]["status"] == "pass", state["critic_verdict"]


def test_monitoring_reports_a_node_with_no_live_data(monkeypatch, no_llm):
    state = _monitor(monkeypatch, "compare compute1-sim and compute2-sim", [_live("compute1-sim", 10, 10, 10)])
    raw = state["agent_result"]["raw_data"]
    assert raw["missing"] == ["compute2-sim"]
    assert "No live data yet for: compute2-sim" in state["agent_result"]["summary"]


def test_monitoring_errors_when_none_of_the_nodes_are_scraped(monkeypatch, no_llm):
    state = _monitor(monkeypatch, "compare compute1-sim and compute2-sim", [])
    assert state["agent_result"] is None and "None of the 2 nodes" in state["error"]


def test_monitoring_single_node_path_is_unchanged(monkeypatch, no_llm):
    state = _monitor(monkeypatch, "cpu on compute2-sim", [_live("compute2-sim", 10, 20, 30)])
    assert "scope" not in state["agent_result"]["raw_data"]
    assert state["agent_result"]["raw_data"]["node"] == "compute2-sim"


def test_fleet_answer_does_not_chain_into_the_expert_agent(monkeypatch, no_llm):
    state = _monitor(monkeypatch, "how are all nodes", [_live(n["hostname"], 99, 99, 99) for n in KNOWN])
    assert openstack_expert.should_trigger_after_monitoring(state) is False


# ----------------------------------------------------------------- prediction --

def test_prediction_forecasts_several_nodes_and_flags_breaches(monkeypatch):
    def fake_forecast(ip, metric):
        if ip == "10.0.0.22":
            return None  # not enough data
        base = 50 if ip == "10.0.0.21" else 92
        return {"horizon_days": 7, "forecast": [{"predicted": base, "upper": base + 5}, {"predicted": base + 3, "upper": base + 9}]}

    monkeypatch.setattr(prediction, "get_forecast", fake_forecast)
    monkeypatch.setattr(prediction, "_resolve_metric", lambda q, default="cpu_percent": "cpu_percent")

    state = prediction.prediction_agent(
        {"user_query": "forecast cpu for all compute nodes and storage-09", "known_nodes": KNOWN, "resolved_entities": {}}
    )
    raw = state["agent_result"]["raw_data"]
    assert raw["scope"] == "multi" and raw["metric"] == "cpu_percent"
    assert raw["at_risk"] == ["storage-09"] and raw["missing"] == ["compute2-sim"]
    assert [r["hostname"] for r in raw["nodes"]] == ["storage-09", "compute1-sim"]
    assert "will cross 90%" in state["agent_result"]["summary"]
    assert critic.critic_check(state)["critic_verdict"]["status"] == "pass"


# -------------------------------------------------------------- anomaly scope --

def test_dispatch_scopes_to_every_named_node_instead_of_letting_one_win():
    state = {"user_query": "something's wrong with compute1-sim and storage-09", "known_nodes": KNOWN}
    result = anomaly.anomaly_dispatch(state)
    assert result["error"] is None
    assert _names(result["incident_scope"]) == ["compute1-sim", "storage-09"]


def test_dispatch_fleet_sweep_when_nothing_is_flagged(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_all_open_anomaly_flag_hostnames", lambda db: [])
    monkeypatch.setattr(anomaly.network_health, "list_hosts_with_down_agents", lambda: [])
    monkeypatch.setattr(anomaly.ebpf_signal, "list_hosts_with_alerts", lambda: [])
    result = anomaly.anomaly_dispatch({"user_query": "check all nodes", "known_nodes": KNOWN})
    assert result["error"] is None
    assert len(result["incident_scope"]) == min(len(KNOWN), anomaly._MAX_INCIDENT_FANOUT)


# --------------------------------------------------------------------- router --

class _Structured:
    def __init__(self, agent):
        self.agent = agent

    def invoke(self, messages):
        from types import SimpleNamespace
        return SimpleNamespace(agent=self.agent, confidence=0.95)


class _LLM:
    def __init__(self, agent):
        self._s = _Structured(agent)

    def with_structured_output(self, schema):
        return self._s


@pytest.mark.parametrize(
    "picked, query, expected",
    [
        ("security", "any suspicious logins on compute1-sim and compute2-sim", "anomaly"),
        ("network", "is the network ok on all compute nodes", "anomaly"),
        ("security", "any suspicious logins on compute1-sim", "security"),
        ("monitoring", "compare compute1-sim and compute2-sim", "monitoring"),
    ],
)
def test_router_sends_multi_node_network_and_security_questions_to_the_fan_out(monkeypatch, picked, query, expected):
    monkeypatch.setattr(intent_router, "get_chat_model", lambda **k: _LLM(picked))
    state = intent_router.route({"user_query": query, "known_nodes": KNOWN})
    assert state["target_agent"] == expected
