"""Tests for agents/network_resolver.py (v0.10, Phase C) -- the parallel
network/subnet/instance matcher node_resolver.py has no equivalent for.
Same style as the node_resolver-driven agent tests: no NVIDIA_API_KEY is
set, so the LLM tier always takes the deterministic LLMConfigError
fallback path unless a test explicitly monkeypatches
network_resolver.get_chat_model to exercise it.
"""
from app.agents import network_resolver
from app.agents.network_resolver import resolve_network_entity

NETWORK = {"kind": "network", "id": "net-1", "name": "sandbox-net", "cidr": None}
OTHER_NETWORK = {"kind": "network", "id": "net-2", "name": "sandbox-storage-net", "cidr": None}
SUBNET = {"kind": "subnet", "id": "sub-1", "name": "sandbox-subnet", "cidr": "10.0.0.0/24"}
INSTANCE = {"kind": "instance", "id": "i1", "name": "sandbox-vm-1", "cidr": None}


# --------------------------------------------------------------------
# No candidates / single candidate shortcuts
# --------------------------------------------------------------------

def test_no_known_entities_returns_none():
    assert resolve_network_entity("anything", []) is None


def test_single_known_entity_resolves_regardless_of_query_text():
    assert resolve_network_entity("what's going on", [NETWORK]) == NETWORK


# --------------------------------------------------------------------
# Exact/substring tier
# --------------------------------------------------------------------

def test_exact_match_on_network_name():
    entity = resolve_network_entity("which VMs are on sandbox-net", [NETWORK, OTHER_NETWORK, SUBNET, INSTANCE])
    assert entity == NETWORK


def test_exact_match_on_instance_name():
    entity = resolve_network_entity("why can't sandbox-vm-1 reach the internet", [NETWORK, SUBNET, INSTANCE])
    assert entity == INSTANCE


def test_exact_match_on_subnet_cidr():
    entity = resolve_network_entity("is anything down on 10.0.0.0/24", [NETWORK, SUBNET, INSTANCE])
    assert entity == SUBNET


def test_exact_match_on_entity_id():
    entity = resolve_network_entity(f"check {NETWORK['id']}", [NETWORK, OTHER_NETWORK])
    assert entity == NETWORK


def test_ambiguous_substring_falls_through_to_none_when_llm_unconfigured():
    # "sandbox" alone is a substring of every candidate's name -- not a
    # unique exact match, and with no NVIDIA_API_KEY set the LLM tier
    # can't disambiguate either.
    entity = resolve_network_entity("is sandbox okay", [NETWORK, OTHER_NETWORK, SUBNET, INSTANCE])
    assert entity is None


def test_short_names_are_not_trusted_by_the_exact_tier():
    short = {"kind": "network", "id": "net-9", "name": "et", "cidr": None}  # < _MIN_NAME_LEN
    entity = resolve_network_entity("is the network okay", [short, NETWORK])
    assert entity is None


# --------------------------------------------------------------------
# LLM tier
# --------------------------------------------------------------------

class _FakeStructuredLLM:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        return self._result


class _FakeChatModel:
    def __init__(self, result):
        self._result = result

    def with_structured_output(self, schema):
        return _FakeStructuredLLM(self._result)


def test_llm_tier_resolves_a_misspelled_or_partial_reference(monkeypatch):
    result = network_resolver._EntityResolution(kind="network", identifier="sandbox-net")
    monkeypatch.setattr(network_resolver, "get_chat_model", lambda temperature, tier: _FakeChatModel(result))

    entity = resolve_network_entity("the sandbox network", [NETWORK, OTHER_NETWORK])

    assert entity == NETWORK


def test_llm_tier_none_result_falls_through():
    # No NVIDIA_API_KEY in the test env -> LLMConfigError -> None, same
    # deterministic fallback every other LLM-touching test in this suite
    # relies on.
    entity = resolve_network_entity("is everything fine", [NETWORK, OTHER_NETWORK])
    assert entity is None


def test_llm_tier_ignores_a_hallucinated_identifier(monkeypatch):
    result = network_resolver._EntityResolution(kind="network", identifier="not-a-real-network")
    monkeypatch.setattr(network_resolver, "get_chat_model", lambda temperature, tier: _FakeChatModel(result))

    entity = resolve_network_entity("the sandbox network", [NETWORK, OTHER_NETWORK])

    assert entity is None


def test_llm_tier_kind_none_returns_none(monkeypatch):
    result = network_resolver._EntityResolution(kind="none", identifier=None)
    monkeypatch.setattr(network_resolver, "get_chat_model", lambda temperature, tier: _FakeChatModel(result))

    entity = resolve_network_entity("what's the weather", [NETWORK, OTHER_NETWORK])

    assert entity is None


# --------------------------------------------------------------------
# Session-memory tier
# --------------------------------------------------------------------

def test_session_memory_resolves_a_bare_followup():
    session_memory = {"last_network_entity": {"kind": "network", "id": "net-1"}}
    entity = resolve_network_entity("is it still down", [NETWORK, OTHER_NETWORK], session_memory=session_memory)
    assert entity == NETWORK


def test_session_memory_does_not_resolve_a_deleted_entity():
    session_memory = {"last_network_entity": {"kind": "network", "id": "net-99-deleted"}}
    entity = resolve_network_entity("is it still down", [NETWORK, OTHER_NETWORK], session_memory=session_memory)
    assert entity is None


def test_session_memory_kind_must_also_match():
    # Same id coincidentally used by a different kind -- should not cross-match.
    session_memory = {"last_network_entity": {"kind": "subnet", "id": "net-1"}}
    entity = resolve_network_entity("is it still down", [NETWORK, OTHER_NETWORK], session_memory=session_memory)
    assert entity is None


def test_no_session_memory_returns_none():
    entity = resolve_network_entity("is it still down", [NETWORK, OTHER_NETWORK])
    assert entity is None
