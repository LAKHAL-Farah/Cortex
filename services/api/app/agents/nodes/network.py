"""Network agent -- router/floating-IP/agent health, and node-level
network-traffic anomalies (v0.9, Sprint 6 story 6.2: "Network Agent
(leaf, straightforward -- same shape as Monitoring)").

Like monitoring_agent, this pulls from live sources with no LLM in the
data path -- an LLM only resolves which node the question is about
(node_resolver.py, same as monitoring/prediction/anomaly) and narrates the
two readings gathered below into plain language, never invents or adjusts
a number. Unlike monitoring_agent, there are two independent live sources
instead of one:

1. `_check_node_metrics` -- node_exporter's own network interface counters
   (services/metrics_collector.py's collect_network_metrics()): receive/
   transmit throughput plus, new here, receive/transmit error and drop
   rates -- the node-local half of "east-west traffic anomalies". Ideally
   these error/drop rates sit at (or extremely close to) zero on a
   healthy interface, so any sustained positive rate is itself the
   signal, not a percentage threshold the way CPU/RAM/disk use.
2. `_check_neutron` -- services/network_health.py's Neutron control-plane
   read, scoped to whichever Neutron agent(s) run on this node
   (neutron-openvswitch-agent everywhere, plus neutron-l3-agent/
   neutron-dhcp-agent on whichever host actually runs them) and, through
   those agents, the specific routers/floating IPs/networks *this host*
   is responsible for -- the "router/floating-IP/port health" half of the
   role. See network_health.py's own docstring for exactly what's in/out
   of scope here (notably: no literal Neutron "port" resource, since
   openstack-sim doesn't expose one to read).

Unlike anomaly.py's sub-orchestration, this does NOT do a third
hypothesize-cause step or a multi-tier confidence formula -- per the
roadmap table, "Sub-orchestration? No -- single coherent data domain, no
heterogeneous merge needed". The two checks above are complementary views
of the *same* thing (this node's network health), not two different
*kinds* of evidence (a metric vs. a log) that need reconciling the way
anomaly.py's do, so they're presented side by side in the narrative rather
than merged into a single derived hypothesis.

The Neutron read is an external OpenStack API call -- exactly the kind of
dependency adr-0007's resilience layer exists for -- so it goes through
`resilience.get_breaker("network.neutron", ...)`, the same way anomaly.py's
`_check_logs` wraps its Loki call: on failure this node still returns a
usable, honestly-degraded finding (node-exporter metrics only, Neutron
control-plane health unknown rather than assumed healthy), with a
FailureRecord pushed into `state["failures"]` for compose.py to surface.

Reachable two ways, mirroring monitoring_agent/anomaly_agent exactly:
- Directly from the router (intent_router.py's "network" branch) for a
  standalone question ("how's the network on compute-02", "any floating
  IP issues").
- Chained into openstack_expert (see openstack_expert.py's
  should_trigger_after_network) when something concerning turns up --
  the catalog already has neutron-dhcp-agent-down/neutron-l3-agent-down/
  neutron-ovs-agent-down entries (openstack_expert_catalog.py) that were
  sitting there unused until now; this reuses them exactly the way
  monitoring/anomaly's own chaining already does, no new catalog content
  needed.

v0.9 note on cross-agent arbitration: this agent does not (yet) plug into
anomaly.py's dispatch/investigate/arbitrate fan-out -- that wiring, and
compose.py's promised cross-agent arbitration, land once the Security
Agent also exists (see compose.py's module docstring), so a broad "is
anything wrong" incident question can genuinely fan out across
Anomaly + Network (+ Security) at once. For now this is reachable the same
two ways monitoring_agent already is: a direct routed question, or chained
into openstack_expert.
"""
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ...services import network_health
from ...services.llm_client import LLMConfigError, get_chat_model
from ...services.metrics_collector import collect_network_metrics
from ..node_resolver import resolve_node
from ..resilience import get_breaker
from ..state import CortexState

logger = logging.getLogger(__name__)

