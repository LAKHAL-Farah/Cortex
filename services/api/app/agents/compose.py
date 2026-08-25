"""Final step of the graph -- the aggregation/arbitration node. Trivial by
design for v0.1 -- there's exactly one agent, so there's nothing to
aggregate or arbitrate between yet. This node exists mainly so the
endpoint always reads final_answer off the same state key, regardless of
which agent ran or whether it errored.

Still trivial as of v0.4's anomaly agent: that agent does its own
sub-orchestration internally (metric-check + log-check merged into one
AgentResult, see nodes/anomaly.py) before it ever gets here, so per the
"first incident investigation" plan this stays "present Anomaly's merged
finding" -- there's still only one investigating agent, so there's no
cross-agent theory to compare yet. Once a second investigating agent
exists (Security Agent, reusing anomaly.py's sub-orchestration pattern),
*this* is where genuine arbitration between competing findings belongs.
"""
from .state import CortexState


def compose_answer(state: CortexState) -> CortexState:
    if state.get("error"):
        state["final_answer"] = state["error"]
        return state

    result = state.get("agent_result")
    if not result:
        state["final_answer"] = "Something went wrong and no agent produced a result."
        return state

    state["final_answer"] = result["summary"]
    return state
