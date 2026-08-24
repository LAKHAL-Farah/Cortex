"""LangGraph wiring for the agentic layer.

v0.1 had exactly one agent, so the router->monitoring edge was a fixed
graph.add_edge (the router still ran a real classifier, it just only ever
had one possible output). v0.2 adds two more agents, so routing is now a
genuine add_conditional_edges keyed off state["target_agent"] -- the
router (an LLM call, see intent_router.py) decides at runtime which of the
three branches to take; nothing about which agent runs for a given
question is hardcoded here or anywhere else in the graph.
"""
from langgraph.graph import END, StateGraph

from .compose import compose_answer
from .intent_router import route
from .nodes.monitoring import monitoring_agent
from .nodes.prediction import prediction_agent
from .nodes.rag import rag_agent
from .state import CortexState


def build_graph():
    graph = StateGraph(CortexState)
    graph.add_node("router", route)
    graph.add_node("monitoring", monitoring_agent)
    graph.add_node("prediction", prediction_agent)
    graph.add_node("rag", rag_agent)
    graph.add_node("compose", compose_answer)

    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        lambda state: state["target_agent"],
        {
            "monitoring": "monitoring",
            "prediction": "prediction",
            "rag": "rag",
        },
    )
    graph.add_edge("monitoring", "compose")
    graph.add_edge("prediction", "compose")
    graph.add_edge("rag", "compose")
    graph.add_edge("compose", END)

    return graph.compile()


# Compiled once at import time -- app_graph.invoke(...) is the only thing
# routers/agents.py needs to call.
app_graph = build_graph()