# node_exporter's receive/transmit error and drop counters should sit at
# ~0 on a healthy interface -- unlike CPU/RAM/disk there's no meaningful
# nonzero "normal" to threshold against, so any sustained positive rate
# over the 5m query window is itself the signal. A tiny epsilon absorbs
# rate()'s own floating-point rounding noise, not real error traffic.
_ERROR_RATE_EPSILON = 0.01

# A degraded Neutron check (control plane unreachable, see _check_neutron)
# means "we don't know", not "confirmed healthy" -- worth less than a
# clean read but not worth crashing the turn over. Same idiom as
# anomaly.py's _DEGRADED_LOG_CONFIDENCE_CAP, applied as a cap rather than
# an offset so it never accidentally raises confidence.
_DEGRADED_NEUTRON_CONFIDENCE_CAP = 0.6


# --------------------------------------------------------------------
# Check 1: node-level network interface counters (Prometheus/node_exporter)
# --------------------------------------------------------------------

def _check_node_metrics(node) -> dict:
    try:
        by_instance = {m["instance"]: m for m in collect_network_metrics()}
    except Exception:
        logger.exception("network_agent: collect_network_metrics() failed")
        return {
            "has_signal": False,
            "detail": "Couldn't reach Prometheus for node-level network counters.",
            "data": None,
        }

    metrics = by_instance.get(node["instance"])
    if metrics is None:
        return {
            "has_signal": False,
            "detail": f"No node-level network data yet for {node['hostname']}.",
            "data": None,
        }

    has_errors = metrics["network_errors_per_sec"] > _ERROR_RATE_EPSILON
    has_drops = metrics["network_drops_per_sec"] > _ERROR_RATE_EPSILON

    if has_errors or has_drops:
        parts = []
        if has_errors:
            parts.append(f"{metrics['network_errors_per_sec']:.2f} errors/sec")
        if has_drops:
            parts.append(f"{metrics['network_drops_per_sec']:.2f} dropped packets/sec")
        detail = (
            f"{node['hostname']}'s network interface is showing "
            f"{' and '.join(parts)} -- a healthy interface reads zero here, "
            "so this is worth a look."
        )
        return {"has_signal": True, "detail": detail, "data": metrics}

    detail = (
        f"{node['hostname']}'s network throughput is {metrics['network_rx_bytes']:.0f} B/s in, "
        f"{metrics['network_tx_bytes']:.0f} B/s out, with no receive/transmit errors or drops."
    )
    return {"has_signal": False, "detail": detail, "data": metrics}


# --------------------------------------------------------------------
# Check 2: Neutron control-plane health (agents/routers/networks/FIPs)
# --------------------------------------------------------------------

