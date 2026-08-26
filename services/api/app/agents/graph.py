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

v0.5 (adr-0007, the resilience layer) adds two things, deliberately
without touching this graph's shape:

- Every agent node is wrapped in resilience.guarded_node before being
  registered here, so a node that hangs or raises unexpectedly degrades
  this one turn (a FailureRecord + an apology in state["error"]) instead
  of the request never returning or an exception bubbling out of
  app_graph.invoke(). This is a pure wrapping step at registration time --
  nodes/*.py's own functions are untouched, and each still handles its own
  *known* failure modes exactly as before (see e.g. anomaly.py's Loki
  fallback, which degrades far more gracefully than "skip the whole
  agent" and is exactly why that node's own internal handling is kept,
  not replaced, by this outer safety net).
- A fifth conditional-edge target, "clarify", alongside the four agents --
  the router can now decide *no* agent should run yet (confidence too low,
  see intent_router.py), in which case it's already written the
  clarifying question into state["error"] and there's nothing left to do
  but go straight to compose, same as any other error short-circuit.
"""
from langgraph.graph import END, StateGraph

from .compose import compose_answer
from .intent_router import route
from .nodes.anomaly import anomaly_agent
from .nodes.monitoring import monitoring_agent
from .nodes.prediction import prediction_agent
from .nodes.rag import rag_agent
from .resilience import guarded_node
from .state import CortexState


def build_graph():
    graph = StateGraph(CortexState)
    graph.add_node("router", route)
    graph.add_node("monitoring", guarded_node("monitoring", timeout_seconds=15.0)(monitoring_agent))
    graph.add_node("prediction", guarded_node("prediction", timeout_seconds=15.0)(prediction_agent))
    graph.add_node("rag", guarded_node("rag", timeout_seconds=25.0)(rag_agent))
    graph.add_node("anomaly", guarded_node("anomaly", timeout_seconds=30.0)(anomaly_agent))
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
            # No node to run -- the router already wrote the clarifying
            # question into state["error"]; go straight to the same
            # convergence point every other branch uses.
            "clarify": "compose",
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
