"""Tests for v0.8's dynamic multi-node fan-out (agents/nodes/anomaly.py's
anomaly_dispatch / anomaly_investigate_one / anomaly_arbitrate, wired via
LangGraph's `Send` in graph.py).

Same monkeypatch-at-the-call-site convention as test_anomaly_agent.py /
test_graph_integration.py: no real Postgres/Loki/Prometheus/NVIDIA API.
"""
from types import SimpleNamespace

import app.agents.intent_router as intent_router
import app.agents.nodes.anomaly as anomaly
from app.agents.graph import app_graph
from app.services import loki_client

NODE_A = {"hostname": "compute-01", "role": "compute", "instance": "10.0.1.11:9100"}
NODE_B = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
NODE_C = {"hostname": "storage-09", "role": "storage", "instance": "10.0.2.9:9100"}
KNOWN_NODES = [NODE_A, NODE_B, NODE_C]


def _flag(hostname, metric_name="network_errors", severity="critical", z_score=4.2, current_value=97.3):
    from datetime import datetime
    return SimpleNamespace(
        hostname=hostname,
        metric_name=metric_name,
        severity=severity,
        z_score=z_score,
        current_value=current_value,
        method="robust_zscore",
        detected_at=datetime(2026, 8, 25, 12, 0, 0),
    )


def _route_to(monkeypatch, agent: str, confidence: float = 0.9):
    classification = SimpleNamespace(agent=agent, confidence=confidence)

    class _FakeStructured:
        def invoke(self, messages):
            return classification

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FakeStructured()

    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM())


# --------------------------------------------------------------------
# anomaly_dispatch: scope resolution
# --------------------------------------------------------------------

def test_dispatch_scopes_to_single_node_when_one_is_named():
    state = {
        "user_query": "something's wrong with compute-02",
        "known_nodes": KNOWN_NODES,
    }
    result = anomaly.anomaly_dispatch(state)

    assert result["error"] is None
    assert [n["hostname"] for n in result["incident_scope"]] == ["compute-02"]


def test_dispatch_scopes_to_living_model_flagged_nodes_for_a_broad_question(monkeypatch):
    monkeypatch.setattr(
        anomaly.crud,
        "list_all_open_anomaly_flag_hostnames",
        lambda db: ["compute-02", "storage-09"],
    )

    state = {
        "user_query": "is anything wrong right now?",
        "known_nodes": KNOWN_NODES,
    }
    result = anomaly.anomaly_dispatch(state)

    assert result["error"] is None
    assert [n["hostname"] for n in result["incident_scope"]] == ["compute-02", "storage-09"]


def test_dispatch_ignores_flagged_hosts_no_longer_in_the_living_model(monkeypatch):
    # A hostname the anomaly detector still has open flags for, but that's
    # since been decommissioned/removed from topology -- shouldn't be
    # investigated as if it were still a real, resolvable node.
    monkeypatch.setattr(
        anomaly.crud,
        "list_all_open_anomaly_flag_hostnames",
        lambda db: ["compute-02", "decommissioned-node"],
    )

    state = {"user_query": "is anything wrong right now?", "known_nodes": KNOWN_NODES}
    result = anomaly.anomaly_dispatch(state)

    assert [n["hostname"] for n in result["incident_scope"]] == ["compute-02"]


def test_dispatch_errors_when_broad_question_has_no_flagged_nodes(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_all_open_anomaly_flag_hostnames", lambda db: [])

    state = {"user_query": "is anything wrong right now?", "known_nodes": KNOWN_NODES}
    result = anomaly.anomaly_dispatch(state)

    assert result["incident_scope"] == []
    assert result["agent_result"] is None
    assert "couldn't tell which node" in result["error"].lower()


# --------------------------------------------------------------------
# Full graph: multi-node fan-out produces one arbitrated agent_result
# --------------------------------------------------------------------

def test_multi_node_incident_fans_out_and_arbitrates_worst_first(monkeypatch):
    _route_to(monkeypatch, "anomaly")
    monkeypatch.setattr(
        anomaly.crud, "list_all_open_anomaly_flag_hostnames", lambda db: ["compute-01", "compute-02"]
    )

    def fake_flags(db, hostname):
        if hostname == "compute-01":
            return [_flag("compute-01", severity="medium", z_score=2.0)]
        if hostname == "compute-02":
            return [_flag("compute-02", severity="critical", z_score=5.0)]
        return []

    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", fake_flags)
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: [])

    result = app_graph.invoke({
        "user_query": "is anything wrong right now?",
        "known_nodes": KNOWN_NODES,
        "failures": [],
        "agent_results": [],
    })

    agent_result = result["agent_result"]
    raw = agent_result["raw_data"]

    # The worse (critical, compute-02) finding is primary...
    assert raw["hostname"] == "compute-02"
    # ...and both findings are visible in the breakdown, exactly two --
    # NOT four or more, which is what a reducer double-add bug would
    # produce (see resilience.guarded_send / anomaly_arbitrate docstrings).
    assert len(raw["multi_node_findings"]) == 2
    hostnames = sorted(f["hostname"] for f in raw["multi_node_findings"])
    assert hostnames == ["compute-01", "compute-02"]

    # The reducer must dedupe correctly -- exactly 2 findings, not 4+,
    # which is what a naive concatenating reducer would produce once this
    # value gets re-seen and re-returned by critic/compose downstream
    # (see state.py's `_concat` docstring).
    assert len(result["agent_results"]) == 2


def test_single_named_node_incident_still_produces_the_old_single_node_shape(monkeypatch):
    # Backward-compat guard: a normal "something's wrong with X" question
    # (exactly one node resolved) should NOT grow a multi_node_findings
    # key at all -- same raw_data shape openstack_expert's chaining logic
    # (_evidence_from_anomaly) has always read.
    _route_to(monkeypatch, "anomaly")
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag(hostname)])
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: [])

    result = app_graph.invoke({
        "user_query": "something's wrong with compute-02",
        "known_nodes": KNOWN_NODES,
        "failures": [],
        "agent_results": [],
    })

    raw = result["agent_result"]["raw_data"]
    assert raw["hostname"] == "compute-02"
    assert "multi_node_findings" not in raw


def test_a_failed_node_in_the_fanout_degrades_that_node_not_the_whole_turn(monkeypatch):
    _route_to(monkeypatch, "anomaly")
    monkeypatch.setattr(
        anomaly.crud, "list_all_open_anomaly_flag_hostnames", lambda db: ["compute-01", "compute-02"]
    )
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag(hostname)])

    def flaky_loki(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(loki_client, "query_range", flaky_loki)

    result = app_graph.invoke({
        "user_query": "is anything wrong right now?",
        "known_nodes": KNOWN_NODES,
        "failures": [],
        "agent_results": [],
    })

    # Both nodes still produced a finding (degraded, not dropped).
    assert len(result["agent_result"]["raw_data"]["multi_node_findings"]) == 2
    assert result["failures"], "Loki failures from the fan-out should still surface"
