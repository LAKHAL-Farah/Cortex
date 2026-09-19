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
"""
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ...services.llm_client import LLMConfigError, get_chat_model
from ...services.metrics_collector import collect_metrics
from ..node_resolver import resolve_node, resolve_nodes
from ..state import CortexState

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


def monitoring_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
    multi = resolve_nodes(state["user_query"], known_nodes, session_memory=state.get("session_memory"))
    if multi:
        return _run_multi(state, multi)

    node = resolve_node(state["user_query"], known_nodes, session_memory=state.get("session_memory"))

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
