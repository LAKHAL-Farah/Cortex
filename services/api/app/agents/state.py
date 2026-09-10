"""Shared state passed between LangGraph nodes for the agent orchestrator
(docs/architecture -- "prove the loop" v0.1).

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

v0.8 (efficiency & scale prep) adds the fields below. Model tiering (see
services/llm_client.py) doesn't need a state field -- it's a per-call-site
choice, not something that flows through the graph. The other two do:

- `session_memory` / `resolved_entities`: the alternative to re-sending a
  full raw conversation transcript into the graph on every turn (which
  knowledge_chat.py's ChatQuery.history already does for the separate
  Copilot RAG chat, and which is exactly the pattern this deliberately
  does NOT repeat here -- replaying 20 turns of text into the router on
  every message doesn't scale in cost or latency as conversation volume
  grows). `session_memory` is read-only input: whatever compact set of
  resolved entities (last node, last metric, last agent) routers/agents.py
  loaded for this conversation before the graph ran. `resolved_entities`
  is the *output* side -- what this turn resolved, for routers/agents.py
  to merge back into that same compact record after the graph finishes.
  Node resolution (node_resolver.py) and intent routing consult
  `session_memory` as their last, cheapest-to-try fallback tier, which is
  what lets a bare follow-up like "what about now?" resolve against
  "the node/agent we were just talking about" instead of re-asking.
- `agent_results` / `incident_scope`: dynamic fan-out support for
  nodes/anomaly.py's multi-node incident investigation (see graph.py's
  anomaly_dispatch -> Send("anomaly_investigate", ...) x N ->
  anomaly_arbitrate wiring). `agent_results` is `Annotated` with a list
  reducer since it's the one field genuinely written *concurrently* --
  every parallel Send-invoked branch appends its own `IncidentFinding`
  to it in the same superstep, and LangGraph needs to know to concatenate
  those instead of one overwriting another. `incident_scope` is plain
  (written once, sequentially, by anomaly_dispatch before the fan-out
  starts) -- the list of nodes that dispatch resolved this turn's
  question against, scoped by the Living Model (the topology graph's
  current `known_nodes` plus whichever of them currently have an open
  AnomalyFlag) rather than any fixed/hardcoded list.
"""
from typing import Annotated, Optional, TypedDict

from .resilience import FailureRecord
from .trace import TraceEvent


def _concat(left: list, right: list) -> list:
    """Reducer for CortexState's one concurrently-written field
    (`agent_results`).

    Deliberately dedupes by hostname (first-seen wins) rather than doing
    plain concatenation. The reason isn't the concurrent Send fan-out
    itself -- LangGraph's Pregel model refills *every* channel's current
    persisted value into each subsequent node's input regardless of what
    that node returns, which means anomaly_arbitrate reading and then
    "not returning" this field doesn't remove it: the very next node
    (critic, then compose -- there is always at least one full-state-
    returning node after arbitrate) still sees the already-merged list in
    its own input and, since every node here follows the "mutate a copy
    of state, return the whole thing" convention, ends up re-contributing
    that exact same list right back to this channel on its own next turn.
    A plain-concatenation reducer would then add it again at every single
    one of those steps, silently doubling the fan-out's findings once per
    downstream node. Deduping by hostname makes that re-contribution a
    no-op (every hostname it contains is already present) while still
    correctly merging the genuinely-concurrent, genuinely-distinct
    findings from the fan-out's actual parallel branches.
    """
    left = left or []
    right = right or []
    seen = {finding["hostname"] for finding in left}
    return left + [finding for finding in right if finding["hostname"] not in seen]


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


class IncidentFinding(TypedDict):
    """One node's worth of anomaly-investigation output, gathered by a
    single Send("anomaly_investigate", ...) branch (see graph.py). Kept
    separate from AgentResult (rather than just a list[AgentResult]) so
    anomaly_arbitrate doesn't have to dig `hostname` back out of
    `agent_result["raw_data"]` to rank/report per-node."""

    hostname: str
    agent_result: AgentResult
    # Any FailureRecord this node's own evidence-gathering hit (e.g. a
    # Loki timeout) -- embedded here rather than written straight to
    # CortexState["failures"] because multiple investigate branches run
    # concurrently and "failures" has no reducer; anomaly_arbitrate (which
    # runs sequentially, after the fan-out has fully joined) is what
    # copies these into state["failures"] for compose.py to read, same as
    # every other agent already does.
    failures: list[FailureRecord]


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

    # v0.8 -- session memory (see module docstring). `session_memory` is
    # whatever routers/agents.py loaded for this conversation before the
    # graph ran (empty dict for a fresh/unscoped turn); `resolved_entities`
    # is this turn's contribution back to it, merged in by the endpoint
    # after the graph finishes.
    session_memory: dict
    resolved_entities: dict

    # v0.8 -- dynamic incident fan-out (see module docstring and graph.py).
    incident_scope: list[KnownNode]
    agent_results: Annotated[list[IncidentFinding], _concat]
