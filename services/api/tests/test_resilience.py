"""Tests for app/agents/resilience.py -- the v0.5 circuit-breaker layer
every agent call (and select sub-calls, e.g. anomaly.py's Loki query) now
goes through. See adr-0007.

conftest.py's autouse _reset_circuit_breakers fixture clears the
module-level registry before/after every test here, since these tests
deliberately trip failure_threshold/open a circuit -- without that reset,
one test's induced failures would leak into the next.
"""
import time

from app.agents.resilience import get_breaker, guarded_node


def _boom(*a, **k):
    raise ValueError("dependency is down")


def _slow(seconds):
    time.sleep(seconds)
    return "too late"


# --------------------------------------------------------------------
# Basic call() behavior
# --------------------------------------------------------------------

def test_successful_call_returns_ok_with_value():
    breaker = get_breaker("test.success")
    result = breaker.call(lambda: 42)

    assert result.ok is True
    assert result.value == 42
    assert result.failure is None


def test_call_retries_once_and_succeeds_on_second_attempt():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("first attempt fails")
        return "ok"

    breaker = get_breaker("test.retry_then_succeed", max_retries=1)
    result = breaker.call(flaky)

    assert result.ok is True
    assert result.value == "ok"
    assert len(attempts) == 2  # one retry happened, not more


def test_call_fails_after_exhausting_retries_and_returns_failure_record():
    breaker = get_breaker("test.always_fails", max_retries=1, failure_threshold=99)
    result = breaker.call(_boom)

    assert result.ok is False
    assert result.value is None
    assert result.failure["source"] == "test.always_fails"
    assert result.failure["error_type"] == "ValueError"
    assert result.failure["attempts"] == 2  # initial attempt + one retry
    assert "timestamp" in result.failure


def test_call_enforces_timeout_budget_and_reports_it_as_timeout():
    breaker = get_breaker("test.timeout", timeout_seconds=0.2, max_retries=0, failure_threshold=99)
    result = breaker.call(_slow, 5)

    assert result.ok is False
    assert result.failure["error_type"] == "timeout"
    assert "0.2" in result.failure["message"]


# --------------------------------------------------------------------
# Circuit state: closed -> open -> half_open -> closed
# --------------------------------------------------------------------

def test_circuit_opens_after_consecutive_failures_and_short_circuits():
    breaker = get_breaker("test.opens", max_retries=0, failure_threshold=2, reset_after_seconds=999)

    first = breaker.call(_boom)
    assert first.ok is False
    assert breaker.state == "closed"  # one failure isn't enough yet

    second = breaker.call(_boom)
    assert second.ok is False
    assert breaker.state == "open"  # threshold hit

    calls_made = []
    third = breaker.call(lambda: calls_made.append(1))
    assert third.ok is False
    assert third.failure["error_type"] == "circuit_open"
    assert calls_made == []  # the real function was never even attempted


def test_circuit_half_opens_after_cooldown_and_closes_on_success():
    breaker = get_breaker("test.recovers", max_retries=0, failure_threshold=1, reset_after_seconds=0.05)

    opened = breaker.call(_boom)
    assert opened.ok is False
    assert breaker.state == "open"

    time.sleep(0.1)  # past reset_after_seconds

    recovered = breaker.call(lambda: "back up")
    assert recovered.ok is True
    assert recovered.value == "back up"
    assert breaker.state == "closed"


def test_get_breaker_returns_the_same_instance_for_the_same_name():
    a = get_breaker("test.identity")
    b = get_breaker("test.identity")
    assert a is b


# --------------------------------------------------------------------
# guarded_node: node-level wrapping used in graph.py
# --------------------------------------------------------------------

def test_guarded_node_passes_through_state_on_success():
    def node(state):
        state["agent_result"] = {"summary": "ok", "confidence": 1.0, "raw_data": {}}
        return state

    wrapped = guarded_node("test_node")(node)
    result = wrapped({"user_query": "hi"})

    assert result["agent_result"]["summary"] == "ok"
    assert "failures" not in result or result["failures"] == []


def test_guarded_node_degrades_instead_of_raising_when_node_crashes():
    def node(state):
        raise RuntimeError("unexpected bug")

    wrapped = guarded_node("test_crashing_node")(node)
    result = wrapped({"user_query": "hi", "failures": []})

    assert result["agent_result"] is None
    assert "didn't respond in time" in result["error"] or "RuntimeError" in result["error"]
    assert len(result["failures"]) == 1
    assert result["failures"][0]["source"] == "test_crashing_node"


def test_guarded_node_degrades_instead_of_hanging_on_timeout():
    def node(state):
        time.sleep(5)
        return state  # never reached within the budget

    wrapped = guarded_node("test_hanging_node", timeout_seconds=0.2)(node)
    started = time.monotonic()
    result = wrapped({"user_query": "hi", "failures": []})
    elapsed = time.monotonic() - started

    # The whole point: this returns promptly instead of blocking ~5s.
    assert elapsed < 2.0
    assert result["agent_result"] is None
    assert result["failures"][0]["error_type"] == "timeout"


def test_guarded_node_does_not_mutate_caller_state_on_timeout():
    """A timed-out call's thread isn't killed -- it may still be running
    against whatever it was given. guarded_node passes a *copy* of state
    into the wrapped call so a straggler thread can never write into the
    live state this function returns."""
    marker = {}

    def node(state):
        time.sleep(0.3)
        state["late_write"] = "should not appear on the live dict"
        marker["ran_to_completion"] = True
        return state

    wrapped = guarded_node("test_straggler_node", timeout_seconds=0.05)(node)
    live_state = {"user_query": "hi", "failures": []}
    result = wrapped(live_state)

    assert result["agent_result"] is None
    assert "late_write" not in live_state

    # Give the abandoned thread time to actually finish in the background,
    # then confirm it still never touched the caller's live dict.
    time.sleep(0.5)
    assert "late_write" not in live_state
    assert marker.get("ran_to_completion") is True
