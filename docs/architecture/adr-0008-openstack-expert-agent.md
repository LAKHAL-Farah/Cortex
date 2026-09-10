# ADR-0008: OpenStack Expert Agent v1 — a curated catalog, a symptom matcher, and two ways in

**Status:** Accepted
**Related code:** `services/api/app/agents/nodes/openstack_expert_catalog.py`,
`services/api/app/agents/nodes/openstack_expert.py`,
`services/api/app/agents/graph.py`, `services/api/app/agents/intent_router.py`
**Related ADRs:** adr-0007 (resilience layer — a hard prerequisite, see
"Why now" below)

## Context

Every agent through v0.5 reports a finding: a metric value, a flagged
anomaly, a correlated log line, a forecast. None of them explain the
finding in a way that teaches an operator what's actually going on or
what to do about it — the closest thing is anomaly.py's own narrative,
which is deliberately scoped to *this specific host's evidence*, not general
operational knowledge about the failure mode itself.

v0.6's brief is explicit about the shape the fix should take: a curated
symptom → command → doc catalog, a matching step, and an agent that either
triggers automatically off a diagnosis or answers a direct "how do I check
X" question — with every command labeled read-only or state-changing.

### Why now, not earlier

This is the first agent that's a genuine **second step chained after a
diagnostic one**, not just one more independent router branch. That's
exactly the scenario adr-0007 was written for: stacking a second call
after a first one multiplies the ways a single question can fail (the
diagnosis call fails, or the follow-up call fails, or both) — v0.5 proved
that failure handling (timeouts, retries, degraded-answer labeling)
*before* this agent existed to need it, rather than retrofitting it once
this agent was already chained into the graph and something broke in
production. Building this before Security/Network agents (Phase 5) is
correct because it's fully self-contained otherwise — it doesn't need
their output, and they can each get the same "chain after a diagnosis"
treatment this ADR establishes without changing anything here.

## Decisions

### 1. The catalog is data, not logic (`openstack_expert_catalog.py`)

