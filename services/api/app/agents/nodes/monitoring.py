"""Monitoring agent -- current/live status questions. Metrics still come
straight from Prometheus (app/services/metrics_collector.py, no LLM
involved in the data fetch, same as v0.1): that data is authoritative and
an LLM has no business inventing or rounding it.

What the LLM *is* used for, added in v0.2:
- Resolving which node the question is about (via node_resolver.py), so a
  partial or misspelled hostname still lands on the right node instead of
  only matching an exact/verbatim mention.
- Turning the raw numbers into a natural-language answer, instead of one
  hand-written f-string that reads identically for every question. If the
  LLM isn't configured or the call fails, this falls back to that same
  f-string -- a missing API key degrades the answer's phrasing, never its
  correctness (the numbers themselves never come from the LLM).

v0.8: both calls above run on the fast tier (services/llm_client.py) --
node resolution is a short classification and this narration is
single-source ("here are 5 numbers, describe them"), neither needs the
reasoning-tier model. Node resolution also gets `state["session_memory"]`
as its last-resort fallback, and this node writes its own resolution back
into `state["resolved_entities"]` so a follow-up question can reuse it
(see node_resolver.py / agents/state.py).

v1.4 adds a third question shape this agent answers, alongside one-node
and several-node metric reads: "which OpenStack services are up/down",
fleet-wide or scoped to one node or one binary (nova-compute,
neutron-l3-agent, ...). This is a live *status* read exactly like the
metrics paths above -- "is nova-compute running right now" -- not a
"how do I check/confirm/fix nova-compute" question, which stays
openstack_expert's territory (see intent_router.py's own note on the
split). The data already exists and is already fresher than anything
this agent would fetch itself: topology_sync.py's periodic pass keeps
every :Service vertex's `state` field Prometheus-reconciled (Phase 4),
so this only ever reads it (`graph_db.fetch_services()`), never
recomputes it -- same "Postgres/graph are sources of truth, an agent
just reads them" shape collect_metrics() already is for node metrics.
Detecting "this question is about services" reuses
openstack_expert._detect_service_binaries/_ALL_SERVICE_BINARIES (the
catalog's own binary list) rather than a second hand-maintained one, plus
a plain "service(s)" keyword -- so a new catalog entry's service_binaries
automatically becomes recognizable here too, with nothing to keep in
sync.
"""
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from ... import graph_db
from ...services.llm_client import LLMConfigError, get_chat_model
from ...services.metrics_collector import collect_metrics
from ..node_resolver import resolve_node, resolve_nodes
from ..state import CortexState
from .openstack_expert import _detect_service_binaries

logger = logging.getLogger(__name__)

# Same "call this out" bar the single-node prompt above uses.
_CONCERN_PERCENT = 90.0
# A gap this wide between the busiest and idlest node of the *same role* is
# worth pointing out as load imbalance.
_IMBALANCE_POINTS = 30.0
_HEALTH_RANK = {"critical": 0, "warning": 1, "healthy": 2}
_FLEET_METRICS = (("cpu_percent", "CPU"), ("memory_percent", "RAM"), ("disk_percent", "Disk"))
_ROW_FIELDS = (
    "node", "role", "instance", "cpu_percent", "memory_percent", "swap_percent",
    "disk_percent", "load1", "uptime", "status", "health",
)

# Service-scope detection: a bare "service(s)" keyword is enough on its
# own ("which services are down"); a named binary counts on its own too
# ("is nova-compute up") and, when present, narrows the answer to just
# that binary. \b avoids matching "serviceable"/"servicing" etc.
_SERVICES_KEYWORD_RE = re.compile(r"\bservices?\b", re.IGNORECASE)
# Prometheus-reconciled `state` vocabulary (see topology_sync.py Phase 4 /
# lib/entities.ts's SERVICE_STATE_LABEL) -- down first, then unreachable,
# then a service the sync hasn't captured a state for yet, then up.
_SERVICE_STATE_RANK = {"down": 0, "unreachable": 1, None: 2, "up": 3}

_SYSTEM_PROMPT = """You are Cortex's monitoring assistant. Answer the user's question about a \
node's current status using ONLY the metrics given below -- never invent or adjust a number. \
Keep it to 1-3 sentences, direct and conversational, and call out anything that looks \
concerning (status not "up", health not "healthy", or any metric above ~90%)."""