def _check_neutron(node) -> dict:
    breaker = get_breaker("network.neutron", timeout_seconds=10.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(network_health.get_node_network_health, node["hostname"])

    if not call_result.ok:
        logger.warning("network_agent: Neutron control-plane check failed: %s", call_result.failure)
        return {
            "has_signal": False,
            "degraded": True,
            "failure": call_result.failure,
            "detail": (
                "The Neutron control-plane check couldn't complete (OpenStack's network API "
                "didn't respond in time), so router/floating-IP/agent health for this host is "
                "unknown rather than confirmed healthy."
            ),
            "data": None,
            "down_agents": [], "bad_routers": [], "bad_networks": [], "bad_fips": [],
        }

    health = call_result.value
    down_agents = [a for a in health["agents"] if not a["alive"] or not a["admin_state_up"]]
    bad_routers = [r for r in health["routers"] if r["status"] != "ACTIVE" or not r["admin_state_up"]]
    bad_networks = [n for n in health["networks"] if n["status"] != "ACTIVE" or not n["admin_state_up"]]
    bad_fips = [f for f in health["floating_ips"] if f["status"] != "ACTIVE"]

    problems = []
    if down_agents:
        names = ", ".join(a["binary"] for a in down_agents)
        problems.append(f"{len(down_agents)} Neutron agent(s) down or disabled on this host ({names})")
    if bad_routers:
        problems.append(f"{len(bad_routers)} router(s) hosted here not fully up")
    if bad_networks:
        problems.append(f"{len(bad_networks)} DHCP-hosted network(s) not fully up")
    if bad_fips:
        problems.append(f"{len(bad_fips)} floating IP(s) on this host's router(s) not ACTIVE")

    if problems:
        detail = f"Neutron control-plane issue(s) for {node['hostname']}: " + "; ".join(problems) + "."
        return {
            "has_signal": True, "degraded": False, "detail": detail, "data": health,
            "down_agents": down_agents, "bad_routers": bad_routers,
            "bad_networks": bad_networks, "bad_fips": bad_fips,
        }

    agent_names = ", ".join(a["binary"] for a in health["agents"]) or "no Neutron agent registered on this host"
    detail = f"Neutron reports {node['hostname']} healthy ({agent_names})."
    return {
        "has_signal": False, "degraded": False, "detail": detail, "data": health,
        "down_agents": [], "bad_routers": [], "bad_networks": [], "bad_fips": [],
    }


# --------------------------------------------------------------------
# Merge: two independent live readings -> one AgentResult
# --------------------------------------------------------------------

_SYSTEM_PROMPT = """You are Cortex's network assistant. You're given two independent live readings \
for one node: node-level network interface counters (throughput, error/drop rates) from Prometheus, \
and Neutron control-plane health (which Neutron agents run on this host, and the routers/networks/ \
floating IPs they're responsible for) from OpenStack. Answer the user's question using ONLY the \
readings given -- never invent a number, agent name, or status. Keep it to 2-4 sentences, direct and \
conversational, and call out anything that looks concerning (nonzero errors/drops, a down/disabled \
agent, a router/network/floating IP not fully up)."""


def _fallback_summary(metric_signal: dict, neutron_signal: dict) -> str:
    return f"{metric_signal['detail']} {neutron_signal['detail']}"


def _narrate(query: str, node, metric_signal: dict, neutron_signal: dict) -> str:
    fallback = _fallback_summary(metric_signal, neutron_signal)
    try:
        llm = get_chat_model(temperature=0.2, tier="fast")
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Question: {query}\n\n"
                        f"Node: {node['hostname']} (role: {node['role']})\n"
                        f"Interface counters: {metric_signal['detail']}\n"
                        f"Neutron control plane: {neutron_signal['detail']}"
                    )
                ),
            ]
        )
        text = (response.content or "").strip()
        return text or fallback
    except LLMConfigError:
        return fallback
    except Exception:
        logger.exception("network_agent: LLM narration failed, using fallback summary")
        return fallback


def _confidence(neutron_signal: dict) -> float:
    if neutron_signal.get("degraded"):
        return _DEGRADED_NEUTRON_CONFIDENCE_CAP
    return 1.0  # both readings are direct live pulls, no inference involved


def network_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
    node = resolve_node(state["user_query"], known_nodes, session_memory=state.get("session_memory"))

    if node is None:
        available = ", ".join(n["hostname"] for n in known_nodes) or "no nodes registered"
        state["error"] = f"I couldn't tell which node you meant. Known nodes: {available}."
        state["agent_result"] = None
        return state

    metric_signal = _check_node_metrics(node)
    neutron_signal = _check_neutron(node)

    summary = _narrate(state["user_query"], node, metric_signal, neutron_signal)
    confidence = _confidence(neutron_signal)

    state["agent_result"] = {
        "summary": summary,
        "confidence": confidence,
        "raw_data": {
            "hostname": node["hostname"],
            "role": node["role"],
            "metric_signal": metric_signal,
            "neutron_signal": neutron_signal,
        },
    }
    state["error"] = None
    if neutron_signal.get("degraded") and neutron_signal.get("failure"):
        state.setdefault("failures", []).append(neutron_signal["failure"])
    state.setdefault("resolved_entities", {})["last_node"] = node
    state["resolved_entities"]["last_agent"] = "network"
    return state
