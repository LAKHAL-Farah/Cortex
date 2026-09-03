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

v0.6 (adr-0008, the OpenStack Expert Agent) adds a sixth node,
openstack_expert, reachable two ways -- this is the first agent that
*isn't* just one more router branch running in isolation:

- Directly from the router, exactly like every other agent, for a
  standalone "how do I check X" question (a sixth conditional-edge
  target next to "clarify").
- **Chained** straight after anomaly or monitoring, via a second
  conditional edge on each of *those* nodes: should_trigger_after_anomaly
  / should_trigger_after_monitoring (nodes/openstack_expert.py) decide,
  from the diagnosis that node just produced, whether there's a
  recognizable symptom worth walking through -- if not, that branch goes
  straight to compose exactly as it did before v0.6. This is why v0.5 had
  to land first: chaining a second agent after a diagnostic one is
  precisely the "more ways for a call to fail" scenario the resilience
  layer exists for, and openstack_expert is wrapped in guarded_node the
  same as every other node, so a hang or crash in the second agent
  degrades to the *first* agent's own diagnosis being shown (via the
  outer safety net's error path) rather than losing the turn entirely.

v0.7 (adr-0009, observability & eval) inserts one new convergence point,
"critic", between *every* branch above and "compose" -- every path that
used to go straight to compose now goes to critic first (which always
continues on to compose; it never dead-ends or loops). This is deliberate:
the critic's evidence-grounding check (nodes/critic.py) needs to run
exactly once per turn, after whichever agent(s) actually produced the
final agent_result, regardless of which of the (by now six) branches got
there or whether a chain happened -- putting it anywhere else would mean
duplicating the same conditional-edge logic openstack_expert's chaining
already has. "router" itself and "critic"/"compose" are wrapped in
trace.traced rather than resilience.guarded_node: they're simple,
synchronous, and (unlike an agent node) don't call a slow/unreliable
external dependency on their own account, so there's no timeout/degrade
case worth the guarded_node machinery -- they just need a trace event
recorded, which trace.traced does directly.

v0.8 (efficiency & scale prep) replaces the single "anomaly" node with
three: "anomaly_dispatch" (resolves which node(s) this turn investigates,
scoped by the Living Model rather than any fixed list -- see nodes/
anomaly.py) fans out via a conditional edge that returns a list of
`Send("anomaly_investigate", ...)`, one per node, instead of the usual
single string target -- LangGraph runs however many of those land
concurrently rather than this graph looping over them one at a time, so
investigating several nodes doesn't cost several times the latency of
investigating one. Every branch still converges through "anomaly_arbitrate"
(which consumes the fan-out's `agent_results` and re-populates the same
single `agent_result` shape everything downstream already expects) before
rejoining should_trigger_after_anomaly / critic exactly as the old single
"anomaly" node did -- from openstack_expert/critic/compose's point of
view, nothing about the shape they consume changed, only how it got
produced. The trace event for this whole fan-out is still recorded under
the name "anomaly" (on anomaly_arbitrate, via the same guarded_node
wrapper the old single node used) so existing trace/dashboard consumers
that look for a step named "anomaly" keep working unchanged.
"""
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from .compose import compose_answer
from .intent_router import route
from .nodes.anomaly import (
    anomaly_arbitrate,
    anomaly_dispatch,
    anomaly_investigate_one,
)
from .nodes.critic import critic_check
from .nodes.monitoring import monitoring_agent
from .nodes.openstack_expert import (
    openstack_expert_agent,
    should_trigger_after_anomaly,
    should_trigger_after_monitoring,
)
from .nodes.prediction import prediction_agent
from .nodes.rag import rag_agent
from .resilience import guarded_node
from .state import CortexState
from .trace import traced


def _fan_out_to_investigate(state: CortexState):
    """Conditional edge off anomaly_dispatch: either the dispatch step
    already gave up (no resolvable scope -- state["error"] is set, same
    short-circuit "couldn't tell which node" always used), or it's time to
    actually investigate -- one Send per node in state["incident_scope"],
    run concurrently rather than looped."""
    if state.get("error"):
        return "critic"
    return [
        Send("anomaly_investigate", {"user_query": state["user_query"], "node": node})
        for node in state["incident_scope"]
    ]


def build_graph():
    graph = StateGraph(CortexState)
    graph.add_node("router", traced("router")(route))
    graph.add_node("monitoring", guarded_node("monitoring", timeout_seconds=15.0)(monitoring_agent))
    graph.add_node("prediction", guarded_node("prediction", timeout_seconds=15.0)(prediction_agent))
    graph.add_node("rag", guarded_node("rag", timeout_seconds=25.0)(rag_agent))
    graph.add_node("anomaly_dispatch", guarded_node("anomaly_dispatch", timeout_seconds=8.0)(anomaly_dispatch))
    graph.add_node("anomaly_investigate", anomaly_investigate_one)
    graph.add_node("anomaly_arbitrate", guarded_node("anomaly", timeout_seconds=30.0)(anomaly_arbitrate))
    graph.add_node(
        "openstack_expert",
        guarded_node("openstack_expert", timeout_seconds=20.0)(openstack_expert_agent),
    )
    graph.add_node("critic", traced("critic")(critic_check))
    graph.add_node("compose", traced("compose")(compose_answer))

    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        lambda state: state["target_agent"],
        {
            "monitoring": "monitoring",
            "prediction": "prediction",
            "rag": "rag",
            "anomaly": "anomaly_dispatch",
            "openstack_expert": "openstack_expert",
            # No node to run -- the router already wrote the clarifying
            # question into state["error"]; go straight through critic
            # (which no-ops on an error turn, see nodes/critic.py) to the
            # same convergence point every other branch uses.
            "clarify": "critic",
        },
    )
    graph.add_conditional_edges("anomaly_dispatch", _fan_out_to_investigate, {"critic": "critic"})
    graph.add_edge("anomaly_investigate", "anomaly_arbitrate")
    graph.add_conditional_edges(
        "anomaly_arbitrate",
        lambda state: "openstack_expert" if should_trigger_after_anomaly(state) else "critic",
        {"openstack_expert": "openstack_expert", "critic": "critic"},
    )
    graph.add_conditional_edges(
        "monitoring",
        lambda state: "openstack_expert" if should_trigger_after_monitoring(state) else "critic",
        {"openstack_expert": "openstack_expert", "critic": "critic"},
    )
    graph.add_edge("prediction", "critic")
    graph.add_edge("rag", "critic")
    graph.add_edge("openstack_expert", "critic")
    graph.add_edge("critic", "compose")
    graph.add_edge("compose", END)

    return graph.compile()


# Compiled once at import time -- app_graph.invoke(...) is the only thing
# routers/agents.py needs to call.
app_graph = build_graph()
