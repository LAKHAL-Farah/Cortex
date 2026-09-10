"""Structural checks on tests/golden/routing_golden_set.json that don't
require calling the real NVIDIA NIM classifier (that gate is
scripts/eval_router_golden_set.py, run separately in CI where
NVIDIA_API_KEY is available -- see that script's module docstring for
why this isn't a mocked-classifier test instead). This file exists so a
malformed or shrunk golden set fails a normal `pytest` run immediately,
rather than only being noticed the next time the real eval script runs.
"""
import json
from pathlib import Path

from app.agents.intent_router import AgentName

_GOLDEN_SET_PATH = Path(__file__).parent / "golden" / "routing_golden_set.json"
_VALID_TARGETS = set(AgentName.__args__) | {"clarify"}


def _load():
    with open(_GOLDEN_SET_PATH) as f:
        return json.load(f)["questions"]


def test_golden_set_has_at_least_forty_questions():
    # DoD: "~40-50 questions".
    assert len(_load()) >= 40


def test_every_question_has_a_unique_id():
    ids = [q["id"] for q in _load()]
    assert len(ids) == len(set(ids))


def test_every_expected_target_agent_is_a_real_routable_outcome():
    for q in _load():
        assert q["expected_target_agent"] in _VALID_TARGETS, q


def test_every_question_has_a_provenance_source():
    for q in _load():
        assert q["source"] in {"test_suite", "router_prompt_example", "extrapolated"}, q


def test_every_agent_and_clarify_has_at_least_one_question():
    targets = {q["expected_target_agent"] for q in _load()}
    assert targets == _VALID_TARGETS


def test_no_duplicate_queries():
    queries = [q["query"] for q in _load()]
    assert len(queries) == len(set(queries))