def _fallback_summary(node, metrics) -> str:
    return (
        f"{node['hostname']} ({node['role']}) is currently at "
        f"{metrics['cpu_percent']}% CPU, {metrics['memory_percent']}% RAM, "
        f"{metrics['disk_percent']}% disk -- status: {metrics['status']} "
        f"({metrics['health']})."
    )


def _narrate(query: str, node, metrics) -> str:
    try:
        llm = get_chat_model(temperature=0.2, tier="fast")
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Question: {query}\n\n"
                        f"Node: {node['hostname']} (role: {node['role']})\n"
                        f"CPU: {metrics['cpu_percent']}%\n"
                        f"Memory: {metrics['memory_percent']}%\n"
                        f"Disk: {metrics['disk_percent']}%\n"
                        f"Status: {metrics['status']}\n"
                        f"Health: {metrics['health']}"
                    )
                ),
            ]
        )
        text = (response.content or "").strip()
        return text or _fallback_summary(node, metrics)
    except LLMConfigError:
        return _fallback_summary(node, metrics)
    except Exception:
        logger.exception("monitoring_agent: LLM narration failed, using fallback summary")
        return _fallback_summary(node, metrics)


# --------------------------------------------------------------------
# Multi-node (fleet) answers. One collect_metrics() call already returns
# every node, so several nodes cost the same single Prometheus round trip as
# one. Everything numeric -- the table, the aggregates, the insight bullets
# -- is computed here, deterministically; the LLM only gets to narrate over
# those figures (and the answer is complete without it).
# --------------------------------------------------------------------

def _severity_key(row: dict):
    """Sort key: down first, then critical/warning/healthy, then busiest."""
    peak = max(row["cpu_percent"], row["memory_percent"], row["disk_percent"])
    return (row["status"] == "up", _HEALTH_RANK.get(row["health"], 3), -peak)


