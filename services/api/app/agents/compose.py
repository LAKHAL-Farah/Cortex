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
"""
from .resilience import FailureRecord
from .state import CortexState

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

    state["final_answer"] = answer
    return state
