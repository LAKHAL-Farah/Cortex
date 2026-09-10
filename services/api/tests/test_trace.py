"""v0.7 (adr-0009) tracing tests -- exercises the compiled graph
(app.agents.graph.app_graph) end to end, same convention as
test_graph_integration.py (external deps monkeypatched at their own call
site, a fake LLM installed via intent_router.get_chat_model to reach a
chosen branch deterministically).

What's being checked: that a real run through the graph leaves behind
exactly the ordered, per-node record agents/trace.py promises -- one
TraceEvent per node actually visited, in the order visited, each with a
status and a duration -- since that record (not this test) is what makes
"why did it say that" a lookup (routers/agents.py persists it verbatim as
models.AgentTrace.steps).
"""
from types import SimpleNamespace

import app.agents.intent_router as intent_router
import app.agents.nodes.anomaly as anomaly
from app.agents.graph import app_graph
from app.agents.trace import new_trace_id, summarize
from app.services import loki_client

NODE = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
KNOWN_NODES = [NODE]


def _route_to(monkeypatch, agent: str, confidence: float = 0.9):
    classification = SimpleNamespace(agent=agent, confidence=confidence)

    class _FakeStructured:
        def invoke(self, messages):
            return classification

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FakeStructured()

    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM())


def _invoke(query: str):
    trace_id = new_trace_id()
    result = app_graph.invoke(
        {
            "user_query": query,
            "known_nodes": KNOWN_NODES,
            "failures": [],
            "trace_id": trace_id,
            "trace_events": [],
        }
    )
    return trace_id, result


def _flag(metric_name="cpu_usage", severity="critical", z_score=4.2, current_value=97.3):
    from datetime import datetime

    return SimpleNamespace(
        hostname=NODE["hostname"],
        metric_name=metric_name,
        severity=severity,
        z_score=z_score,
        current_value=current_value,
        method="robust_zscore",
        detected_at=datetime(2026, 8, 25, 12, 0, 0),
    )


def test_new_trace_id_is_unique_per_call():
    assert new_trace_id() != new_trace_id()


def test_trace_id_round_trips_through_the_graph():
    trace_id, result = _invoke("how is compute-02 doing")
    assert result["trace_id"] == trace_id


def test_router_and_compose_and_critic_all_record_a_trace_event():
    trace_id, result = _invoke("how is compute-02 doing")
    events = result["trace_events"]
    node_names = [e["node"] for e in events]

    assert node_names[0] == "router"
    assert node_names[-1] == "compose"
    assert "critic" in node_names
    # monitoring is DEFAULT_AGENT's fallback with no NVIDIA_API_KEY set in
    # this test environment (see test_graph_integration.py's module
    # docstring for why that's the deterministic no-fake-LLM outcome).
    assert "monitoring" in node_names


def test_events_are_in_actual_execution_order():
    trace_id, result = _invoke("how is compute-02 doing")
    node_names = [e["node"] for e in result["trace_events"]]
    assert node_names.index("router") < node_names.index("monitoring")
    assert node_names.index("monitoring") < node_names.index("critic")
    assert node_names.index("critic") < node_names.index("compose")


def test_every_event_has_a_nonnegative_duration_and_ok_status():
    trace_id, result = _invoke("how is compute-02 doing")
    for event in result["trace_events"]:
        assert event["status"] == "ok"
        assert event["duration_ms"] >= 0
        assert event["timestamp"]


def test_router_event_detail_captures_intent_and_target_agent():
    trace_id, result = _invoke("how is compute-02 doing")
    router_event = next(e for e in result["trace_events"] if e["node"] == "router")
    assert router_event["detail"]["target_agent"] == result["target_agent"]


def test_critic_event_detail_captures_the_verdict():
    trace_id, result = _invoke("how is compute-02 doing")
    critic_event = next(e for e in result["trace_events"] if e["node"] == "critic")
    assert critic_event["detail"]["critic_verdict"] == result["critic_verdict"]


