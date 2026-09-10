# ADR-0007: Resilience layer — circuit breakers, degraded answers, and a clarification gate

**Status:** Accepted
**Related code:** `services/api/app/agents/resilience.py`,
`services/api/app/agents/graph.py`, `services/api/app/agents/compose.py`,
`services/api/app/agents/intent_router.py`,
`services/api/app/agents/nodes/anomaly.py`
**Related docs:** none yet — this is the first ADR for the agentic layer
itself (v0.1–v0.4 shipped without one; see the docstrings in `app/agents/`
for that history).

## Context

By v0.4 the agentic layer had four agents (monitoring, prediction, rag,
anomaly), each handling its own *known* failure modes inline — an
unresolvable hostname, an unreachable Prometheus, a missing NVIDIA API
key — by catching a specific exception and writing a hand-written fallback
into `CortexState`. That pattern works well for a call that fails
promptly, but it left three real gaps, all of which get worse, not better,
once more agents are added (v0.6+ is planned to add a Security Agent and
others):

1. **Nothing bounded how long a call could take.** `loki_client.py` and
   `prometheus_client.py` pass `timeout=` to `requests`, but nothing
   wrapped an LLM invocation (`ChatNVIDIA.invoke` / `with_structured_
   output(...).invoke`) with any timeout at all. A hung socket read or a
   NIM endpoint that accepts a connection but never responds would block
   that request indefinitely — "fails safely" has to include "doesn't
   hang", and nothing did that.
2. **A degraded finding and a clean finding looked identical downstream.**
   `nodes/anomaly.py`'s `_check_logs` already caught a Loki failure and
   returned `{"has_signal": False, ...}` — but that's the exact same shape
   as "Loki answered and there were genuinely no correlated log lines".
   `compose.py` (and the confidence score) had no way to tell "we don't
   know" from "we checked, and it's clean", so a Loki outage would silently
   present as ordinary evidence rather than an honestly-labeled gap.
3. **The router only ever guessed.** `intent_router.py`'s prompt literally
   said "if genuinely ambiguous, default to monitoring" — an ambiguous
   question got routed with the same confidence as an unambiguous one,
   with no mechanism for the system to say "I'm not sure, which did you
   mean?" instead of picking an agent and hoping.

The task (v0.5, before v0.6 adds more agents) is to close these three gaps
once, generically, rather than patching each agent's own try/except and
having every future agent reinvent the same handling.

## Decisions

### 1. One shared circuit breaker (`app/agents/resilience.py`), not a per-agent pattern

`CircuitBreaker` wraps an arbitrary callable with:

- **A hard wall-clock timeout**, enforced by running the call in a shared
  `ThreadPoolExecutor` and bounding it with `future.result(timeout=...)`.
  This was chosen over relying on each callee to time out on its own
  because that's exactly the assumption gap #1 above exposes — a thread-
  pool-enforced budget bounds *any* callable, cooperative or not, without
  needing `ChatNVIDIA` (or any future dependency) to implement its own
  timeout correctly.
- **One retry** before giving up — a single transient blip is common
  enough to be worth one immediate re-attempt, and no more: retrying
  indefinitely on a genuinely dead dependency just moves the hang
  problem instead of solving it.
- **A `FailureRecord` on final failure**, returned in a `CallResult`
  rather than raised — every call site gets `ok`/`value`/`failure` to
  branch on explicitly, instead of a bare exception it would need its own
  try/except to interpret.
- **Per-call-site circuit state** (`closed` → `open` after
  `failure_threshold` consecutive failures → `half_open` after
  `reset_after_seconds` → `closed` again on a successful trial). A
  breaker for a confirmed-dead dependency short-circuits (fails
  immediately, no real call attempted) instead of spending a full timeout
  budget on every single question while it's down — this is what turns
  "kill Loki mid-demo" from "every anomaly question waits out a timeout"
  into "the second and later questions fail fast".

Breakers are held in a module-level registry (`get_breaker(name)`) keyed
by name, not constructed fresh per call — a breaker that reset every call
could never actually open. This means breaker state is process-global,
which is deliberate (see Consequences) and is why tests get an autouse
`reset_all_breakers()` fixture (`tests/conftest.py`).

**Two application points, chosen per the shape of the failure they guard
against:**

- **`guarded_node(name, timeout_seconds)`** wraps a whole graph node
  (`graph.py` applies this to all four agents) as the outer safety net for
  *anything* that node doesn't already handle — an unexpected exception,
  or a hang. It runs the node against a **copy** of state; since a timed-
  out call's thread isn't killed (Python has no public API to interrupt a
  running thread) and may keep executing in the background, using a copy
  means a straggler can never write into the state the graph actually
  uses. On failure it sets `agent_result = None` and a plain apology into
  `error` — this is a "the whole finding is unavailable" failure, not a
  partial one.
- **A breaker used directly inside a node**, for a sub-call whose failure
  the node can meaningfully route around rather than losing the whole
  turn over. `nodes/anomaly.py`'s `_check_logs` is the first example
  (`get_breaker("anomaly.loki", ...)`): a failed log-check still lets the
  node produce a metric-only finding, so the node calls the breaker
  itself, tags the result `degraded: True`, and keeps going — see
  decision 2.

Both share the same `FailureRecord` shape, which is what lets decision 2
be generic instead of anomaly-specific.

### 2. Degraded-answer handling lives in `compose.py`, driven by `CortexState["failures"]`, not in each agent's own narrative

A new state field, `failures: list[FailureRecord]`, is distinct from the
existing `error` field:

- `error` means **no agent_result at all** — nothing to show but the
  error/clarifying text itself (unresolvable node, LLM not configured,
  now also "confidence too low to route", see decision 3).
- `failures` means **an agent still produced a usable result**, but part
  of its evidence-gathering failed along the way (`nodes/anomaly.py`'s
  Loki-unreachable path is the only producer of this today).

`compose.py` — already the graph's single convergence point before `END`
— reads `failures` generically: if it's non-empty, it prefixes the
answer with a note built from `FailureRecord.source` (e.g.
`"anomaly.loki"` → *"the log-check"*, with a readable fallback for any
unlisted breaker name) before the agent's own summary. Compose does not
know or care which agent or breaker produced the record. This is the
specific design choice that satisfies "build this before adding more
agents, not after": a v0.6 agent that routes a sub-call's failure through
`get_breaker(...).call(...)` and appends the resulting `FailureRecord` to
`state["failures"]` gets an honest degraded-answer note for free, with no
change to `compose.py` at all.

Within `nodes/anomaly.py` itself, a degraded log-check is also reflected
in the **confidence score**: it's capped (`_DEGRADED_LOG_CONFIDENCE_CAP =
0.6`), not penalized the way a confirmed-clean log-check would be —
"the check failed" is missing information, not evidence of absence, so it
shouldn't be scored as if logs were checked and came back negative.

### 3. A confidence-based clarification gate in the router, not in node_resolver

`intent_router.py`'s `_IntentClassification` now asks the LLM to report
its own confidence (0–1) alongside the agent pick, and the system prompt
explicitly says not to inflate it. Below `CLARIFY_THRESHOLD` (default
`0.5`, overridable via `ROUTER_CLARIFY_THRESHOLD` since this is a tuning
knob, not a code change), the router sets `target_agent = "clarify"` and
writes a clarifying question into `error` instead of picking an agent —
routed straight to `compose` via a new conditional-edge entry (no new
graph node needed, since the router already fully prepared the state).

This is a different mechanism from `node_resolver.py`'s existing
"couldn't tell which node you meant" handling, and deliberately stays
separate: `node_resolver` disambiguates *which node* a question is about,
after an agent has already been chosen; this gate disambiguates *which
agent* should run at all, before any agent executes. Conflating the two
would mean a node-resolution failure inside (say) the wrong agent could
never surface as "actually, I should have asked which kind of question
this was."

The router's own LLM call is also now breaker-wrapped
(`get_breaker("router.intent_llm", ...)`), separately from the clarify
gate: an LLM call that's unavailable or fails post-retry still falls back
to `DEFAULT_AGENT` exactly as before v0.5, and does **not** get treated as
a "confidence too low" clarification or fed into `state["failures"]`. The
distinction matters: a routing fallback still runs a real agent that
produces a normal, full-confidence result of its own kind — that's a
different situation from the *evidence itself* being degraded (decision
2), and labeling it as degraded would put a "this answer is uncertain"
note on a perfectly good monitoring/prediction/rag answer that simply
arrived via the default route instead of a classified one.

## Consequences

- **Breaker state is process-global**, which is the whole point (a
  breaker that reset every call could never open), but it means: (a) a
  Loki outage detected on one anomaly question makes the *next* one fail
  faster (short-circuited) rather than each one independently re-waiting
  out the full timeout — desirable; (b) tests that exercise breaker
  behavior need to reset it, which is why `tests/conftest.py` adds an
  autouse fixture calling `reset_all_breakers()` before/after every test.
- **Timed-out calls leave an abandoned thread running** in the shared
  executor (Python can't interrupt a blocked thread). This is bounded —
  the thread eventually finishes or the process's own I/O-level timeouts
  fire — but it does mean a sustained flood of timing-out calls could
  build up background threads faster than they drain; the shared pool
  (`max_workers=16`) caps concurrent *new* attempts once that limit is
  hit rather than growing unbounded, but that's a queueing behavior
  worth watching if a dependency is down for an extended period, not a
  hard limit on outstanding stragglers.
- **`AgentOrchestrateResponse` gained a `degraded: bool` field**
  (`schemas.py`), computed from whether `state["failures"]` was non-empty
  — additive, no existing consumer needs to change to keep working, but
  the frontend can now style/flag a degraded answer without re-parsing
  the note text out of `answer`.
- **The clarify gate can change existing behavior for genuinely ambiguous
  questions** that v0.4 would have silently routed to `monitoring` —
  intentional (that's the fix), but means `ROUTER_CLARIFY_THRESHOLD` is a
  real product-tuning surface now, not just an implementation detail.

## Revisit when

- A second agent grows its own external sub-call worth breaker-wrapping
  (e.g. the planned Security Agent reusing `nodes/anomaly.py`'s
  sub-orchestration pattern) — it should follow the same
  `get_breaker(...).call(...)` + append-to-`state["failures"]` shape
  `_check_logs` established here, not a bespoke try/except.
- The clarify gate's static `_CLARIFYING_QUESTION` stops scaling once
  there are enough agents that listing all of them in one sentence reads
  poorly — at that point it's worth having the classifier return its
  top-2 candidates so the clarifying question can be specific ("did you
  mean X or Y?") rather than enumerating every agent.
- Breaker configuration (timeouts, thresholds) currently lives as
  hardcoded defaults at each `get_breaker(...)` call site; if these need
  to be tuned per-environment without a code change, they should move to
  env vars the way `ROUTER_CLARIFY_THRESHOLD` already does.