A fixed Python list of `SymptomEntry` records, each with `metric_names`,
`service_binaries`, and free-text `keywords` as trigger inputs, and three
fixed output fields matching the DoD's 3-layer shape exactly:
`what_it_means` (layer 1), `confirm_commands` (layer 2, **enforced
read-only** — see the test suite's invariant checks), and
`remediation_commands` (layer 3, each command individually labeled
`read_only: bool` rather than labeling the whole section, since a
remediation step often starts with one more read before the actual
state-changing command).

Coverage is deliberately weighted by what's actually true of this
deployment, not generic OpenStack trivia:

- **`cpu_usage` and `ram_usage`** get the deepest entries because
  `anomaly_detector.py`'s `METRICS` dict only scores those two today —
  they're the only failure modes a live `AnomalyFlag` can actually
  produce right now, so they're the most likely real trigger.
- **The exact service binaries `topology_sync.py` syncs**
  (`nova-compute`, `nova-scheduler`, `cinder-volume`, `cinder-scheduler`,
  `neutron-dhcp-agent`, `neutron-l3-agent`, `neutron-openvswitch-agent`)
  each get an entry, because a `Service.state == "unreachable"`
  reconciliation (adr-0003) is a second real, already-implemented
  detection path independent of `AnomalyFlag` rows.
- **Everything else** (Nova scheduling failures, stuck/ERROR instances,
  RabbitMQ/MariaDB/Keystone/Glance/libvirt issues) has no automatic
  Cortex detector behind it yet, but is exactly the kind of thing an
  operator asks Cortex directly — this is what the standalone
  "how do I check X" path is for.

`disk_usage` is included even though `anomaly_detector.py` doesn't score
it yet — flagged explicitly in that entry's own `what_it_means` as a gap,
not silently treated as if it were covered.

Commands are real, Kolla-Ansible-specific commands for this deployment
(`docker exec nova_libvirt virsh ...`, not a generic `systemctl` example)
— this catalog is written for *this* cloud, per docs/knowledge/README.md's
own stack description, not generic OpenStack documentation. `doc_ref`
points at the *intended* path in docs/knowledge/ (topology.md,
service-detail/nova.md, etc.) per that directory's own README table; those
files aren't all authored yet in this checkout, so a `doc_ref` today means
"this is where the deep-dive will live," not a guaranteed retrievable RAG
citation — nothing here needs to change once those files exist and get
ingested, since the path is already the same one rag_agent's citations
will eventually show.

### 2. The symptom matcher is a simple additive score, not an LLM call

`_match_symptoms` scores every catalog entry by how many of its trigger
inputs (metric name, service binary, keyword-in-question,
keyword-in-log-line) actually hit, and returns them ranked. This was
chosen over an LLM-based classifier (the way `intent_router.py` picks an
*agent*) because matching a *specific catalog entry* needs to be exact
and auditable — a wrong agent-routing guess costs a clarifying question at
worst (adr-0007's clarify gate), but a wrong symptom match here would
hand someone a plausible-sounding but incorrect diagnosis and command
set. A fixed, inspectable scoring function can be unit-tested against
every catalog entry directly (see `test_openstack_expert.py`); an LLM
classifier's behavior can't be pinned down the same way.

Log-line keyword hits are weighted **equal to** a query-keyword hit and
above a bare metric-name hit alone (both `_SCORE_KEYWORD_IN_LOG_LINE` and
`_SCORE_KEYWORD_IN_QUERY` are 2, vs. `_SCORE_METRIC_NAME`'s 3 needing two
log hits to outweigh it) — deliberately so a specific, corroborated
pattern (e.g. two log keywords matching "lost connection to libvirt")
can outrank a generic metric-name match (e.g. `cpu_usage`) when both are
present. This mirrors `anomaly.py`'s own `_hypothesize_cause` philosophy
that correlated log content is more specific evidence than a bare metric
reading, applied the same way here for consistency between the two
agents' reasoning.

### 3. Two entry paths, distinguished by what's already in `state`

- **Chained**: `graph.py` adds a second conditional edge on *both*
  `anomaly` and `monitoring` — `should_trigger_after_anomaly` /
  `should_trigger_after_monitoring` (in `openstack_expert.py`, so the
  trigger logic lives next to the matching logic it has to stay
  consistent with) decide whether that node's diagnosis has anything
  worth explaining. If not, the graph flows straight to `compose` exactly
  as it did before v0.6 — chaining is additive, never mandatory.
- **Standalone**: a sixth `AgentName` (`openstack_expert`) the router can
  pick directly for a "how do I check X" question with no diagnosis
  involved at all.

Both converge on the same `_match_symptoms` → render pipeline; only how
the matcher's inputs get assembled differs (`_evidence_from_anomaly` /
`_evidence_from_monitoring` for the chained case, direct query text +
`_detect_service_binaries` for standalone).

**How the node tells which path it's on**: it reads `state["target_agent"]`
on entry. If it's `"anomaly"` or `"monitoring"` (set by the router before
either of *those* nodes ran, and never touched by them), this is a chained
call — the node reads that agent's own `raw_data` shape for evidence, then
overwrites `target_agent` to `"openstack_expert"` once it has produced its
own result. If it's already `"openstack_expert"` (set directly by the
router), this is standalone. No new state field was needed for this
distinction — `target_agent`'s existing value already carries it.

The original diagnosis is never discarded on a successful chain: its
`summary` is preserved under `raw_data["upstream_summary"]`, and
`raw_data["diagnosed_by"]` records which agent triggered the chain — both
purely for traceability, since `agent_result["summary"]` (what the user
actually sees) becomes the new 3-layer answer, evidence from the
diagnosis woven into its "what's happening" opening line rather than
just appended.

### 4. No catalog match → don't force a generic answer

