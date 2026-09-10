"""Tests for app/agents/compose.py -- v0.5 (adr-0007) adds degraded-answer
handling: when state["failures"] is non-empty, the final answer gets an
honest note prefixed to it instead of silently presenting a thinner
finding as if nothing had gone wrong.
"""
from app.agents import compose


def _failure(source="anomaly.loki", error_type="timeout"):
    return {
        "source": source,
        "error_type": error_type,
        "message": "boom",
        "attempts": 2,
        "timestamp": "2026-08-26T00:00:00+00:00",
    }


# --------------------------------------------------------------------
# Existing contract: error / missing-result short-circuits (unchanged)
# --------------------------------------------------------------------

def test_error_state_takes_priority_over_everything_else():
    state = {
        "error": "I couldn't tell which node you meant.",
        "agent_result": {"summary": "should be ignored", "confidence": 1.0, "raw_data": {}},
        "failures": [_failure()],
    }
    result = compose.compose_answer(state)
    assert result["final_answer"] == "I couldn't tell which node you meant."


def test_missing_agent_result_without_error_gets_generic_message():
    state = {"error": None, "agent_result": None, "failures": []}
    result = compose.compose_answer(state)
    assert "something went wrong" in result["final_answer"].lower()


# --------------------------------------------------------------------
# Clean success: no failures -> answer passes through untouched
# --------------------------------------------------------------------

def test_clean_result_with_no_failures_is_unmodified():
    state = {
        "error": None,
        "agent_result": {"summary": "compute-02 is healthy.", "confidence": 1.0, "raw_data": {}},
        "failures": [],
    }
    result = compose.compose_answer(state)
    assert result["final_answer"] == "compute-02 is healthy."


def test_missing_failures_key_is_treated_as_no_failures():
    # Older/partial states (or a node that never touches "failures") should
    # not crash compose -- .get("failures") or [] handles a missing key.
    state = {"error": None, "agent_result": {"summary": "fine.", "confidence": 1.0, "raw_data": {}}}
    result = compose.compose_answer(state)
    assert result["final_answer"] == "fine."


# --------------------------------------------------------------------
# Degraded: failures present -> honest note prefixed, original text kept
# --------------------------------------------------------------------

def test_degraded_answer_gets_a_prefixed_note_and_keeps_original_text():
    state = {
        "error": None,
        "agent_result": {
            "summary": "compute-02's CPU is flagged critical.",
            "confidence": 0.55,
            "raw_data": {},
        },
        "failures": [_failure(source="anomaly.loki")],
    }
    result = compose.compose_answer(state)

    assert "compute-02's CPU is flagged critical." in result["final_answer"]
    assert "log-check" in result["final_answer"].lower()
    assert "reduced confidence" in result["final_answer"].lower()
    # Note comes first, original finding follows.
    assert result["final_answer"].index("log-check") < result["final_answer"].index("compute-02")


def test_degraded_note_handles_unlisted_breaker_names_generically():
    state = {
        "error": None,
        "agent_result": {"summary": "some finding.", "confidence": 0.5, "raw_data": {}},
        "failures": [_failure(source="prediction.forecast_model")],
    }
    result = compose.compose_answer(state)

    assert "prediction forecast model" in result["final_answer"].lower()


def test_degraded_note_lists_multiple_distinct_sources():
    state = {
        "error": None,
        "agent_result": {"summary": "some finding.", "confidence": 0.4, "raw_data": {}},
        "failures": [_failure(source="anomaly.loki"), _failure(source="monitoring")],
    }
    result = compose.compose_answer(state)

    note = result["final_answer"].split("\n\n")[0].lower()
    assert "log-check" in note
    assert "monitoring" in note
    assert " and " in note  # joined as a readable list, not just concatenated


def test_degraded_note_deduplicates_repeated_sources():
    state = {
        "error": None,
        "agent_result": {"summary": "some finding.", "confidence": 0.4, "raw_data": {}},
        "failures": [_failure(source="anomaly.loki"), _failure(source="anomaly.loki")],
    }
    result = compose.compose_answer(state)

    note = result["final_answer"].split("\n\n")[0]
    assert note.lower().count("log-check") == 1