def test_a_failed_dependency_still_records_an_ok_node_event(monkeypatch):
    """resilience.guarded_node's degraded-not-failed path (see
    nodes/anomaly.py's Loki handling) -- a sub-call failing doesn't make
    the wrapping node's own trace event "error"; it stays "ok" because the
    node still produced a usable, honestly-degraded agent_result."""
    _route_to(monkeypatch, "anomaly")
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag()])

    def _dead_connection(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(loki_client, "query_range", _dead_connection)

    trace_id, result = _invoke("something's wrong with compute-02")

    anomaly_event = next(e for e in result["trace_events"] if e["node"] == "anomaly")
    assert anomaly_event["status"] == "ok"


def test_summarize_reports_step_count_and_total_duration():
    trace_id, result = _invoke("how is compute-02 doing")
    rollup = summarize(result["trace_events"])
    assert rollup["step_count"] == len(result["trace_events"])
    assert rollup["total_duration_ms"] >= 0
    assert rollup["any_failed"] is False


def test_summarize_handles_no_events():
    assert summarize(None) == {"step_count": 0, "total_duration_ms": 0, "any_failed": False}


# --------------------------------------------------------------------
# v0.11 (agentic-ai-layer UI): summary + chained_from in trace detail
# --------------------------------------------------------------------

def test_agent_event_detail_carries_the_same_summary_the_answer_is_built_from(monkeypatch):
    """The trace's `summary` for an agent node is not a second, diverging
    description -- it's the exact AgentResult.summary compose.py/critic.py
    already consume at the point that node ran, just also surfaced
    per-step for the UI. (This flag is severe enough to chain into
    openstack_expert -- see should_trigger_after_anomaly -- so the
    *final* result["agent_result"] ends up being the expert agent's,
    not anomaly's; the anomaly node's own trace event is what still
    carries anomaly's own summary, which is the thing being checked
    here.)"""
    _route_to(monkeypatch, "anomaly")
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag()])

    trace_id, result = _invoke("something's wrong with compute-02")

    anomaly_event = next(e for e in result["trace_events"] if e["node"] == "anomaly")
    assert "compute-02" in anomaly_event["detail"]["summary"]
    assert "cpu" in anomaly_event["detail"]["summary"].lower()


def test_openstack_expert_event_records_which_upstream_agent_triggered_it(monkeypatch):
    """This is the exact "network said this, then it got handed to the
    expert agent" story the UI wants to render -- confirmed here at the
    trace level, not just by reading graph.py's routing table."""
    from app.agents.nodes import network
    from app.services import network_health

    _route_to(monkeypatch, "network")
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [{
        "instance": NODE["instance"], "node": NODE["hostname"], "role": NODE["role"],
        "network_rx_bytes": 1000.0, "network_tx_bytes": 500.0,
        "network_errors_per_sec": 0.0, "network_drops_per_sec": 0.0, "status": "up",
    }])
    down_agent = {
        "id": "a1", "binary": "neutron-openvswitch-agent", "agent_type": "Open vSwitch agent",
        "host": NODE["hostname"], "alive": False, "admin_state_up": True,
    }
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: {
            "hostname": NODE["hostname"], "agents": [down_agent],
            "routers": [], "networks": [], "floating_ips": [], "instances": [],
        },
    )

    trace_id, result = _invoke("is the network okay on compute-02")

    node_names = [e["node"] for e in result["trace_events"]]
    assert node_names.index("network") < node_names.index("openstack_expert")

    network_event = next(e for e in result["trace_events"] if e["node"] == "network")
    assert "chained_from" not in network_event["detail"]  # only openstack_expert reports this
    assert "compute-02" in network_event["detail"]["summary"]

    expert_event = next(e for e in result["trace_events"] if e["node"] == "openstack_expert")
    assert expert_event["detail"]["chained_from"] == "network"


def test_standalone_openstack_expert_question_has_no_chained_from(monkeypatch):
    """A direct "how do I fix a stuck Ceph OSD"-style question reaches
    openstack_expert straight from the router (graph.py's sixth
    conditional-edge target) -- nothing upstream "found" anything, so
    chained_from should stay None rather than pointing at an unrelated
    earlier node."""
    _route_to(monkeypatch, "openstack_expert")

    trace_id, result = _invoke("how do I fix a stuck Ceph OSD")

    expert_event = next(e for e in result["trace_events"] if e["node"] == "openstack_expert")
    assert expert_event["detail"]["chained_from"] is None