def _aggregates(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for key, _label in _FLEET_METRICS:
        top = max(rows, key=lambda r: r[key])
        low = min(rows, key=lambda r: r[key])
        out[key] = {
            "avg": round(sum(r[key] for r in rows) / len(rows), 1),
            "max": top[key], "max_node": top["node"],
            "min": low[key], "min_node": low["node"],
        }
    return out


def _fleet_insights(rows: list[dict], aggregates: dict, missing: list[str], evidence: dict) -> list[str]:
    """`evidence` collects every derived figure the sentences below quote
    that isn't already a table/aggregate value (the threshold, an imbalance
    gap), so it lands in raw_data and the critic can ground it."""
    insights: list[str] = []
    down = [r["node"] for r in rows if r["status"] != "up"]
    if down:
        insights.append(f"Down: {', '.join(down)}.")
    hot = [
        f"{r['node']} ({label} {r[key]}%)"
        for r in rows if r["status"] == "up"
        for key, label in _FLEET_METRICS if r[key] >= _CONCERN_PERCENT
    ]
    if hot:
        evidence["concern_percent"] = _CONCERN_PERCENT
        insights.append(f"Above {int(_CONCERN_PERCENT)}%: {', '.join(hot)}.")
    for key, label in _FLEET_METRICS:
        agg = aggregates[key]
        if len(rows) > 1 and agg["max_node"] != agg["min_node"]:
            insights.append(
                f"{label}: highest on {agg['max_node']} ({agg['max']}%), lowest on "
                f"{agg['min_node']} ({agg['min']}%), average {agg['avg']}%."
            )
    by_role: dict[str, list[dict]] = {}
    for r in rows:
        by_role.setdefault(r["role"], []).append(r)
    for role, group in by_role.items():
        if len(group) < 2:
            continue
        busiest = max(group, key=lambda r: r["cpu_percent"])
        idlest = min(group, key=lambda r: r["cpu_percent"])
        gap = round(busiest["cpu_percent"] - idlest["cpu_percent"], 1)
        if gap >= _IMBALANCE_POINTS:
            evidence.setdefault("imbalance_gaps", []).append(
                {"role": role, "busiest": busiest["node"], "idlest": idlest["node"], "gap": gap}
            )
            insights.append(
                f"Uneven {role} load: {busiest['node']} is {gap} points busier on CPU than {idlest['node']}."
            )
    if missing:
        insights.append(f"No live data yet for: {', '.join(missing)}.")
    if not down and not hot and not missing:
        insights.append("Nothing above the concern threshold across these nodes.")
    return insights


def _fleet_table(rows: list[dict]) -> str:
    lines = [
        "| Node | Role | Status | CPU | RAM | Disk | Load (1m) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        status = r["status"] if r["health"] == "healthy" else f"{r['status']} · {r['health']}"
        lines.append(
            f"| {r['node']} | {r['role']} | {status} | {r['cpu_percent']}% | "
            f"{r['memory_percent']}% | {r['disk_percent']}% | {r['load1']} |"
        )
    return "\n".join(lines)


_FLEET_SYSTEM_PROMPT = """You are Cortex's monitoring assistant. You're given the live status of \
several nodes. In 2-4 sentences answer the user's question across them: which node(s) need attention \
and why, and how the rest compare. Use ONLY the figures given -- never invent or adjust a number or \
a node. Do not repeat the whole table."""


def _fallback_fleet_lead(counts: dict, concerning: list[str]) -> str:
    lead = f"{counts['up']} of {counts['total']} nodes are up."
    if concerning:
        return f"{lead} Needs attention: {', '.join(concerning)}."
    return f"{lead} All of them report healthy."


def _narrate_fleet(query: str, rows: list[dict], insights: list[str], counts: dict, concerning: list[str]) -> str:
    fallback = _fallback_fleet_lead(counts, concerning)
    try:
        llm = get_chat_model(temperature=0.2, tier="fast")
        response = llm.invoke(
            [
                SystemMessage(content=_FLEET_SYSTEM_PROMPT),
                HumanMessage(
                    content=f"Question: {query}\n\n{_fleet_table(rows)}\n\nComputed insights:\n"
                    + "\n".join(f"- {i}" for i in insights)
                ),
            ]
        )
        return (response.content or "").strip() or fallback
    except LLMConfigError:
        return fallback
    except Exception:
        logger.exception("monitoring_agent: fleet narration failed, using computed insights")
        return fallback


def _run_multi(state: CortexState, nodes: list) -> CortexState:
    try:
        live_by_instance = {m["instance"]: m for m in collect_metrics()}
    except Exception:
        logger.exception("monitoring_agent: collect_metrics() failed")
        state["error"] = "Couldn't reach Prometheus to fetch live metrics."
        state["agent_result"] = None
        return state

    rows: list[dict] = []
    missing: list[str] = []
    for node in nodes:
        metrics = live_by_instance.get(node["instance"])
        if metrics is None:
            missing.append(node["hostname"])
            continue
        row = {k: metrics.get(k) for k in _ROW_FIELDS}
        row["node"], row["role"] = node["hostname"], node["role"]
        rows.append(row)

    if not rows:
        state["error"] = (
            f"None of the {len(nodes)} nodes you asked about have been scraped by Prometheus yet."
        )
        state["agent_result"] = None
        return state

    rows.sort(key=_severity_key)
    aggregates = _aggregates(rows)
    evidence: dict = {}
    insights = _fleet_insights(rows, aggregates, missing, evidence)
    counts = {
        "total": len(rows),
        "up": sum(1 for r in rows if r["status"] == "up"),
        "down": sum(1 for r in rows if r["status"] != "up"),
        "healthy": sum(1 for r in rows if r["health"] == "healthy"),
        "warning": sum(1 for r in rows if r["health"] == "warning"),
        "critical": sum(1 for r in rows if r["health"] == "critical"),
    }
    concerning = [
        r["node"] for r in rows
        if r["status"] != "up" or r["health"] != "healthy"
    ]

    narration = _narrate_fleet(state["user_query"], rows, insights, counts, concerning)
    bullets = "\n".join(f"- {i}" for i in insights)
    summary = (
        f"### Fleet status — {counts['total']} nodes\n\n"
        f"{narration}\n\n"
        f"{_fleet_table(rows)}\n\n"
        f"#### Insights\n{bullets}"
    )

    state["agent_result"] = {
        "summary": summary,
        "confidence": 1.0,  # direct Prometheus pull, same as the single-node read
        "raw_data": {
            "scope": "multi",
            "nodes": rows,
            "missing": missing,
            "counts": counts,
            "aggregates": aggregates,
            "concerning": concerning,
            "insights": insights,
            **evidence,
        },
    }
    state["error"] = None
    resolved = state.setdefault("resolved_entities", {})
    resolved["last_nodes"] = list(nodes)
    resolved["last_agent"] = "monitoring"
    return state


def _mentions_services(query: str) -> tuple[bool, list[str]]:
    """(is this a services question, which binaries -- if any -- it named).
    Reuses openstack_expert's own binary list/detector (see module
    docstring) rather than a second hand-maintained one."""
    binaries = _detect_service_binaries(query)
    return bool(binaries) or bool(_SERVICES_KEYWORD_RE.search(query)), binaries


def _service_row(service: dict) -> dict:
    """`node_id` IS the hostname (topology_sync.py sets every :Node
    vertex's id to its hostname, the same identifier known_nodes/
    resolve_node already use) -- so this needs no separate id->hostname
    lookup to line a service up with a resolved node. `state` is Phase
    4's Prometheus-reconciled up/down/unreachable; `openstack_state` (the
    raw OpenStack-reported value, pre-reconciliation) is only a fallback
    for the rare row a reconciliation pass hasn't touched yet."""
    return {
        "binary": service.get("binary"),
        "host": service.get("host"),
        "node": service.get("node_id"),
        "zone": service.get("zone"),
        "backend": service.get("backend"),
        "source": service.get("source"),
        "status": service.get("status"),
        "state": service.get("state") or service.get("openstack_state"),
    }


def _service_label(r: dict) -> str:
    return f"{r['binary'] or 'unknown service'} on {r['node'] or r['host'] or 'unknown host'}"


def _services_table(rows: list[dict]) -> str:
    lines = ["| Service | Node | Zone | State |", "| --- | --- | --- | --- |"]
    for r in rows:
        lines.append(
            f"| {r['binary'] or '?'} | {r['node'] or r['host'] or '?'} | "
            f"{r['zone'] or '—'} | {r['state'] or 'unknown'} |"
        )
    return "\n".join(lines)


_SERVICES_SYSTEM_PROMPT = """You are Cortex's monitoring assistant. You're given the live up/down state of \
OpenStack services (Nova/Cinder/Neutron binaries) across the fleet, or scoped to one node or one service. In \
1-3 sentences answer the user's question: which service(s) need attention and where. Use ONLY the rows and \
insights given -- never invent a service, a node, or a state."""


def _fallback_services_lead(counts: dict, scope_label: str) -> str:
    lead = f"{counts['up']} of {counts['total']} known OpenStack service instance(s){scope_label} report up."
    trouble = counts["down"] + counts["unreachable"]
    return f"{lead} {trouble} need attention." if trouble else f"{lead} Nothing else reporting trouble."


def _narrate_services(query: str, rows: list[dict], insights: list[str], counts: dict, scope_label: str) -> str:
    fallback = _fallback_services_lead(counts, scope_label)
    try:
        llm = get_chat_model(temperature=0.2, tier="fast")
        response = llm.invoke(
            [
                SystemMessage(content=_SERVICES_SYSTEM_PROMPT),
                HumanMessage(
                    content=f"Question: {query}\n\n{_services_table(rows)}\n\nComputed insights:\n"
                    + "\n".join(f"- {i}" for i in insights)
                ),
            ]
        )
        return (response.content or "").strip() or fallback
    except LLMConfigError:
        return fallback
    except Exception:
        logger.exception("monitoring_agent: services narration failed, using computed lead")
        return fallback


def _run_services(state: CortexState, known_nodes: list, node, binaries: list[str]) -> CortexState:
    """`graph_db.fetch_services()` is a plain, already-existing read of
    every :Service vertex (same one GET /api/v1/topology/services -- the
    Services page -- already calls); this only ever reads it, same
    "graph is a derived read-model, never recomputed here" shape
    collect_metrics() already is for node metrics (see module
    docstring)."""
    try:
        raw_services = graph_db.fetch_services()
    except Exception:
        logger.exception("monitoring_agent: graph_db.fetch_services() failed")
        state["error"] = "Couldn't reach the topology graph to fetch service status."
        state["agent_result"] = None
        return state

    rows = [_service_row(s) for s in raw_services]
    if node is not None:
        rows = [r for r in rows if r["node"] == node["hostname"]]
    if binaries:
        rows = [r for r in rows if r["binary"] in binaries]

    if not rows:
        scope = f" on {node['hostname']}" if node else ""
        which = f" ({', '.join(binaries)})" if binaries else ""
        state["error"] = f"No known OpenStack service{which}{scope} in the topology graph yet."
        state["agent_result"] = None
        return state

    rows.sort(key=lambda r: (_SERVICE_STATE_RANK.get(r["state"], 2), r["binary"] or "", r["node"] or ""))

    down = [r for r in rows if r["state"] == "down"]
    unreachable = [r for r in rows if r["state"] == "unreachable"]
    unknown = [r for r in rows if r["state"] not in ("up", "down", "unreachable")]
    counts = {
        "total": len(rows),
        "up": sum(1 for r in rows if r["state"] == "up"),
        "down": len(down),
        "unreachable": len(unreachable),
        "unknown": len(unknown),
    }

    insights: list[str] = []
    if down:
        insights.append(f"Down: {', '.join(_service_label(r) for r in down)}.")
    if unreachable:
        insights.append(f"Unreachable: {', '.join(_service_label(r) for r in unreachable)}.")
    if unknown:
        insights.append(f"No live state yet: {', '.join(_service_label(r) for r in unknown)}.")
    if not down and not unreachable and not unknown:
        insights.append("Nothing reporting down or unreachable.")

    scope_label = f" on {node['hostname']}" if node else " fleet-wide"
    narration = _narrate_services(state["user_query"], rows, insights, counts, scope_label)
    bullets = "\n".join(f"- {i}" for i in insights)
    summary = (
        f"### OpenStack services{scope_label} — {counts['total']} known\n\n"
        f"{narration}\n\n"
        f"{_services_table(rows)}\n\n"
        f"#### Insights\n{bullets}"
    )

    state["agent_result"] = {
        "summary": summary,
        "confidence": 1.0,  # direct graph read of Prometheus-reconciled state, no inference in the numbers
        "raw_data": {
            "scope": "services",
            "services": rows,
            "counts": counts,
            "insights": insights,
        },
    }
    state["error"] = None
    resolved = state.setdefault("resolved_entities", {})
    if node is not None:
        resolved["last_node"] = node
    resolved["last_agent"] = "monitoring"
    return state


def monitoring_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
    query = state["user_query"]

    is_services_question, binaries = _mentions_services(query)
    if is_services_question:
        # A node named alongside "services"/a binary scopes the answer to
        # just that host ("are the services on compute-02 healthy");
        # resolve_node returning None just means "fleet-wide" here, not a
        # failure the way it is for the metrics paths below -- a services
        # question never *requires* a node the way "what's the CPU on X"
        # does, so there's no error branch for "couldn't tell which node".
        node = resolve_node(query, known_nodes, session_memory=state.get("session_memory"))
        return _run_services(state, known_nodes, node, binaries)

    multi = resolve_nodes(query, known_nodes, session_memory=state.get("session_memory"))
    if multi:
        return _run_multi(state, multi)

    node = resolve_node(query, known_nodes, session_memory=state.get("session_memory"))

    if node is None:
        available = ", ".join(n["hostname"] for n in known_nodes) or "no nodes registered"
        state["error"] = (
            f"I couldn't tell which node you meant. Known nodes: {available}."
        )
        state["agent_result"] = None
        return state

    try:
        live_by_instance = {m["instance"]: m for m in collect_metrics()}
    except Exception:
        logger.exception("monitoring_agent: collect_metrics() failed")
        state["error"] = "Couldn't reach Prometheus to fetch live metrics."
        state["agent_result"] = None
        return state

    metrics = live_by_instance.get(node["instance"])
    if metrics is None:
        state["error"] = (
            f"{node['hostname']} is registered but Prometheus hasn't scraped it yet "
            "(no data at its instance target)."
        )
        state["agent_result"] = None
        return state

    summary = _narrate(state["user_query"], node, metrics)

    state["agent_result"] = {
        "summary": summary,
        "confidence": 1.0,  # direct Prometheus pull, no inference involved in the numbers
        "raw_data": metrics,
    }
    state["error"] = None
    state.setdefault("resolved_entities", {})["last_node"] = node
    state["resolved_entities"]["last_agent"] = "monitoring"
    return state
