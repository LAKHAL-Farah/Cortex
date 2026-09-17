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

v0.10 (Phase C, "make the network agent actually smarter") adds a third
question shape on top of the node-scoped one above: "which VMs are on
network X", "why can't instance Y reach the internet", "is anything down
on subnet Z" -- questions scoped by a Neutron/Nova entity, not a physical
node, which node_resolver.py has no way to answer (it only fuzzy-matches
hostnames). `network_agent` below tries `resolve_node` first (unchanged,
still the common case and the cheapest to match) and only falls through to
the new entity-scoped path -- resolve via network_resolver.py's parallel
matcher, then read via network_health.py's `get_network_instance_health`/
`get_subnet_port_health`/`get_instance_connectivity` -- when no physical
node is named at all. The two paths produce differently-shaped `raw_data`
(tagged by a `scope` key, "node" vs "network"/"subnet"/"instance"), so
openstack_expert.py's `_evidence_from_network`/`should_trigger_after_network`
branch on it too -- see that module for how a down port or an
instance stuck in ERROR now feeds the catalog's new `port-down`/
`instance-stuck-in-error` entries.

This same Phase C also folds instance/port data into the *existing*
node-scoped path: `_check_neutron` below now also flags any instance
hosted on the resolved node whose own port is down, using the same
`network_health.get_node_network_health` call as before (v0.9 didn't
change) now returning an `instances` list it didn't used to.
"""
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ...services import network_health
from ...services.llm_client import LLMConfigError, get_chat_model
from ...services.metrics_collector import collect_network_metrics
from ..network_resolver import KnownNetworkEntity, resolve_network_entity
from ..node_resolver import resolve_node
from ..resilience import get_breaker, guarded_send
from ..state import CortexState, IncidentFinding

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
            "down_agents": [], "bad_routers": [], "bad_networks": [], "bad_fips": [], "bad_instances": [],
        }

    health = call_result.value
    down_agents = [a for a in health["agents"] if not a["alive"] or not a["admin_state_up"]]
    bad_routers = [r for r in health["routers"] if r["status"] != "ACTIVE" or not r["admin_state_up"]]
    bad_networks = [n for n in health["networks"] if n["status"] != "ACTIVE" or not n["admin_state_up"]]
    bad_fips = [f for f in health["floating_ips"] if f["status"] != "ACTIVE"]
    # Phase C -- instances hosted here with their own port down/disabled.
    # `.get(...)` rather than a bare index: a monkeypatched
    # get_node_network_health in an older test fixture won't have this
    # key, and that's "no instance data available", not a KeyError.
    bad_instances = [i for i in health.get("instances", []) if i["has_down_port"]]

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
    if bad_instances:
        names = ", ".join(i["name"] or i["id"] for i in bad_instances)
        problems.append(f"{len(bad_instances)} instance(s) here with a down/disabled port ({names})")

    if problems:
        detail = f"Neutron control-plane issue(s) for {node['hostname']}: " + "; ".join(problems) + "."
        return {
            "has_signal": True, "degraded": False, "detail": detail, "data": health,
            "down_agents": down_agents, "bad_routers": bad_routers,
            "bad_networks": bad_networks, "bad_fips": bad_fips, "bad_instances": bad_instances,
        }

    agent_names = ", ".join(a["binary"] for a in health["agents"]) or "no Neutron agent registered on this host"
    detail = f"Neutron reports {node['hostname']} healthy ({agent_names})."
    return {
        "has_signal": False, "degraded": False, "detail": detail, "data": health,
        "down_agents": [], "bad_routers": [], "bad_networks": [], "bad_fips": [], "bad_instances": [],
    }


# --------------------------------------------------------------------
# Check 3 (v0.10, Phase C): entity-scoped Neutron reads -- network /
# subnet / instance, not a physical node. Only reached when resolve_node
# finds nothing (see network_agent below); each function here mirrors
# _check_neutron's shape (has_signal/degraded/detail/data, same breaker,
# same "raises -> degrade, don't crash" contract) so the merge step and
# openstack_expert.py's chaining can treat all four scopes uniformly.
# --------------------------------------------------------------------

def _list_known_entities():
    """Live candidate list for network_resolver.py's matching tiers --
    every known network/subnet/instance, tagged by kind. Wrapped in its
    own breaker (separate from "network.neutron", the breaker every
    _check_* function above and below uses) since this only runs when a
    question doesn't name a physical node at all -- a much rarer path,
    worth its own circuit state rather than sharing (and potentially
    tripping) the far more frequently used node-scoped breaker.
    """
    breaker = get_breaker("network.entity_lookup", timeout_seconds=10.0, max_retries=1, failure_threshold=2)

    def _fetch() -> list[KnownNetworkEntity]:
        entities: list[KnownNetworkEntity] = []
        for n in network_health.list_known_networks():
            entities.append({"kind": "network", "id": n["id"], "name": n["name"], "cidr": None})
        for s in network_health.list_known_subnets():
            entities.append({"kind": "subnet", "id": s["id"], "name": s["name"], "cidr": s["cidr"]})
        for i in network_health.list_known_instances():
            entities.append({"kind": "instance", "id": i["id"], "name": i["name"], "cidr": None})
        return entities

    return breaker.call(_fetch)


def _entity_label(entity: KnownNetworkEntity) -> str:
    return entity.get("name") or entity["id"]


def _degraded_entity_signal(entity: KnownNetworkEntity, call_result) -> dict:
    return {
        "has_signal": False,
        "degraded": True,
        "failure": call_result.failure,
        "detail": (
            f"The Neutron check for {_entity_label(entity)} couldn't complete (OpenStack's network "
            "API didn't respond in time), so its health is unknown rather than confirmed healthy."
        ),
        "data": None,
        "down_ports": [],
        "down_instances": [],
    }


def _check_network_scope(entity: KnownNetworkEntity) -> dict:
    breaker = get_breaker("network.neutron", timeout_seconds=10.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(network_health.get_network_instance_health, entity["id"])
    if not call_result.ok:
        logger.warning("network_agent: network-scoped Neutron check failed: %s", call_result.failure)
        return _degraded_entity_signal(entity, call_result)

    data = call_result.value
    label = _entity_label(entity)
    rows = data["instances"]
    down_rows = [r for r in rows if r["port_down"]]
    down_ports = [r["port"] for r in down_rows]
    down_instances = [r["instance"] for r in down_rows if r["instance"] is not None]

    if not rows:
        detail = f"No instances are attached to {label}."
    elif down_rows:
        bad_names = ", ".join(
            (r["instance"]["name"] or r["instance"]["id"]) if r["instance"] else (r["port"]["name"] or r["port"]["id"])
            for r in down_rows
        )
        detail = (
            f"{label} has {len(rows)} instance(s): {len(rows) - len(down_rows)} with a healthy port, "
            f"{len(down_rows)} with a down/disabled port ({bad_names})."
        )
    else:
        vm_names = ", ".join((r["instance"]["name"] or r["instance"]["id"]) if r["instance"] else "unowned port" for r in rows)
        detail = f"{label} has {len(rows)} instance(s), all with healthy ports: {vm_names}."

    return {
        "has_signal": bool(down_rows), "degraded": False, "detail": detail, "data": data,
        "down_ports": down_ports, "down_instances": down_instances,
    }


def _check_subnet_scope(entity: KnownNetworkEntity) -> dict:
    breaker = get_breaker("network.neutron", timeout_seconds=10.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(network_health.get_subnet_port_health, entity["id"])
    if not call_result.ok:
        logger.warning("network_agent: subnet-scoped Neutron check failed: %s", call_result.failure)
        return _degraded_entity_signal(entity, call_result)

    data = call_result.value
    label = _entity_label(entity)
    down_ports = data["down_ports"]
    dead_dhcp = [a for a in data["dhcp_agents"] if not a["alive"] or not a["admin_state_up"]]

    problems = []
    if down_ports:
        names = ", ".join(p["name"] or p["id"] for p in down_ports)
        problems.append(f"{len(down_ports)} port(s) down/disabled ({names})")
    if dead_dhcp:
        problems.append("the DHCP agent hosting this subnet's network is down or disabled")

    if problems:
        detail = f"{label}: " + "; ".join(problems) + "."
    else:
        detail = f"{label} looks healthy -- {len(data['ports'])} port(s), all up, DHCP agent(s) alive."

    return {
        "has_signal": bool(problems), "degraded": False, "detail": detail, "data": data,
        "down_ports": down_ports, "down_instances": [],
    }


def _check_instance_scope(entity: KnownNetworkEntity) -> dict:
    breaker = get_breaker("network.neutron", timeout_seconds=10.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(network_health.get_instance_connectivity, entity["id"])
    if not call_result.ok:
        logger.warning("network_agent: instance-scoped Neutron check failed: %s", call_result.failure)
        return _degraded_entity_signal(entity, call_result)

    data = call_result.value
    label = _entity_label(entity)

    if not data["ports"]:
        detail = f"{label} has no Neutron port at all -- it isn't attached to any network."
        down_instances = [data["instance"]] if data.get("instance") else []
        return {
            "has_signal": True, "degraded": False, "detail": detail, "data": data,
            "down_ports": [], "down_instances": down_instances,
        }

    reasons = []
    down_ports = []
    for report in data["ports"]:
        port = report["port"]
        net_name = report["network"]["name"] if report["network"] else port["network_id"]
        if report["port_down"]:
            down_ports.append(port)
            reasons.append(f"its port on {net_name} is down/admin-disabled")
            continue  # a down port alone already explains no reachability for this port
        if not report["network_is_external"] and not report["gateway_routers"]:
            reasons.append(f"{net_name} has no router with an external gateway, so it can't reach outside networks")
        elif not report["network_is_external"] and not report["floating_ips"]:
            reasons.append(
                f"it has no floating IP on {net_name} -- outbound may still work via the router's SNAT, "
                "but nothing can reach it from outside"
            )

    instance_status = (data.get("instance") or {}).get("status")
    down_instances = [data["instance"]] if down_ports and instance_status == "ERROR" else []

    if reasons:
        detail = f"{label}: " + "; ".join(reasons) + "."
        has_signal = True
    else:
        detail = (
            f"{label}'s port(s) are up, on a network with an external-gatewayed router or a floating "
            "IP -- nothing here explains a reachability problem."
        )
        has_signal = False

    return {
        "has_signal": has_signal, "degraded": False, "detail": detail, "data": data,
        "down_ports": down_ports, "down_instances": down_instances,
    }


_ENTITY_SCOPE_CHECKS = {
    "network": _check_network_scope,
    "subnet": _check_subnet_scope,
    "instance": _check_instance_scope,
}


# --------------------------------------------------------------------
# Merge: independent live readings -> one AgentResult
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


def _confidence(signal: dict) -> float:
    if signal.get("degraded"):
        return _DEGRADED_NEUTRON_CONFIDENCE_CAP
    return 1.0  # every reading here is a direct live pull, no inference involved


# v0.10 (Phase C) -- narration for the entity-scoped path. Kept as its own
# system prompt/function rather than widening _SYSTEM_PROMPT/_narrate
# above: those are written specifically around "two readings for one
# node" (interface counters + Neutron), and an entity-scoped question has
# exactly one Neutron reading, no node-level counters at all (there's no
# such thing as a per-VM node_exporter interface metric in this stack).
_ENTITY_SYSTEM_PROMPT = """You are Cortex's network assistant. You're given a live Neutron reading \
scoped to one specific network, subnet, or instance (not a physical node) -- ports and their up/down, \
admin-enabled state, the instances that own them, gateway routers, and floating IPs, as relevant. \
Answer the user's question using ONLY the reading given -- never invent a name, id, or status. Keep \
it to 2-4 sentences, direct and conversational, and call out anything that looks concerning (a down/ \
disabled port, an instance with no working network path, a subnet with no live DHCP agent)."""


