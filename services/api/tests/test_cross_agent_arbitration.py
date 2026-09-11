"""Tests for the v0.9 (Phase 5) cross-*agent* arbitration behavior in
app/agents/nodes/anomaly.py's `anomaly_arbitrate` -- the literal roadmap
DoD: "two synthetic correlated issues (one network-flavored, one
security-flavored) trigger real parallel investigation, and arbitration
picks the graph-supported theory over the first one to respond."

Deliberately calls `anomaly_arbitrate`/`_corroboration_bonus`/
`_finding_has_signal` directly against hand-built IncidentFinding lists
rather than driving the whole graph -- this is unit-level coverage of the
arbitration *rule itself*; test_graph_integration.py already covers the
same rule end-to-end through the full compiled graph.
"""
from app.agents.nodes.anomaly import (
    _corroboration_bonus,
    _finding_has_signal,
    anomaly_arbitrate,
)


def _finding(hostname, agent, confidence, has_signal, summary=None):
    return {
        "hostname": hostname,
        "agent": agent,
        "agent_result": {
            "summary": summary or f"{agent} summary for {hostname}",
            "confidence": confidence,
            "raw_data": {"hostname": hostname, "has_signal": has_signal},
        },
        "failures": [],
    }


def _state(findings, query="is anything wrong"):
    return {"user_query": query, "agent_results": findings, "failures": {}}


# --------------------------------------------------------------------
# has_signal always beats confidently-clean, regardless of raw confidence
# --------------------------------------------------------------------

def test_a_flagged_finding_beats_a_more_confident_clean_one():
    """The exact bug this suite exists to pin down: Network reading clean
    interface counters at confidence 1.0 must never outrank Anomaly's
    degraded-but-actually-flagged 0.6, since "confidently found nothing"
    and "found something, but one of my own sub-checks degraded" aren't
    even answering the same question."""
    findings = [
        _finding("compute-02", "anomaly", confidence=0.6, has_signal=True, summary="CPU flagged critical"),
        _finding("compute-02", "network", confidence=1.0, has_signal=False, summary="network clean"),
    ]
    result = anomaly_arbitrate(_state(findings))
    assert result["agent_result"]["raw_data"]["investigating_agent"] == "anomaly"
    assert "CPU flagged critical" in result["agent_result"]["summary"] or result["agent_result"]["confidence"] == 0.6


def test_two_agents_flagging_the_same_host_both_show_up_as_cross_agent_findings():
    findings = [
        _finding("compute-02", "network", confidence=0.9, has_signal=True, summary="down neutron agent"),
        _finding("compute-02", "security", confidence=0.95, has_signal=True, summary="active eBPF alert"),
    ]
    result = anomaly_arbitrate(_state(findings))
    cross = result["agent_result"]["raw_data"]["cross_agent_findings"]
    assert {f["agent"] for f in cross} == {"network", "security"}
    # Security's own eBPF-backed reading is the strongest -- should win as
    # the primary theory (highest confidence among the signal-bearing set).
    assert result["agent_result"]["raw_data"]["investigating_agent"] == "security"


# --------------------------------------------------------------------
# Corroboration: multiple independent agents agreeing outranks a lone
# finding that merely happened to respond with a nominally higher score
# --------------------------------------------------------------------

def test_corroborated_theory_outranks_a_lone_higher_raw_confidence_theory():
    """Two hosts: host A has only Anomaly flagging it (confidence 0.7);
    host B has both Network AND Security independently flagging it
    (confidence 0.65 each) -- corroboration should let host B's theory
    win the overall arbitration despite neither of its individual
    confidences being the highest raw number in the whole set."""
    findings = [
        _finding("compute-01", "anomaly", confidence=0.7, has_signal=True, summary="lone CPU blip"),
        _finding("compute-02", "network", confidence=0.65, has_signal=True, summary="down neutron agent"),
        _finding("compute-02", "security", confidence=0.65, has_signal=True, summary="matching eBPF alert"),
    ]
    result = anomaly_arbitrate(_state(findings))
    assert result["agent_result"]["raw_data"]["investigating_agent"] in ("network", "security")
    multi_node = result["agent_result"]["raw_data"]["multi_node_findings"]
    assert multi_node[0]["hostname"] == "compute-02"


def test_corroboration_bonus_is_zero_for_a_single_signal_and_capped_for_many():
    solo = [_finding("h", "anomaly", 0.7, True)]
    assert _corroboration_bonus(solo) == 0.0

    two_agents = [_finding("h", "anomaly", 0.7, True), _finding("h", "network", 0.6, True)]
    assert _corroboration_bonus(two_agents) == 0.05

    three_agents = [_finding("h", "anomaly", 0.7, True), _finding("h", "network", 0.6, True), _finding("h", "security", 0.6, True)]
    assert _corroboration_bonus(three_agents) == 0.1

    # A clean (no-signal) finding doesn't count toward corroboration --
    # "nothing wrong here" from a third agent isn't evidence for the
    # theory the other two are converging on.
    two_signal_one_clean = [
        _finding("h", "anomaly", 0.7, True),
        _finding("h", "network", 0.6, True),
        _finding("h", "security", 0.9, False),
    ]
    assert _corroboration_bonus(two_signal_one_clean) == 0.05


def test_corroboration_bonus_never_exceeds_the_reported_cap():
    many = [_finding("h", f"agent{i}", 0.5, True) for i in range(10)]
    assert _corroboration_bonus(many) == 0.15


def test_reported_confidence_is_always_the_winning_findings_own_number():
    """The corroboration bonus decides *which* finding wins; it must never
    leak into the confidence number actually reported for it."""
    findings = [
        _finding("compute-02", "network", confidence=0.6, has_signal=True),
        _finding("compute-02", "security", confidence=0.6, has_signal=True),
    ]
    result = anomaly_arbitrate(_state(findings))
    assert result["agent_result"]["confidence"] == 0.6  # not 0.65 (0.6 + 0.05 bonus)


# --------------------------------------------------------------------
# finding_has_signal / single-finding passthrough
# --------------------------------------------------------------------

def test_finding_has_signal_reads_the_uniform_raw_data_flag():
    assert _finding_has_signal(_finding("h", "anomaly", 0.5, True)) is True
    assert _finding_has_signal(_finding("h", "anomaly", 0.5, False)) is False


def test_single_finding_single_host_skips_the_narrative_llm_call_entirely(monkeypatch):
    """The v0.8 cost-conscious behavior must survive v0.9's changes: one
    host, one agent, no corroboration to report -- arbitration should
    just pass the finding's own summary through unchanged, not spend an
    LLM call synthesizing a "cross-agent narrative" for a set of exactly
    one."""
    import app.agents.nodes.anomaly as anomaly_module

    def _boom(*a, **k):
        raise AssertionError("should not call the narrative LLM for a single, uncorroborated finding")

    monkeypatch.setattr(anomaly_module, "_arbitrate_narrative", _boom)

    findings = [_finding("compute-02", "anomaly", confidence=0.8, has_signal=True, summary="CPU flagged critical")]
    result = anomaly_arbitrate(_state(findings))
    assert result["agent_result"]["summary"] == "CPU flagged critical"


def test_empty_findings_returns_state_unchanged():
    state = {"user_query": "is anything wrong", "agent_results": [], "error": "couldn't tell which node"}
    result = anomaly_arbitrate(state)
    assert result["error"] == "couldn't tell which node"
    assert "agent_result" not in result or result.get("agent_result") is None
