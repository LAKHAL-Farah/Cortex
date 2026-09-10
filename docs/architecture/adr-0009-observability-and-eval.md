# ADR-0009: Observability & eval — tracing, a critic node, and two golden sets

**Status:** Accepted
**Related code:** `services/api/app/agents/trace.py`,
`services/api/app/agents/nodes/critic.py`, `services/api/app/agents/graph.py`,
`services/api/app/agents/compose.py`, `services/api/app/agents/resilience.py`,
`services/api/app/agents/state.py`, `services/api/app/models.py`
(`AgentTrace`), `services/api/app/crud.py`, `services/api/app/routers/agents.py`,
`services/api/tests/golden/`, `services/api/scripts/eval_router_golden_set.py`,
`.github/workflows/ci.yml`
**Related docs:** ADR-0007 (resilience layer), ADR-0008 (OpenStack Expert
Agent — the "auditable, not LLM" scoring-function argument this ADR reuses
for the critic node)

## Context

Through v0.6, "did this turn work" was answerable only by re-running it
with a debugger attached or reading application logs by hand — there was
no per-turn record of what the router picked, which agent(s) ran, what
they found, or whether anything degraded, all in one place. And there was
no way to know whether a change to the router's prompt or an agent's logic
made routing or answer quality better or worse without eyeballing a
handful of manual test questions.

v0.1 could get away with this (one agent, nothing to route between). By
v0.6 there are five routable outcomes (`monitoring`/`prediction`/`rag`/
`anomaly`/`openstack_expert`, plus the `clarify` non-route from ADR-0007)
and a two-agent chain (anomaly → openstack_expert). "Guessing whether a
prompt change helped" stops being viable at that point — the system needs
a way to *measure* it.

### A scoping note on what this ADR does and doesn't build

The v0.7 plan this was written against describes a more advanced,
multi-agent-per-turn architecture than what exists on this branch today:
it talks about a `target_agents` *list*, an `execution_mode`
(parallel/sequential), a `best_theory` an arbitration step picks between
competing agent outputs, and a `critic_verdict` gating that arbitration.

None of that exists yet. `state.py`'s `CortexState.target_agent` is a
single string; `compose.py`'s own docstring says this outright: *"there's
exactly one agent, so there's nothing to aggregate or arbitrate between
yet... once a second investigating agent exists... that kind of
arbitration belongs here."* Building tracing and a critic around fields
that don't exist would mean inventing a parallel, disconnected data model
nothing else in the codebase populates.

So this ADR keeps the underlying architecture from v0.6 exactly as it is,
and maps each piece of the v0.7 goal onto what's real:

| v0.7 concept | This ADR's answer | Why |
|---|---|---|
| Structured tracing, one record per turn | `trace.py` + `AgentTrace` (below) | Directly buildable; no architecture change needed. |
| "Critic node... evidence-grounding check before an answer ships" | `nodes/critic.py`, a new deterministic node before `compose` | Applies to today's single `agent_result` — grounding doesn't require a second competing agent to check against. |
| Routing golden set | `tests/golden/routing_golden_set.json` | Router already exists and already routes to one of 5 outcomes; directly testable today. |
| Arbitration golden set with known-ground-truth root cause | `tests/golden/arbitration_golden_set.json`, driving `app/services/rca_suggester.find_causal_suggestions` | This is the one piece of the codebase that already does what "arbitration" means (given several concurrent anomalies on a graph, decide which one is the likely cause) — it predates this ADR (used by `/api/v1/anomalies/rca`, see `adr-0003`-adjacent code) and already has ground truth in the form of its own directionality table. It is not wired into the LangGraph orchestrator's per-turn flow (it's a separate, on-demand endpoint over historical `AnomalyFlag` rows) — this ADR does not change that; it only gives that existing engine the golden-set discipline the v0.7 goal asks for. |
| `best_theory` matching ground truth | `find_causal_suggestions`'s own `cause`/`effect`/`relationship` output | Same reasoning as above — this is the system's actual "which theory is right" engine today. |

If/when a genuine multi-agent-per-turn design lands (`target_agents`,
parallel execution, real arbitration between competing in-turn theories),
it should extend `trace.py` and the `AgentTrace` schema rather than
replace them — the shape (`trace_id`, ordered `TraceEvent` list) doesn't
assume single-agent, only the current graph topology does.

## Decision

### 1. Tracing (`trace.py`, `state.py`, `resilience.py`, `routers/agents.py`, `models.AgentTrace`)

`state.py` already holds every rule this needed to follow: state must
stay JSON-serializable, and the graph itself never touches the DB session
(`known_nodes` already worked this way). So tracing is built the same way:

- `routers/agents.py` mints a `trace_id` (`trace.new_trace_id()`, a plain
  `uuid4().hex`) before calling `app_graph.invoke(...)`, and seeds
  `state["trace_events"] = []`.