def _narrate_entity(query: str, entity: KnownNetworkEntity, entity_signal: dict) -> str:
    fallback = entity_signal["detail"]
    try:
        llm = get_chat_model(temperature=0.2, tier="fast")
        response = llm.invoke(
            [
                SystemMessage(content=_ENTITY_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Question: {query}\n\n"
                        f"{entity['kind'].capitalize()}: {_entity_label(entity)}\n"
                        f"Reading: {entity_signal['detail']}"
                    )
                ),
            ]
        )
        text = (response.content or "").strip()
        return text or fallback
    except LLMConfigError:
        return fallback
    except Exception:
        logger.exception("network_agent: LLM narration failed for entity scope, using fallback summary")
        return fallback


def _investigate(query: str, node) -> tuple[dict, list]:
    """Pure node-scope investigation -- (AgentResult, embedded_failures),
    no graph state touched. Mirrors nodes/anomaly.py's `_investigate` /
    nodes/security.py's `_investigate` exactly, for the same reason: v0.9
    adds `network_investigate_one`, a fan-out Send target (see graph.py)
    that needs this same node-scope pipeline without a full CortexState to
    mutate (see resilience.guarded_send's docstring on why a Send target
    works off a narrow payload, not the graph's full state) -- kept in
    exactly one place so the standalone leaf path (_run_node_scope below)
    and the fan-out path can never drift apart, same rationale anomaly.py
    already gives for its own `_investigate`.
    """
    metric_signal = _check_node_metrics(node)
    neutron_signal = _check_neutron(node)

    summary = _narrate(query, node, metric_signal, neutron_signal)
    confidence = _confidence(neutron_signal)

    agent_result = {
        "summary": summary,
        "confidence": confidence,
        "raw_data": {
            "scope": "node",
            "hostname": node["hostname"],
            "role": node["role"],
            # v0.9: uniform, agent-shape-agnostic flag every agent's
            # raw_data now carries (see anomaly.py/security.py) -- lets
            # anomaly_arbitrate's cross-agent corroboration check ask "did
            # this agent find anything" without knowing this module's own
            # raw_data layout.
            "has_signal": metric_signal["has_signal"] or neutron_signal["has_signal"],
            "metric_signal": metric_signal,
            "neutron_signal": neutron_signal,
        },
    }

    failures = []
    if neutron_signal.get("degraded") and neutron_signal.get("failure"):
        failures.append(neutron_signal["failure"])
    return agent_result, failures


