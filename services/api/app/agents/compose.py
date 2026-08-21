"""Final step of the graph. Trivial by design for v0.1 -- there's exactly
one agent, so there's nothing to aggregate or arbitrate between yet. This
node exists mainly so the endpoint always reads final_answer off the same
state key, regardless of which agent ran or whether it errored.
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