Standalone with no match: a graceful, honest fallback ("I don't have a
runbook entry for that yet") pointing at what the catalog *does* cover and
suggesting `rag` for a broader documentation question — never a fabricated
generic answer.

Chained with no match (the trigger predicate said "there's something,"
but nothing in the catalog recognizes the specific pattern): the node
returns `state` **unmodified** — the upstream diagnosis stands as the
final answer, `target_agent` stays `"anomaly"`/`"monitoring"`. This was
chosen over synthesizing a generic "something's wrong, investigate
further" layer on top of a real, specific diagnosis that already said
something useful — replacing a good specific answer with a mediocre
generic one to force every chained call through the 3-layer shape would
make the *worse* answer more common, not the goal here.

### 5. Commands are never LLM-generated

`_render_command_list` renders `Command` records verbatim, always,
regardless of whether the request was chained or standalone, LLM-narrated
elsewhere or not. Layer 1 ("what's happening") is plain string
interpolation of the catalog's `what_it_means` and the diagnosis's own
evidence line — also not LLM-touched in this v1. An incorrect but
fluent-sounding command is actively harmful in a way an incorrect
sentence of prose usually isn't; this agent's value is specifically that
its layer-2/3 commands are exactly right for this deployment, so nothing
in the rendering path introduces a chance of that not being true.

## Consequences

- **`openstack_expert` is wrapped in `guarded_node` like every other
  node** (`graph.py`), so a hang or crash in it degrades to the outer
  safety net's apology text rather than losing the whole turn — but
  because it's chained *after* anomaly/monitoring, a failure here means
  the user loses the teaching layer, not the diagnosis itself, since the
  diagnosis already fully executed and would otherwise have gone straight
  to `compose`. (This isn't automatic — `guarded_node`'s failure path
  replaces `agent_result` outright rather than falling back to the
  pre-chain diagnosis; a future refinement could preserve the upstream
  result specifically for this node's failure case, noted below.)
- **The catalog is process-static.** Adding a new symptom means a code
  change (a new `SymptomEntry` in the list), not a data/config update —
  appropriate for v1 given how much editorial judgment goes into writing
  a correct, deployment-specific command list, but worth reconsidering if
  the catalog grows past what's comfortable to review as Python literals.
- **The matcher can pick a "close enough" entry that isn't actually
  right** — e.g. a question mentioning both "cpu" and "libvirt" gets
  whichever scores higher, not necessarily the operator's actual intent.
  Low `_match_symptoms` scores aren't currently surfaced as an
  "uncertain match" caveat the way `intent_router.py`'s confidence gate
  surfaces routing uncertainty — this is a reasonable v2 addition once
  there's real usage data on how often the top match is wrong.
- **`AgentOrchestrateResponse.raw_data`** now sometimes carries
  `matched_symptom_id`, `confirm_commands`, and `remediation_commands`
  (structured, with `read_only` booleans) for any answer that went
  through this agent — additive, no existing consumer needs to change.

## Revisit when

- A future agent (Security/Network, Phase 5) wants the same
  "chain after a diagnosis" pattern this ADR establishes — it should
  follow the same shape: a `should_trigger_after_<agent>` predicate
  living next to its own matching logic, read `target_agent` to detect
  chained-vs-standalone, and never force a generic answer when there's no
  good specific match.
- Usage data shows the matcher's top-1 pick is wrong often enough to be
  worth surfacing its score as a confidence/caveat, the way the intent
  router already does for agent routing.
- The catalog grows large enough that hand-maintained Python literals
  stop being the right authoring format — at that point, consider moving
  `CATALOG` to a reviewed YAML/JSON file loaded at import time, keeping
  the same `SymptomEntry` shape so `openstack_expert.py` doesn't change.
- `guarded_node`'s failure path for a *chained* node should be refined to
  fall back to the upstream diagnosis specifically, rather than the
  generic "this agent didn't respond in time" apology — worth doing once
  this pattern is used by more than one chained agent, so the fix isn't
  openstack_expert-specific.
