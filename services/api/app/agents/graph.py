"""3 nodes, linear edges -- the actual LangGraph mechanics for v0.1.

No add_conditional_edges yet: with a single agent, intent_router.route()
always picks "monitoring", so the router->monitoring edge is hardcoded on
purpose. v0.2 adds a second agent by widening
intent_router.INTENT_TO_AGENT and replacing the hardcoded edge below with
add_conditional_edges(router, lambda s: s["target_agent"], {...}) -- the
router node itself doesn't need to change.
"""
from langgraph.graph import END, StateGraph

from .compose import compose_answer
from .intent_router import route
from .nodes.monitoring import monitoring_agent
from .state import CortexState


def build_graph():
    graph = StateGraph(CortexState)
    graph.add_node("router", route)
    graph.add_node("monitoring", monitoring_agent)
    graph.add_node("compose", compose_answer)

    graph.set_entry_point("router")
    graph.add_edge("router", "monitoring")
    graph.add_edge("monitoring", "compose")
    graph.add_edge("compose", END)

    return graph.compile()


# Compiled once at import time -- app_graph.invoke(...) is the only thing
# routers/agents.py needs to call.
app_graph = build_graph()