- Every node appends exactly one `trace.TraceEvent` (`node`, `status`,
  `duration_ms`, `timestamp`, a small `detail` dict) to that list as it
  runs:
  - Agent nodes already wrapped in `resilience.guarded_node` (ADR-0007)
    get this for free — that wrapper already measures wall time and knows
    ok-vs-failed for every call, which is exactly what a `TraceEvent`
    needs, so `record_step` is called from inside it directly.
  - `router`, `critic`, and `compose` aren't wrapped in `guarded_node`
    (they're synchronous and don't call an external, potentially-hanging
    dependency on their own account — nothing to circuit-break) but still
    need a trace event, so they're wrapped in the new, much smaller
    `trace.traced(name)` decorator instead.
- After `app_graph.invoke(...)` returns, `routers/agents.py` persists the
  whole thing as one `models.AgentTrace` row, keyed by `trace_id` as the
  primary key itself (not a separate generated id) — this is the
  "lightweight custom trace store in Postgres" option the v0.7 goal
  offers as an alternative to a vendor product (LangSmith): one
  append-only row per turn, no separate spans/traces/services schema,
  because a single-agent-per-turn graph doesn't need one yet.
- `GET /api/v1/agents/trace/{trace_id}` is a direct primary-key lookup
  returning the full ordered step list — "why did it say that" is now
  exactly the lookup the v0.7 goal describes, not an investigation.
- `GET /api/v1/agents/stats?hours=N` gives the 6.3 cost/latency rollup
  (invocations, per-agent count/avg latency, degraded rate, critic-flagged
  rate) as a handful of aggregate SQL queries (`crud.agent_trace_stats`)
  over `AgentTrace` — no separate dashboard service.

**Why not fold this into `state["failures"]`?** A `FailureRecord`
(ADR-0007) means "something broke, but we recovered with a labeled
degraded answer." A `TraceEvent` records *what happened*, breakage or
not — a clean, fast, fully-successful run still gets a full trace, which
is the point. They're separate lists for that reason, though a trace's
`detail` does surface `state["failures"]` when present so a trace itself
tells the whole story.

### 2. Critic node (`nodes/critic.py`)

A new node runs after every agent branch converges and before `compose`
(see graph.py changes below), checking one thing: does `agent_result
["summary"]` claim anything the agent's own gathered evidence doesn't
support?

**Deliberately not an LLM call.** ADR-0008 already made this exact
argument for the OpenStack Expert Agent's symptom matcher — a fixed,
inspectable scoring function you can unit test, not "ask a model whether
this seems right," because a judgment that gates what ships needs to be
exact, not merely plausible-sounding. An LLM critic judging an LLM's
output shares the same failure surface as the thing it's checking; a
critic built from two fixed, testable rules doesn't:

1. **Numeric grounding** — any monitoring/anomaly/prediction-style
   narration is over real numbers already sitting in `raw_data`. Every
   number the summary states must appear (within ±1.0 rounding tolerance)
   somewhere in `raw_data` or the user's own question. A number that
   doesn't is exactly the "the model said 94% when the read was 61%"
   failure mode a bare metric narration has no business producing.
2. **Lexical grounding** — for `rag_agent`, the one node whose summary is
   genuine free-text generation over retrieved documents rather than
   narration of numbers it was handed: a sentence whose content words
   barely overlap with the retrieved chunk text (now carried in
   `raw_data["sources"][i]["text_snippet"]`, an additive field added to
   `nodes/rag.py` for exactly this) is a sentence the model likely
   produced from its own general knowledge, not what was actually found.

Both checks are skip-when-nothing-to-check-against by design (no numeric
evidence at all, or no chunks retrieved) — this catches ungrounded
*claims*, it doesn't punish an agent for having little evidence, which is
already `rag_agent`'s own 0.3-confidence "no context" path's job to
signal.

