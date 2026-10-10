"""
services/api/tests/test_proposal_qa.py

The answerer for "ask for more information" (services/proposal_qa.py): which
topics a question maps to, and that every answer is built from the stored
proposal only. Pure -- no database, no graph, no LLM.
"""
import copy

import pytest

from app.agents.nodes import openstack_expert as expert
from app.agents.nodes.openstack_expert_catalog import CATALOG
from app.agents.nodes.remediation import build_fix_proposal
from app.services.proposal_qa import MAX_TOPICS, answer_question, pick_topics

BY_ID = {e["id"]: e for e in CATALOG}


def _proposal(symptom="nova-compute-down"):
    result = expert._build_result(BY_ID[symptom], "compute-02's service is down.", "compute-02", extra_raw={"diagnosed_by": "monitoring"})
    return copy.deepcopy(build_fix_proposal(result["raw_data"]))


@pytest.mark.parametrize(
    "question, expected",
    [
        ("How do I undo this?", "undo"),
        ("Can I roll back if it goes wrong?", "undo"),
        ("Which instances will be affected?", "impact"),
        ("Will there be downtime for the VMs?", "impact"),
        ("How risky is it?", "risk"),
        ("Why was this proposed?", "why"),
        ("How can I verify it worked?", "verify"),
        ("Is there a safer alternative?", "alternatives"),
        ("What is still missing from me?", "inputs"),
        ("Can Cortex execute this automatically?", "execution"),
        ("What does the command do exactly?", "what"),
        ("Comment annuler cette commande ?", "undo"),
        ("Quel est le risque ?", "risk"),
        ("Quel est l'impact sur les instances ?", "impact"),
    ],
)
def test_a_question_maps_to_its_topic(question, expected):
    assert expected in pick_topics(question)


def test_at_most_three_topics_are_answered():
    topics = pick_topics("what is the risk and impact, how do I undo and verify it, any alternative, can Cortex execute it, why?")
    assert 0 < len(topics) <= MAX_TOPICS


def test_a_question_that_matches_nothing_gets_the_overview():
    qa = answer_question(_proposal(), "zxqv?")
    assert qa["matched"] is False and qa["topics"] == ["what", "why", "risk", "impact"]
    assert "could not tell which part" in qa["answer"]


def test_empty_or_missing_questions_do_not_crash():
    for q in ("", None, "   "):
        assert answer_question(_proposal(), q)["matched"] is False


def test_the_answer_quotes_the_proposal_and_nothing_else():
    proposal = _proposal()
    qa = answer_question(proposal, "what does it run and why, and how do I undo it?")
    assert proposal["primary"]["command"] in qa["answer"]
    assert proposal["evidence"] in qa["answer"]
    # the undo section is present and comes from the step's own undo note
    assert proposal["primary"]["undo_note"] in qa["answer"] or proposal["primary"]["undo"] in qa["answer"]


def test_undo_reports_the_derived_undo_when_there_is_one():
    proposal = _proposal("host-ram-pressure")  # disable compute service -> --enable
    assert proposal["primary"]["undo"]
    assert proposal["primary"]["undo"] in answer_question(proposal, "how do I undo it?")["answer"]


def test_impact_without_a_simulation_says_there_is_none_rather_than_inventing_one():
    qa = answer_question(_proposal(), "what is the impact on instances?")
    assert "No impact simulation was recorded" in qa["answer"]


def test_impact_uses_the_recorded_simulation():
    proposal = _proposal()
    proposal["simulation"] = {
        "status": "simulated", "verdict": "disruptive", "headline": "Takes 3 instances offline.",
        "effects": [{"name": "vm-1", "effect": "stops", "detail": "root disk kept"}], "effects_total": 3,
        "warnings": ["Do it in a quiet window."], "assumptions": [], "reversible": False, "duration": "about a minute",
    }
    answer = answer_question(proposal, "which instances will be disrupted?")["answer"]
    assert "Takes 3 instances offline." in answer and "vm-1: stops -- root disk kept" in answer
    assert "...and 2 more not listed." in answer and "Not reversible with one command." in answer
    assert "Do it in a quiet window." in answer and "about a minute" in answer


def test_it_is_deterministic():
    p = _proposal()
    assert answer_question(p, "risk and undo") == answer_question(p, "risk and undo")
