"""Shared state passed between LangGraph nodes for the agent orchestrator
(docs/architecture -- "prove the loop" v0.1).

Deliberately minimal: no session memory, no model tiering, no arbitration
across multiple agents yet. Every field here is either set once by the
endpoint before the graph runs, or written by exactly one node.
"""
from typing import Optional, TypedDict


class KnownNode(TypedDict):
    """One row from the `nodes` table, trimmed to what the monitoring agent
    needs to resolve "compute-02" in a question to a Prometheus instance
    label. Built by routers/agents.py from crud.list_nodes() -- the graph
    itself never touches the DB session (state must stay JSON-serializable
    and nodes must stay side-effect-free / easy to unit test)."""

    hostname: str
    role: str
    instance: str  # "{ip_address}:{exporter_port}", matches metrics_collector's keys


class AgentResult(TypedDict):
    summary: str
    confidence: float
    raw_data: dict


class CortexState(TypedDict):
    user_query: str
    known_nodes: list[KnownNode]

    intent: str
    target_agent: str

    agent_result: Optional[AgentResult]
    final_answer: str

    # Set by monitoring_agent when it can't confidently resolve a node from
    # the query text (ambiguous, unknown hostname, or none mentioned at all)
    # so compose_answer can produce a helpful message instead of crashing on
    # a missing agent_result.
    error: Optional[str]
