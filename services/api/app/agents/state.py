"""Shared state passed between LangGraph nodes for the agent orchestrator
(docs/architecture -- "prove the loop" v0.1).

Deliberately minimal: no session memory, no model tiering, no arbitration
across multiple agents yet. Every field here is either set once by the
endpoint before the graph runs, or written by exactly one node.

v0.5 (adr-0007, the resilience layer) adds `failures`: unlike `error`
(a hard stop -- no agent_result at all, e.g. an unresolvable node), a
`FailureRecord` in `failures` means *part* of an agent's evidence-gathering
failed but the agent still produced a usable, honestly-degraded
`agent_result` (see nodes/anomaly.py's Loki-unreachable path) -- compose.py
reads this list to attach a degraded-answer note, and any future agent can
push into it via resilience.get_breaker(...).call(...) without compose.py
needing to know anything agent-specific.

v0.7 (adr-0009, observability & eval) adds three more fields, all written
without any node needing to know about each other's:

- `trace_id` / `trace_events`: see agents/trace.py's module docstring --
  minted by routers/agents.py before the graph runs, appended to by every
  node (trace.traced for router/critic/compose, resilience.guarded_node
  for every wrapped agent), and persisted as one row once the graph
  finishes. Still JSON-serializable, same constraint as everything else
  here.
- `critic_verdict`: written by the new critic node (nodes/critic.py),
  which runs after every agent branch and before compose -- an
  evidence-grounding check on `agent_result["summary"]`, not a new kind of
  failure (an ungrounded claim isn't "this agent's evidence-gathering
  failed", it's "this agent's own narration said something its evidence
  doesn't support"), so it gets its own field rather than folding into
  `failures`.
"""
from typing import Optional, TypedDict

from .resilience import FailureRecord
from .trace import TraceEvent


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


class CriticVerdict(TypedDict):
    """See nodes/critic.py. `status` is "pass" (nothing flagged, including
    the common case of "nothing checkable" -- e.g. a clarify turn with no
    agent_result) or "flagged" (at least one claim in the summary wasn't
    grounded in the evidence the agent actually gathered)."""

    status: str  # "pass" | "flagged"
    checked_sentences: int
    flagged_claims: list[str]


class CortexState(TypedDict):
    user_query: str
    known_nodes: list[KnownNode]

    intent: str
    target_agent: str

    agent_result: Optional[AgentResult]
    final_answer: str

    trace_id: str
    trace_events: list[TraceEvent]
    critic_verdict: Optional[CriticVerdict]

    # Set by monitoring_agent when it can't confidently resolve a node from
    # the query text (ambiguous, unknown hostname, or none mentioned at all),
    # or by the router when its own confidence in the intent classification
    # is too low to act on (see intent_router.py's clarify gate) -- either
    # way, compose_answer treats it the same: no agent ran, show this text
    # instead of crashing on a missing agent_result.
    error: Optional[str]

    # Failed (post-retry) sub-calls that a node recovered from well enough
    # to still produce a degraded agent_result, plus any node-level failure
    # resilience.guarded_node caught. See module docstring above.
    failures: list[FailureRecord]
