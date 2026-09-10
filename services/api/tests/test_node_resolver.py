"""Tests for app/agents/node_resolver.py -- no dedicated suite existed
before this (only exercised indirectly via test_network_agent.py /
test_graph_integration.py's full-graph fixtures), so this covers the
tiered matching directly: literal, the new stem tier, and the existing
session-memory fallback. The LLM tier itself isn't covered here (no
NVIDIA_API_KEY in this test environment, same as every other agent test
module -- see llm_client.py); that path is exercised indirectly wherever
a fixture needs a partial/misspelled name the deterministic tiers below
can't resolve on their own.
"""
from app.agents.node_resolver import resolve_node

COMPUTE2_SIM = {"hostname": "compute2-sim", "role": "compute", "instance": "10.0.1.22:9100"}
COMPUTE1_SIM = {"hostname": "compute1-sim", "role": "compute", "instance": "10.0.1.21:9100"}
CONTROLLER_01 = {"hostname": "controller-01", "role": "controller", "instance": "10.0.1.10:9100"}
KNOWN_NODES = [COMPUTE1_SIM, COMPUTE2_SIM, CONTROLLER_01]


def test_literal_full_hostname_resolves():
    assert resolve_node("how is the network on compute2-sim", KNOWN_NODES) == COMPUTE2_SIM


def test_literal_hostname_with_space_instead_of_hyphen_resolves():
    assert resolve_node("cpu on compute2 sim please", KNOWN_NODES) == COMPUTE2_SIM


def test_dropped_suffix_stem_resolves_without_any_llm(monkeypatch):
    """The exact scenario this tier exists for: "compute2" with no "-sim"
    at all. Blows up the LLM tier if this ever falls through to it, so a
    regression here is loud rather than silently masked by the LLM."""
    import app.agents.node_resolver as node_resolver

    def _boom(*a, **k):
        raise AssertionError("should resolve in the free literal/stem tier, never reach the LLM")

    monkeypatch.setattr(node_resolver, "_llm_match", _boom)

    assert resolve_node("how is the network on compute2", KNOWN_NODES) == COMPUTE2_SIM


def test_dropped_suffix_stem_resolves_for_multi_segment_hostname(monkeypatch):
    import app.agents.node_resolver as node_resolver

    monkeypatch.setattr(node_resolver, "_llm_match", lambda *a, **k: None)
    assert resolve_node("is controller up", KNOWN_NODES) == CONTROLLER_01


def test_bare_role_prefix_shared_by_two_nodes_is_ambiguous_not_a_stem_guess(monkeypatch):
    """"compute" alone matches the leading segment of both compute1-sim
    and compute2-sim -- that's genuinely ambiguous, not a dropped suffix,
    so the stem tier must not silently pick one. (Falls through to the
    LLM tier here, which is monkeypatched to "no confident match" since
    there's genuinely nothing in the question to disambiguate with.)"""
    import app.agents.node_resolver as node_resolver

    monkeypatch.setattr(node_resolver, "_llm_match", lambda *a, **k: None)
    assert resolve_node("how's compute doing", KNOWN_NODES) is None


def test_two_literal_hostnames_both_present_is_ambiguous(monkeypatch):
    import app.agents.node_resolver as node_resolver

    monkeypatch.setattr(node_resolver, "_llm_match", lambda *a, **k: None)
    assert resolve_node("compare compute1-sim and compute2-sim", KNOWN_NODES) is None


def test_single_known_node_resolves_even_with_no_hostname_mentioned():
    assert resolve_node("how's the network doing", [COMPUTE2_SIM]) == COMPUTE2_SIM


def test_no_match_falls_back_to_session_memory(monkeypatch):
    import app.agents.node_resolver as node_resolver

    monkeypatch.setattr(node_resolver, "_llm_match", lambda *a, **k: None)
    session_memory = {"last_node": COMPUTE2_SIM}
    assert resolve_node("what about now", KNOWN_NODES, session_memory=session_memory) == COMPUTE2_SIM


def test_session_memory_ignored_when_node_no_longer_known(monkeypatch):
    import app.agents.node_resolver as node_resolver

    monkeypatch.setattr(node_resolver, "_llm_match", lambda *a, **k: None)
    decommissioned = {"hostname": "old-node", "role": "compute", "instance": "10.0.1.99:9100"}
    session_memory = {"last_node": decommissioned}
    assert resolve_node("what about now", KNOWN_NODES, session_memory=session_memory) is None
