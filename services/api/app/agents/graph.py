"""LangGraph wiring for the agentic layer.

v0.1 had exactly one agent, so the router->monitoring edge was a fixed
graph.add_edge (the router still ran a real classifier, it just only ever
had one possible output). v0.2 added two more agents, making routing a
genuine add_conditional_edges keyed off state["target_agent"] -- the
router (an LLM call, see intent_router.py) decides at runtime which
branch to take; nothing about which agent runs for a given question is
hardcoded here or anywhere else in the graph.

v0.4 adds a fourth branch, anomaly -- the first agent that does internal
sub-orchestration (metric-check + log-check merged into one AgentResult
inside nodes/anomaly.py) rather than a single data pull. From this
graph's point of view it's still just one more node with one more edge
into compose: the sub-orchestration is entirely private to that node, and
compose stays the single place every branch converges on before END,
which is exactly what lets it double as the (still-trivial, single-agent)
arbitration step -- see compose.py's docstring.
"""
from langgraph.graph import END, StateGraph

from .compose import compose_answer
from .intent_router import route
from .nodes.anomaly import anomaly_agent
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
    graph.add_node("anomaly", anomaly_agent)
    graph.add_node("compose", compose_answer)

    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        lambda state: state["target_agent"],
        {
            "monitoring": "monitoring",
            "prediction": "prediction",
            "rag": "rag",
            "anomaly": "anomaly",
        },
    )
    graph.add_edge("monitoring", "compose")
    graph.add_edge("prediction", "compose")
    graph.add_edge("rag", "compose")
    graph.add_edge("anomaly", "compose")
    graph.add_edge("compose", END)

    return graph.compile()


# Compiled once at import time -- app_graph.invoke(...) is the only thing
# routers/agents.py needs to call.
app_graph = build_graph()
