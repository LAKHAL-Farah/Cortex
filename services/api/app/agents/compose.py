"""Final step of the graph -- the aggregation/arbitration node. Trivial by
design for v0.1 -- there's exactly one agent, so there's nothing to
aggregate or arbitrate between yet. This node exists mainly so the
endpoint always reads final_answer off the same state key, regardless of
which agent ran or whether it errored.

Still trivial in the "compare competing findings" sense as of v0.4's
anomaly agent: that agent does its own sub-orchestration internally
(metric-check + log-check merged into one AgentResult, see
nodes/anomaly.py) before it ever gets here, so per the "first incident
investigation" plan this stays "present Anomaly's merged finding" --
there's still only one investigating agent, so there's no cross-agent
theory to compare yet. Once a second investigating agent exists (Security
Agent, reusing anomaly.py's sub-orchestration pattern), *that* kind of
arbitration belongs here.

v0.5 (adr-0007) gives this node one genuinely new job, though: reading
state["failures"] and, when it's non-empty, prefixing the answer with an
honest note about what didn't work rather than presenting a thinner
finding as if nothing had gone wrong. This is deliberately generic -- it
doesn't know or care which agent or breaker produced a FailureRecord, so
every future agent gets this for free just by pushing failures into that
list (via resilience.get_breaker(...).call(...)), the same way every
future agent already gets "converges on compose before END" for free.

v0.7 (adr-0009) adds a second, analogous honesty note: when the critic
node (nodes/critic.py), which now always runs just before this one, flags
at least one ungrounded claim, compose prepends a caution note the same
way it prepends a degraded-evidence note, and caps the reported
confidence -- a summary containing a claim its own evidence doesn't back
up shouldn't ship at full confidence just because the gathering itself
succeeded. This is deliberately a caveat, not a rewrite or a dropped
answer: the critic's checks (numeric/lexical grounding) are heuristic
enough that a false positive is possible, and telling the user "verify
this" costs far less than silently discarding a mostly-correct finding
over one flagged sentence would.
"""
from .resilience import FailureRecord
from .state import CortexState

# A flagged critic verdict never raises confidence and always caps it
# below "fully trust this" -- same spirit as resilience.py's
# _DEGRADED_LOG_CONFIDENCE_CAP in nodes/anomaly.py, just for "the
# narration said something the evidence doesn't" instead of "a sub-check
# failed to run at all".
_CRITIC_FLAGGED_CONFIDENCE_CAP = 0.4

# breaker name -> short, human phrase for the degraded-answer note.
# Falls back to a generic phrase built from the name for anything not
# listed here, so a new agent's breaker doesn't need an entry added here
# to get a sensible (if less polished) note -- see _describe_source.
_SOURCE_LABELS = {
    "anomaly.loki": "the log-check",
}


def _describe_source(source: str) -> str:
    if source in _SOURCE_LABELS:
        return _SOURCE_LABELS[source]
    # "monitoring" -> "the monitoring step"; "anomaly.loki" (unlisted) ->
    # "the anomaly loki step" -- readable without a bespoke entry per name.
    return "the " + source.replace(".", " ").replace("_", " ") + " step"


def _degraded_note(failures: list[FailureRecord]) -> str:
    parts = sorted({_describe_source(f["source"]) for f in failures})
    if len(parts) == 1:
        described = parts[0]
    else:
        described = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    return (
        f"_Note: {described} failed while gathering evidence for this answer -- what follows "
        "reflects the evidence that did come back, with reduced confidence._"
    )


def _critic_note(flagged_claims: list[str]) -> str:
    example = flagged_claims[0]
    return (
        "_Note: this answer contains at least one claim "
        f'("{example}") that could not be verified against the evidence '
        "gathered for it -- treat it with extra caution._"
    )


def compose_answer(state: CortexState) -> CortexState:
    if state.get("error"):
        state["final_answer"] = state["error"]
        return state

    result = state.get("agent_result")
    if not result:
        state["final_answer"] = "Something went wrong and no agent produced a result."
        return state

    answer = result["summary"]
    failures = state.get("failures") or []
    if failures:
        answer = f"{_degraded_note(failures)}\n\n{answer}"

    verdict = state.get("critic_verdict")
    if verdict and verdict["status"] == "flagged":
        answer = f"{_critic_note(verdict['flagged_claims'])}\n\n{answer}"
        result["confidence"] = min(result.get("confidence", 1.0), _CRITIC_FLAGGED_CONFIDENCE_CAP)

    state["final_answer"] = answer
    return state
