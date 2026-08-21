"""Classifies a question's intent and picks the target agent to handle it.

v0.1 only has one real target ("monitoring"), so this is a real classifier
with a one-label output rather than a hardcoded stub -- adding a second
agent in v0.2 is then just widening INTENT_TO_AGENT and, in graph.py,
swapping the fixed router->monitoring edge for add_conditional_edges keyed
off state["target_agent"]. No LLM call yet: keyword matching is enough to
prove the mechanism, and it costs nothing per request.
"""
from .state import CortexState

# question keywords -> target agent name. First match wins; extend this
# (not the function body) when a new agent comes online.
INTENT_TO_AGENT: dict[str, tuple[str, ...]] = {
    "monitoring": (
        "cpu",
        "ram",
        "memory",
        "disk",
        "load",
        "uptime",
        "status",
        "health",
        "network",
        "usage",
        "up",
        "down",
        "online",
        "offline",
    ),
}

DEFAULT_AGENT = "monitoring"  # only agent that exists in v0.1


def route(state: CortexState) -> CortexState:
    query = state["user_query"].lower()

    target = DEFAULT_AGENT
    for agent_name, keywords in INTENT_TO_AGENT.items():
        if any(kw in query for kw in keywords):
            target = agent_name
            break

    state["intent"] = target  # v0.1: intent label == agent name 1:1
    state["target_agent"] = target
    return state
