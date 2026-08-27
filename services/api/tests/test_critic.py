"""Tests for app/agents/nodes/critic.py -- v0.7 (adr-0009).

DoD: "Critic node catches at least one deliberately-injected unsupported
claim in a test run" -- see
test_flags_a_deliberately_injected_unsupported_numeric_claim and
test_flags_a_deliberately_injected_ungrounded_rag_sentence below, which
are exactly that: a clean, would-pass agent_result with one fabricated
sentence spliced in, asserting the critic catches it.
"""
from app.agents.nodes import critic


def _state(summary, raw_data, user_query="what is going on", error=None):
    return {
        "user_query": user_query,
        "error": error,
        "agent_result": {"summary": summary, "confidence": 1.0, "raw_data": raw_data} if not error else None,
    }


# --------------------------------------------------------------------
# Nothing to check
# --------------------------------------------------------------------

def test_error_turn_passes_without_checking_anything():
    state = _state(summary="unused", raw_data={}, error="I couldn't tell which node you meant.")
    result = critic.critic_check(state)
    assert result["critic_verdict"] == {"status": "pass", "checked_sentences": 0, "flagged_claims": []}


def test_missing_agent_result_passes_without_checking_anything():
    state = {"user_query": "hi", "error": None, "agent_result": None}
    result = critic.critic_check(state)
    assert result["critic_verdict"]["status"] == "pass"


# --------------------------------------------------------------------
# Numeric grounding: monitoring/anomaly-style narration over real numbers
# --------------------------------------------------------------------

def test_clean_numeric_narration_passes():
    raw_data = {"cpu_percent": 61.2, "memory_percent": 40.0, "disk_percent": 30.0, "status": "up"}
    summary = "compute-02 is at 61.2% CPU and 40.0% memory, and looks healthy."
    result = critic.critic_check(_state(summary, raw_data))
    assert result["critic_verdict"]["status"] == "pass"


def test_flags_a_deliberately_injected_unsupported_numeric_claim():
    """The clean case above, plus one fabricated sentence citing a CPU
    figure nowhere in raw_data or the question -- the exact "injected
    unsupported claim" DoD scenario."""
    raw_data = {"cpu_percent": 61.2, "memory_percent": 40.0, "disk_percent": 30.0, "status": "up"}
    clean = "compute-02 is at 61.2% CPU and 40.0% memory, and looks healthy."
    injected = "It briefly spiked to 94.7% CPU five minutes ago."
    summary = f"{clean} {injected}"

    result = critic.critic_check(_state(summary, raw_data))

    assert result["critic_verdict"]["status"] == "flagged"
    assert result["critic_verdict"]["flagged_claims"] == [injected]


def test_number_within_rounding_tolerance_is_not_flagged():
    raw_data = {"cpu_percent": 89.6}
    summary = "CPU is running about 90% right now."
    result = critic.critic_check(_state(summary, raw_data))
    assert result["critic_verdict"]["status"] == "pass"


def test_number_present_in_the_users_own_question_is_not_flagged():
    raw_data = {"cpu_percent": 10.0}
    summary = "You asked if it's above 90%, and no, it's nowhere close."
    result = critic.critic_check(_state(summary, raw_data, user_query="is it above 90% CPU?"))
    assert result["critic_verdict"]["status"] == "pass"


def test_no_numeric_evidence_at_all_skips_the_numeric_check():
    # rag-style raw_data has no numbers in it -- nothing for the numeric
    # check to compare against, so it should not fire false positives.
    summary = "This has been reported 12 times in the last quarter."
    result = critic.critic_check(_state(summary, raw_data={"sources": []}))
    assert result["critic_verdict"]["status"] == "pass"


# --------------------------------------------------------------------
# Lexical grounding: rag-style free-text narration over retrieved chunks
# --------------------------------------------------------------------

def _rag_raw_data():
    return {
        "sources": [
            {
                "source_path": "docs/knowledge/service-detail/nova.md",
                "doc_title": "Nova Compute Service",
                "score": 0.91,
                "text_snippet": (
                    "The nova-compute service runs on every compute node and manages "
                    "instance lifecycle, communicating with nova-scheduler over RabbitMQ."
                ),
            }
        ]
    }


def test_clean_rag_summary_grounded_in_retrieved_chunk_passes():
    summary = "The nova-compute service manages instance lifecycle on each compute node."
    result = critic.critic_check(_state(summary, _rag_raw_data(), user_query="what does nova-compute do"))
    assert result["critic_verdict"]["status"] == "pass"


def test_flags_a_deliberately_injected_ungrounded_rag_sentence():
    clean = "The nova-compute service manages instance lifecycle on each compute node."
    injected = "It also automatically resizes customer invoices every billing cycle."
    summary = f"{clean} {injected}"

    result = critic.critic_check(_state(summary, _rag_raw_data(), user_query="what does nova-compute do"))

    assert result["critic_verdict"]["status"] == "flagged"
    assert injected in result["critic_verdict"]["flagged_claims"]


def test_no_chunks_retrieved_skips_the_lexical_check():
    # rag_agent's own "no context found" path (see nodes/rag.py) -- already
    # labeled low-confidence there; not this node's job to pile on.
    summary = "I couldn't find anything about that in the knowledge base."
    result = critic.critic_check(_state(summary, raw_data={"sources": []}))
    assert result["critic_verdict"]["status"] == "pass"


def test_short_sentence_is_not_judged_even_with_low_overlap():
    summary = "Yes, definitely."
    result = critic.critic_check(_state(summary, _rag_raw_data(), user_query="does it do that"))
    assert result["critic_verdict"]["status"] == "pass"


def test_checked_sentences_counts_all_sentences_in_the_summary():
    summary = "First sentence here. Second sentence here. Third one too."
    result = critic.critic_check(_state(summary, raw_data={}))
    assert result["critic_verdict"]["checked_sentences"] == 3