def _run_node_scope(state: CortexState, node) -> CortexState:
    agent_result, failures = _investigate(state["user_query"], node)

    state["agent_result"] = agent_result
    state["error"] = None
    for failure in failures:
        state.setdefault("failures", []).append(failure)
    state.setdefault("resolved_entities", {})["last_node"] = node
    state["resolved_entities"]["last_agent"] = "network"
    return state


# --------------------------------------------------------------------
# v0.9: fan-out Send target -- one branch of the multi-agent incident
# investigation (see graph.py's `_fan_out_to_investigate` and
# nodes/anomaly.py's `anomaly_arbitrate`, which joins this back together
# with anomaly's and security's own findings for the same node). Only
# ever dispatched for a node already known by hostname (the fan-out's
# `incident_scope` is a list[KnownNode], see state.py) -- the
# entity-scoped path (_run_entity_scope) has no equivalent here, since a
# broad "is anything wrong" incident question is asked about nodes, not
# about a specific network/subnet/instance by name.
# --------------------------------------------------------------------

def _network_investigate_one_impl(payload: dict) -> dict:
    query = payload["user_query"]
    node = payload["node"]
    agent_result, failures = _investigate(query, node)
    finding: IncidentFinding = {
        "hostname": node["hostname"],
        "agent": "network",
        "agent_result": agent_result,
        "failures": failures,
    }
    return {"agent_results": [finding]}


