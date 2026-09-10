"""Tests for app/agents/intent_router.py -- v0.5 (adr-0007) adds a
confidence-based clarification gate and wraps the classification LLM call
in a circuit breaker.

Mirrors test_anomaly_agent.py's convention: fake the module's own call
sites (get_chat_model here) rather than hitting a real NIM endpoint, since
no NVIDIA_API_KEY is set in CI.
"""
from types import SimpleNamespace

import app.agents.intent_router as intent_router


class _FakeClassification(SimpleNamespace):
    pass


class _FakeStructuredModel:
    """Stands in for `llm.with_structured_output(_IntentClassification)`."""

    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeLLM:
    def __init__(self, structured):
        self._structured = structured

    def with_structured_output(self, schema):
        return self._structured


def _state(query="something's wrong with compute-02"):
    return {"user_query": query, "known_nodes": []}


# --------------------------------------------------------------------
# LLM unavailable / failing -> unchanged graceful fallback to DEFAULT_AGENT
# --------------------------------------------------------------------

def test_route_defaults_to_monitoring_when_llm_not_configured(monkeypatch):
    def _raise_config_error(**kwargs):
        raise intent_router.LLMConfigError("no key")

    monkeypatch.setattr(intent_router, "get_chat_model", _raise_config_error)

    result = intent_router.route(_state())

    assert result["target_agent"] == intent_router.DEFAULT_AGENT
    assert result["intent"] == intent_router.DEFAULT_AGENT
    # No clarification and no error text -- this is "degrade routing
    # quality", not "ask the user something".
    assert result.get("error") is None


def test_route_defaults_to_monitoring_when_llm_call_fails_post_retry(monkeypatch):
    structured = _FakeStructuredModel(exc=RuntimeError("NIM endpoint unreachable"))
    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM(structured))

    result = intent_router.route(_state())

    assert result["target_agent"] == intent_router.DEFAULT_AGENT
    assert result.get("error") is None
    # Breaker retried once before giving up (initial attempt + 1 retry).
    assert structured.calls == 2


# --------------------------------------------------------------------
# LLM works: confident classification runs normally
# --------------------------------------------------------------------

def test_route_picks_classified_agent_when_confidence_is_high(monkeypatch):
    structured = _FakeStructuredModel(result=_FakeClassification(agent="anomaly", confidence=0.92))
    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM(structured))

    result = intent_router.route(_state())

    assert result["target_agent"] == "anomaly"
    assert result["intent"] == "anomaly"
    assert result.get("error") is None


# --------------------------------------------------------------------
# LLM works but is honestly unsure -> clarification gate fires
# --------------------------------------------------------------------

def test_route_asks_for_clarification_when_confidence_is_low(monkeypatch):
    structured = _FakeStructuredModel(result=_FakeClassification(agent="monitoring", confidence=0.2))
    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM(structured))

    result = intent_router.route(_state(query="what about that thing"))

    assert result["target_agent"] == "clarify"
    assert result["intent"] == "clarify"
    assert result["agent_result"] is None
    assert result["error"]  # a clarifying question, not a guessed answer
    assert "clarify" in result["error"].lower() or "confident" in result["error"].lower()


def test_route_threshold_is_configurable(monkeypatch):
    # A confidence that would normally pass (0.6) should still trigger
    # clarification once the threshold is raised above it.
    monkeypatch.setattr(intent_router, "CLARIFY_THRESHOLD", 0.9)
    structured = _FakeStructuredModel(result=_FakeClassification(agent="prediction", confidence=0.6))
    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM(structured))

    result = intent_router.route(_state())

    assert result["target_agent"] == "clarify"


def test_route_does_not_clarify_right_at_the_threshold_boundary(monkeypatch):
    # confidence == threshold should pass (only strictly-below clarifies).
    monkeypatch.setattr(intent_router, "CLARIFY_THRESHOLD", 0.5)
    structured = _FakeStructuredModel(result=_FakeClassification(agent="rag", confidence=0.5))
    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM(structured))

    result = intent_router.route(_state())

    assert result["target_agent"] == "rag"
