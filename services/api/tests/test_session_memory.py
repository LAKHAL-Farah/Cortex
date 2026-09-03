"""Tests for v0.8 session memory: agents/state.py's session_memory/
resolved_entities, node_resolver.py's third (session-memory) resolution
tier, intent_router.py's low-confidence-follow-up reuse of last_agent, and
crud.py's get_session_memory/upsert_session_memory persistence.

The definition-of-done scenario this is built for: a multi-turn
conversation where a bare follow-up like "what about now?" -- no hostname,
no clear intent on its own -- still resolves against what the previous
turn already established, instead of falling back to "I couldn't tell
which node you meant" or a clarifying question on every single follow-up.
"""
import uuid
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.agents.intent_router as intent_router
import app.agents.nodes.monitoring as monitoring
from app import crud, models
from app.agents.graph import app_graph

NODE_A = {"hostname": "compute-01", "role": "compute", "instance": "10.0.1.11:9100"}
NODE_B = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
KNOWN_NODES = [NODE_A, NODE_B]


def _route_to(monkeypatch, agent: str, confidence: float = 0.9):
    classification = SimpleNamespace(agent=agent, confidence=confidence)

    class _FakeStructured:
        def invoke(self, messages):
            return classification

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FakeStructured()

    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM())


def _live_metrics(instance, cpu=42.0, status="up", health="healthy"):
    return {
        "instance": instance,
        "cpu_percent": cpu,
        "memory_percent": 50.0,
        "disk_percent": 30.0,
        "status": status,
        "health": health,
    }


# --------------------------------------------------------------------
# node_resolver's session-memory fallback tier
# --------------------------------------------------------------------

def test_monitoring_resolves_a_bare_followup_against_session_memory(monkeypatch):
    monkeypatch.setattr(
        monitoring, "collect_metrics", lambda: [_live_metrics(NODE_B["instance"], cpu=91.0)]
    )

    # No hostname anywhere in this question -- only session memory says
    # which node "now" refers to.
    state = {
        "user_query": "what about now?",
        "known_nodes": KNOWN_NODES,
        "session_memory": {"last_node": NODE_B, "last_agent": "monitoring"},
    }
    result = monitoring.monitoring_agent(state)

    assert result["error"] is None
    assert result["agent_result"]["raw_data"]["instance"] == NODE_B["instance"]
    assert result["resolved_entities"]["last_node"]["hostname"] == "compute-02"


def test_session_memory_hostname_no_longer_in_living_model_is_not_trusted(monkeypatch):
    monkeypatch.setattr(monitoring, "collect_metrics", lambda: [_live_metrics(NODE_A["instance"])])

    # The remembered node has since been removed from topology -- known_nodes
    # (the current Living Model) no longer contains it.
    state = {
        "user_query": "what about now?",
        "known_nodes": [NODE_A],
        "session_memory": {"last_node": {"hostname": "decommissioned-01"}, "last_agent": "monitoring"},
    }
    result = monitoring.monitoring_agent(state)

    # Falls through to the single-known-node shortcut rather than trusting
    # a hostname that isn't in the current topology.
    assert result["agent_result"]["raw_data"]["instance"] == NODE_A["instance"]


def test_without_session_memory_a_bare_followup_still_cannot_resolve():
    # Backward-compat guard: no session memory at all (a fresh/stateless
    # call, e.g. no conversation_id) and more than one known node ->
    # still the honest "couldn't tell which node" error, not a guess.
    state = {"user_query": "what about now?", "known_nodes": KNOWN_NODES, "session_memory": {}}
    result = monitoring.monitoring_agent(state)

    assert result["agent_result"] is None
    assert "couldn't tell which node" in result["error"].lower()


# --------------------------------------------------------------------
# intent_router's low-confidence-follow-up reuse of last_agent
# --------------------------------------------------------------------