A "flagged" verdict doesn't discard or rewrite the answer — `compose.py`
prepends a caution note (same shape as ADR-0007's degraded-answer note)
and caps confidence at 0.4, the same "degrade honestly, don't hide or
crash" philosophy applied to a different kind of gap. This is deliberate:
the checks are heuristic enough that a false positive is possible, and a
caveat costs far less than discarding an otherwise-correct finding over
one flagged sentence.

`graph.py` change: every branch that used to go straight to `compose` now
goes to the new `critic` node first (`critic` always continues to
`compose`; it's a pass-through convergence point, never a dead end). This
keeps the check in exactly one place regardless of which of the (now six)
branches produced the final `agent_result`, including the chained
anomaly→openstack_expert path from v0.6.

### 3. Golden sets

**Routing** (`tests/golden/routing_golden_set.json`, 40 questions): every
entry's `source` field says where it actually came from —
`test_suite` (a query string literally already exercised somewhere in the
v0.1–v0.6 test suite: `"how is compute-02 doing"`, `"is nova-compute
actually running?"`, `"what about that thing"` for the clarify path,
etc.), `router_prompt_example` (a full question written from one of the
worked examples already embedded in `intent_router.py`'s own
`_SYSTEM_PROMPT` — those examples were themselves written from real
routing confusions seen while building v0.4–v0.5), or `extrapolated` (a
natural variation added only to give each of the 5 agents + `clarify`
enough coverage, never a new invented category). Nothing here was invented
from a blank page, per the v0.7 goal's explicit ask.

Gated by `scripts/eval_router_golden_set.py`, run in CI
(`.github/workflows/ci.yml`) whenever `NVIDIA_API_KEY` is available as a
secret — it has to call the real classifier the router prompt is actually
sent to; mocking the LLM's response the way `test_intent_router.py`'s
unit tests do would only assert the mock agrees with itself, not that the
prompt still works. `--write-baseline` records
`tests/golden/routing_baseline.json`; normal runs fail (exit 1) if
accuracy drops more than one question (2.5%) below that baseline. Without
the secret, the script prints a warning and exits 0 rather than silently
returning a meaningless number (`route()`'s own graceful DEFAULT_AGENT
fallback with no key would otherwise make every non-monitoring question
"fail" for a reason that has nothing to do with the router prompt).
`tests/test_routing_golden_set_shape.py` validates the golden set's own
shape (size, valid targets, no dupes) in the normal `pytest` run, with no
key required.

**Arbitration** (`tests/golden/arbitration_golden_set.json`, 13
scenarios): drives `rca_suggester.find_causal_suggestions` — see the
context table above for why this, not a new concept — reusing the exact
fixture shapes `test_rca_suggester.py` already established
(`_FakeDriver`/`_FakeSession` answering `graph_db.fetch_vertex_detail`,
real `AnomalyFlag` rows in an in-memory SQLite session). Scenarios 1–9 are
the same demo topology and hostnames that unit test already pins down
(reused, not reinvented); scenarios 10–13 are new synthetic incidents
(a 3-vertex causal chain, two independent concurrent pairs, a
bidirectional-looking pair that must still dedupe to one suggestion)
built from the same already-implemented, already-real `_DIRECTION` table.
Fully offline — no LLM, no external service — so it runs in every
`pytest` invocation, gating every change to `rca_suggester.py`'s logic
regardless of whether a secret is configured.

### 4. Tests

- `tests/test_critic.py` — unit tests for both grounding checks,
  including the DoD's literal scenario: a clean, would-pass summary with
  one deliberately fabricated sentence spliced in (a fake CPU spike
  number; a fake claim about what a service does), asserting the critic
  flags exactly that sentence and nothing else.
- `tests/test_trace.py` — end-to-end through the compiled graph (same
  convention as `test_graph_integration.py`): confirms `trace_id` round-
  trips, every node visited leaves a `TraceEvent` in actual execution
  order, and a sub-call failing (Loki down) still records the *wrapping*
  node's event as `"ok"` (it degraded, it didn't fail — same distinction
  ADR-0007 draws for `state["failures"]`).
- `tests/test_arbitration_golden_set.py` — parametrized over all 13
  scenarios, plus a floor-count test so the golden set can't silently
  shrink below the DoD's 10-scenario minimum.

## Consequences

- **Every orchestrator turn now writes one row to Postgres.** This is a
  new, unbounded-growth table (`agent_traces`) — no retention/pruning
  policy exists yet. Fine at current volume; worth a TTL or archival job
  before this becomes a real production system with sustained traffic.
- **The critic node adds a small, fixed amount of CPU work to every
  turn** (regex over the summary + a raw_data walk) — negligible next to
  an LLM call, but it does mean every turn now does slightly more than
  before even when nothing is wrong.
- **A flagged critic verdict changes user-visible output** (a caution
  note, a lower reported confidence) for any turn where either grounding
  check fires — including, potentially, a false positive on an unusually
  phrased but perfectly accurate summary. The threshold constants
  (`_NUMBER_TOLERANCE`, `_LEXICAL_OVERLAP_THRESHOLD`) are hand-picked with
  no usage data behind them yet (same caveat ADR-0007 notes about
  `ROUTER_CLARIFY_THRESHOLD` when it shipped) — worth revisiting once
  real critic-flagged traces exist to look at via `GET /agents/stats`.
- **The routing golden-set CI gate is inert until `NVIDIA_API_KEY` is
  added as a repo secret.** Until then it's present and wired but only
  ever prints a skip warning — it doesn't yet provide the actual
  regression protection the v0.7 DoD asks for.
- **`AgentOrchestrateResponse` gained `trace_id` (always present) and
  `critic_verdict` (present whenever an agent actually ran)** — additive,
  same pattern as ADR-0007's `degraded: bool` field.

## Revisit when

- A genuine multi-agent-per-turn design lands (the `target_agents`/
  `execution_mode`/`best_theory` architecture the original v0.7 plan
  assumed) — at that point `compose.py`'s own arbitration TODO gets
  resolved for real, and this ADR's critic node should be checked against
  *each* competing agent's output before arbitration picks between them,
  not just the single winner's.
- The critic's thresholds get tuned against real flagged-vs-not-flagged
  data from `GET /api/v1/agents/stats`'s `critic_flagged_rate` once
  there's enough production traffic to look at.
- `agent_traces` needs a retention policy — revisit once storage growth is
  actually a concern, not preemptively.
- `rca_suggester.find_causal_suggestions` ever gets wired into the live
  per-turn graph (rather than staying a separate on-demand endpoint) — at
  that point the arbitration golden set should be re-pointed at whatever
  node calls it in-graph, the way the routing golden set already targets
  `intent_router.route()` directly.
