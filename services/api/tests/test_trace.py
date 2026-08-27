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