def test_low_confidence_followup_reuses_last_agent_from_session_memory(monkeypatch):
    _route_to(monkeypatch, "monitoring", confidence=0.4)  # below CLARIFY_THRESHOLD

    state = {
        "user_query": "what about now?",
        "session_memory": {"last_agent": "prediction"},
    }
    result = intent_router.route(state)

    assert result["target_agent"] == "prediction"
    assert result.get("error") is None


def test_low_confidence_first_turn_still_clarifies_without_session_memory(monkeypatch):
    _route_to(monkeypatch, "monitoring", confidence=0.4)

    state = {"user_query": "what about now?", "session_memory": {}}
    result = intent_router.route(state)

    assert result["target_agent"] == "clarify"
    assert result["error"]


# --------------------------------------------------------------------
# Full graph: a two-turn conversation where turn 2 is a bare follow-up
# --------------------------------------------------------------------

def test_full_graph_multi_turn_followup_resolves_against_session_memory(monkeypatch):
    _route_to(monkeypatch, "monitoring", confidence=0.9)
    monkeypatch.setattr(monitoring, "collect_metrics", lambda: [_live_metrics(NODE_B["instance"], cpu=77.0)])

    turn_1 = app_graph.invoke({
        "user_query": "how's compute-02 doing?",
        "known_nodes": KNOWN_NODES,
        "failures": [],
        "agent_results": [],
        "session_memory": {},
        "resolved_entities": {},
    })
    assert turn_1["agent_result"]["raw_data"]["instance"] == NODE_B["instance"]
    memory_after_turn_1 = turn_1["resolved_entities"]

    # Turn 2: a bare follow-up, low router confidence, no hostname at all --
    # simulates routers/agents.py loading back what turn 1 resolved.
    _route_to(monkeypatch, "monitoring", confidence=0.35)
    turn_2 = app_graph.invoke({
        "user_query": "what about now?",
        "known_nodes": KNOWN_NODES,
        "failures": [],
        "agent_results": [],
        "session_memory": memory_after_turn_1,
        "resolved_entities": {},
    })

    assert turn_2["target_agent"] == "monitoring"
    assert turn_2["error"] is None
    assert turn_2["agent_result"]["raw_data"]["instance"] == NODE_B["instance"]


# --------------------------------------------------------------------
# crud persistence
# --------------------------------------------------------------------

def _sqlite_session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(
        engine, tables=[models.Conversation.__table__, models.AgentSessionMemory.__table__, models.User.__table__]
    )
    return sessionmaker(bind=engine)()


def test_upsert_session_memory_merges_rather_than_replaces():
    db = _sqlite_session()
    user = models.User(id=uuid.uuid4(), username="u", password_hash="x", role="viewer")
    db.add(user)
    db.commit()
    conversation = models.Conversation(id=uuid.uuid4(), user_id=user.id, title="t")
    db.add(conversation)
    db.commit()

    crud.upsert_session_memory(db, conversation.id, {"last_node": {"hostname": "compute-01"}, "last_agent": "monitoring"})
    assert crud.get_session_memory(db, conversation.id) == {
        "last_node": {"hostname": "compute-01"},
        "last_agent": "monitoring",
    }

    # A later turn only resolves a metric (e.g. prediction_agent on the
    # same node) -- last_node/last_agent from the earlier turn should
    # survive the merge, not get wiped by a partial update.
    crud.upsert_session_memory(db, conversation.id, {"last_metric": "disk_percent", "last_agent": "prediction"})
    assert crud.get_session_memory(db, conversation.id) == {
        "last_node": {"hostname": "compute-01"},
        "last_metric": "disk_percent",
        "last_agent": "prediction",
    }


def test_upsert_session_memory_is_a_noop_for_an_empty_update():
    db = _sqlite_session()
    user = models.User(id=uuid.uuid4(), username="u2", password_hash="x", role="viewer")
    db.add(user)
    db.commit()
    conversation = models.Conversation(id=uuid.uuid4(), user_id=user.id, title="t")
    db.add(conversation)
    db.commit()

    crud.upsert_session_memory(db, conversation.id, {})
    assert crud.get_session_memory(db, conversation.id) == {}
