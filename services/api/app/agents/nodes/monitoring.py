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
from ..node_resolver import resolve_node
from ..state import CortexState

logger = logging.getLogger(__name__)

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


def monitoring_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
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