network_investigate_one = guarded_send("network.investigate", timeout_seconds=60.0)(_network_investigate_one_impl)


def _run_entity_scope(state: CortexState, known_nodes) -> CortexState:
    """The v0.10 (Phase C) fallback path -- only reached once resolve_node
    has already come back empty (see network_agent below), so this never
    pays the extra OpenStack list calls _list_known_entities needs for a
    plain node-scoped question.
    """
    query = state["user_query"]
    session_memory = state.get("session_memory")

    entities_result = _list_known_entities()
    if not entities_result.ok:
        logger.warning("network_agent: entity candidate lookup failed: %s", entities_result.failure)
        available = ", ".join(n["hostname"] for n in known_nodes) or "no nodes registered"
        state["error"] = (
            "I couldn't tell which node you meant, and couldn't look up networks/subnets/instances "
            f"either (OpenStack's API didn't respond in time). Known nodes: {available}."
        )
        state["agent_result"] = None
        state.setdefault("failures", []).append(entities_result.failure)
        return state

    entity = resolve_network_entity(query, entities_result.value, session_memory=session_memory)
    if entity is None:
        available = ", ".join(n["hostname"] for n in known_nodes) or "no nodes registered"
        state["error"] = (
            f"I couldn't tell which node, network, subnet, or instance you meant. Known nodes: {available}."
        )
        state["agent_result"] = None
        return state

    entity_signal = _ENTITY_SCOPE_CHECKS[entity["kind"]](entity)
    summary = _narrate_entity(query, entity, entity_signal)
    confidence = _confidence(entity_signal)

    state["agent_result"] = {
        "summary": summary,
        "confidence": confidence,
        "raw_data": {
            "scope": entity["kind"],
            "entity": entity,
            "entity_signal": entity_signal,
        },
    }
    state["error"] = None
    if entity_signal.get("degraded") and entity_signal.get("failure"):
        state.setdefault("failures", []).append(entity_signal["failure"])
    state.setdefault("resolved_entities", {})["last_network_entity"] = entity
    state["resolved_entities"]["last_agent"] = "network"
    return state


def network_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
    node = resolve_node(state["user_query"], known_nodes, session_memory=state.get("session_memory"))

    if node is not None:
        return _run_node_scope(state, node)

    return _run_entity_scope(state, known_nodes)
