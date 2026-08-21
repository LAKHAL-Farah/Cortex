"""The one agent in v0.1. Wraps the existing Prometheus collector
(app/services/metrics_collector.py) -- no LLM involved in the data fetch,
just plain PromQL + arithmetic that's already battle-tested by
GET /api/v1/dashboard. The only new logic here is resolving which node the
question is actually about.
"""
import logging
import re

from ...services.metrics_collector import collect_metrics
from ..state import CortexState, KnownNode

logger = logging.getLogger(__name__)

_HOSTNAME_TOKEN = re.compile(r"[a-z0-9][a-z0-9-]*")


def _resolve_node(query: str, known_nodes: list[KnownNode]) -> KnownNode | None:
    """Matches a hostname mentioned in the query against the real node
    inventory (not a regex-only guess) so "cpu on compute 02" and
    "compute-02 cpu" both resolve the same way. Returns None -- rather than
    guessing -- when zero or more than one node matches, since a wrong
    node's metrics are worse than admitting we're not sure which one was
    meant.
    """
    tokens = set(_HOSTNAME_TOKEN.findall(query))
    # Normalize "compute 02" -> "compute-02" so a space where the hostname
    # has a hyphen still matches.
    normalized_query = "-".join(t for t in re.split(r"\s+", query.strip()) if t)

    matches = [
        node
        for node in known_nodes
        if node["hostname"] in tokens
        or node["hostname"].replace("-", "") in normalized_query.replace("-", "")
        or node["hostname"] in query
    ]
    # de-dupe while preserving order (a node could satisfy more than one
    # condition above)
    seen = set()
    unique_matches = []
    for node in matches:
        if node["hostname"] not in seen:
            seen.add(node["hostname"])
            unique_matches.append(node)

    if len(unique_matches) == 1:
        return unique_matches[0]
    if len(unique_matches) == 0 and len(known_nodes) == 1:
        # Only one node registered at all -- no ambiguity possible even
        # though the query didn't name it explicitly.
        return known_nodes[0]
    return None


def monitoring_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
    node = _resolve_node(state["user_query"], known_nodes)

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

    summary = (
        f"{node['hostname']} ({node['role']}) is currently at "
        f"{metrics['cpu_percent']}% CPU, {metrics['memory_percent']}% RAM, "
        f"{metrics['disk_percent']}% disk -- status: {metrics['status']} "
        f"({metrics['health']})."
    )

    state["agent_result"] = {
        "summary": summary,
        "confidence": 1.0,  # direct Prometheus pull, no inference involved
        "raw_data": metrics,
    }
    state["error"] = None
    return state
