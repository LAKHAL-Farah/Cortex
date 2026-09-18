# ADR-0010: OpenStack Expert Agent v1.0 — official docs, a community-search fallback, and a feedback loop

**Status:** Accepted
**Related code:** `services/api/app/services/openstack_docs/`,
`services/api/app/services/web_search.py`,
`services/api/app/agents/nodes/openstack_expert.py`,
`services/api/app/agents/nodes/openstack_expert_catalog_store.py`,
`services/api/app/models.py` (`CatalogEntry`),
`services/api/app/routers/openstack_docs.py`,
`services/api/app/routers/openstack_expert.py`
**Related ADRs:** adr-0008 (OpenStack Expert Agent v1 — v0.6's curated
catalog, symptom matcher, and two entry paths; everything below extends
that design rather than replacing any of it), adr-0004 (Knowledge RAG —
the internal-docs corpus this ADR is careful to stay a *separate* corpus
from, see Decision 1)

## Context

adr-0008 shipped v0.6 with three explicit gaps, all called out in its own
"Revisit when" section and the original planning doc's OS.4-OS.6:

- A standalone question with no catalog match got one static, honest
  "I don't have a runbook entry for that yet" answer — useful, but a dead
  end even when the real OpenStack documentation, or the OpenStack
  community, would actually have answered it.
- The catalog (`openstack_expert_catalog.py`'s `CATALOG`) could only grow
  through a code change and review — correct for a v1 baseline (adr-0008
  Decision 1: "editorial judgment... appropriate for v1"), but a hard
  ceiling on how fast it could grow from real, resolved incidents.
- adr-0008 Decision 1 also flagged, as a "Revisit when," that the catalog
  might eventually outgrow hand-maintained Python literals.

v1.0 is exactly these three gaps, and only these three — nothing about
the matcher's scoring, the 3-layer render shape, or the chained/standalone
distinction adr-0008 established changes here.

## Decisions

### 1. Official docs get their own Qdrant collection, not a merge into either existing corpus

`services/openstack_docs/` embeds a small, curated set of
docs.openstack.org admin/troubleshooting pages into
`cortex-openstack-official-docs` — a Qdrant collection on the *same*
Qdrant Cloud cluster `cortex-knowledge` (adr-0004) already uses, but a
different collection, reusing that module's embedding client
(`../knowledge/embeddings.py`) and its Qdrant client
(`../knowledge/qdrant_store.get_client()`) directly rather than
duplicating either.

This is the same line adr-0008's own §1 table drew between the RAG/
Knowledge Agent and the OpenStack Expert Agent in the first place: "how
did *we* fix this before" (internal runbooks/post-mortems) and "what does
OpenStack itself recommend" (official upstream docs) are different jobs
grounded in different corpora, and a single citation showing up as "from
docs.openstack.org" vs. "from our own runbook" has to stay
unambiguous. Mixing the two into one collection would blur exactly the
distinction that table exists to preserve — so a third corpus (official
docs) gets a third collection, not folded into either of the first two.

**Retrieval-only, no LLM layer on top** (`search.py`): consistent with
adr-0008 Decision 5 ("commands are never LLM-generated" — an incorrect
but fluent command is actively harmful), an official-docs excerpt is
*shown*, not rewritten into new prose. If the raw excerpt doesn't
adequately answer the question, that's a real gap in what got ingested,
not something worth papering over with a generation step that might
subtly misstate what the docs actually say.

**Small and curated, not a full-site crawl** (`sources.py`): the original
planning doc's own §6 table was explicit that "don't over-invest in the
docs embedding pipeline early — the catalog is where the real value is."
One admin/troubleshooting entry point per service, not an open-ended
crawl — bounded, predictable, and easy to review as a diff the same way
adding a `CATALOG` entry is.

**On-demand, not scheduled** (`ingest.py`, `POST /api/v1/openstack-docs/ingest`,
`ingest_openstack_docs` CLI): mirrors `../knowledge/ingest.py`'s own
on-demand trigger — upstream OpenStack docs don't drift on this
deployment's clock the way anomaly baselines or topology do, so there's
no periodic job to add to `main.py`'s lifespan tasks.

### 2. The web-search fallback is deliberately the *last* tier, and deliberately biased away from docs.openstack.org

`services/web_search.py` wraps a community-search API (Tavily), scoped to
`COMMUNITY_DOMAINS` — Launchpad, the OpenStack mailing lists, Ask
OpenStack, StoryBoard, Stack Overflow/Server Fault — explicitly excluding
docs.openstack.org, which is the *official-docs* tier's job (Decision 1).
This is exactly what the original planning doc's §2/§6 called for: "live
web_search fallback for Launchpad bugs / mailing list threads... always
labeled as community-sourced, distinct from authoritative docs... use
only as the low-confidence fallback, never primary."

`_standalone_fallback` (`openstack_expert.py`) tries official docs first,
then web search, only after both come back empty does it fall back to
the original v0.6 static text — same graceful-degradation shape as every
other external client in this codebase (`services/cve_feed.py`,
`ebpf_signal.py`, `loki_client.py`): a missing `TAVILY_API_KEY`, an
unreachable Qdrant Cloud cluster, or an embedding model that can't load
are all just "this tier isn't available right now," caught broadly and
logged, never a crash and never something that blocks trying the next
tier.

**Each tier's `raw_data["source"]` and rendered markdown are visibly
different** ("catalog" / "official_docs" / "web_search" / "none") — a
catalog match keeps adr-0008's original "Deeper reference" framing; the
official-docs fallback is explicitly framed as "no entry in the curated
catalog... but the official docs cover it," with a per-excerpt
`docs.openstack.org` source line; the web-search fallback is explicitly
framed as "community-sourced" and "not official documentation... hasn't
been reviewed" before it lists anything, with a required trailing note
saying so again. This is the same distinction adr-0008's own §1 table
insisted on for the *catalog's* citations, extended one level down here
to distinguish all three tiers from each other, not just from the
internal RAG agent.

### 3. The catalog grows through an *additive* reviewable layer, not by editing `CATALOG` itself

adr-0008 flagged two different possible futures for how the catalog
might need to change once it stopped being small: growth through code
review (fine for a while), or "moving `CATALOG` to a reviewed YAML/JSON
file loaded at import time" once literals got unwieldy. v1.0 does
neither — it adds a third option that fits the actual shape of how new
entries arrive in practice (from a *resolved incident*, submitted by
whoever fixed it, not by someone editing a file and opening a PR): a new
`catalog_entries` Postgres table (`models.CatalogEntry`), loaded
alongside the static `CATALOG` at match time
(`openstack_expert_catalog_store.load_published_entries`,
`openstack_expert._combined_catalog`) rather than merged into it.

This keeps the property adr-0008 Decision 1 cared about most — a bad
entry can't corrupt the reviewed baseline — while removing the
"needs a code change" ceiling entirely:

- **`status` is the review gate a code review gave `CATALOG` entries for
  free.** A submission starts as `"draft"` (pre-filled from a resolved
  `AnomalyEvent`'s `resolution_note` when it came from the "save this as
  a catalog entry" UI action, see `routers/openstack_expert.py`'s
  `draft-from-incident` endpoint; blank otherwise) and is completely
  invisible to `_match_symptoms` until it passes
  `validate_symptom_entry` — the *exact* per-entry invariants
  `tests/test_openstack_expert_catalog.py` already enforces on every
  hand-authored `CATALOG` entry (every `confirm_command` read-only, every
  command labeled and described, both layers non-empty) — and an admin
  publishes it (`POST .../publish`, `require_admin`-gated, same trust
  boundary as the official-docs `/ingest` endpoint). adr-0008 Decision 5
  is exactly why this gate can't be skipped: an incorrect but
  confidently-labeled command is actively harmful, not just a bad
  sentence, and a hand-authored `CATALOG` entry only avoids that risk
  because a human reviewed it before merge — a UI submission needs the
  equivalent check enforced in code instead.
- **Never a hard delete once published** — `"archived"` retires an entry
  from matching without erasing the row, so a citation a past answer
  already showed someone never dangles.
- **Fresh read per call, not cached** (`_combined_catalog`): a
  newly-published entry is visible to the very next query, not after a
  restart. The read itself degrades to "just `CATALOG`" (an empty list
  from `load_published_entries`) on any DB hiccup, the same defensive
  shape `agents/nodes/anomaly.py` and `agents/nodes/security.py` already
  use for their own short-lived `SessionLocal()` reads from inside a
  graph node — see those modules' own comments on why a live `Session` is
  never threaded through graph state.
- **`symptom_id` (a human-readable slug), not the row's surrogate `id`,
  is what slots into `SymptomEntry["id"]`** — a published entry is looked
  up and cited (`matched_symptom_id`) exactly the way a hand-authored one
  always has been; there is no second code path anywhere in
  `openstack_expert.py`'s matcher or renderer for a DB-backed entry.

## Consequences

- **A standalone "how do I check X" question with no catalog match now
  costs up to two more IO round-trips** (an embedding call + Qdrant
  query, then possibly a web-search API call) before giving up — real
  added latency on the worst case (a genuinely novel question), traded
  for that case going from "a dead end" to "grounded in something." A
  *chained* no-match is unaffected either way (see below).
- **Chained no-match still leaves the upstream diagnosis unmodified,
  unchanged from adr-0008 Decision 4.** This was deliberately not
  widened to also try the docs/web-search tiers: a good, specific
  diagnosis already says something useful, and adr-0008's own reasoning
  for not forcing a generic layer on top of it ("would make the *worse*
  answer more common, not the goal here") applies just as much to a
  sourced-but-generic fallback as to a synthesized one.
- **Matching now depends on a DB read the v0.6 matcher never needed.**
  `_match_symptoms`/`_detect_service_binaries` both keep the old
  signature usable (a `catalog` argument defaulting to just the static
  `CATALOG`), so every v0.6-era unit test of either function is
  unaffected; only `openstack_expert_agent` itself (both entry paths)
  now pulls in published entries via `_combined_catalog`.
- **Two more admin-gated write paths exist that can change what the live
  agent tells every user**: publishing a catalog entry, and (indirectly)
  what an official-docs ingest puts in front of the fallback tier. Same
  trust boundary as the pre-existing `/api/v1/knowledge/ingest`, not a
  new category of risk, but worth naming: unlike a `CATALOG` PR, there's
  no required second reviewer here — `require_admin` is the whole gate.
- **`AgentResult.raw_data` gains a `source` key** (`"catalog"` /
  `"official_docs"` / `"web_search"` / `"none"`) on every answer this
  agent produces, additive and always present now — no existing consumer
  needs to change, but a frontend can now render a citation badge without
  inferring the tier from whether `matched_symptom_id` is set.

## Revisit when

- Real submission volume shows whether `validate_symptom_entry`'s
  invariants are sufficient, or whether publishing should require more
  than one admin's sign-off (a lightweight two-person review) given a bad
  command here is exactly the harm adr-0008 Decision 5 was written to
  prevent.
- The official-docs collection is stale enough (an upstream release
  changed a troubleshooting page's content) that quarterly-ish manual
  `/ingest` runs aren't enough, at which point a scheduled job (added to
  `main.py`'s lifespan tasks, same as anomaly detection/baselines) is the
  natural next step — deliberately not built now, per the original
  planning doc's own "don't over-invest early" guidance in §6.
- Usage data on the web-search fallback shows whether `COMMUNITY_DOMAINS`
  is the right set, or whether it should be configurable per-deployment
  rather than a fixed list in code.
- A retrieved official-docs chunk should be filtered/boosted by the same
  `category` the symptom matcher scored the original query against —
  `sources.py`'s `DocSource.category` already matches
  `openstack_expert_catalog.Category` exactly for this, but `search.py`
  doesn't use it yet (`search_official_docs` takes an optional `service`
  filter today, not `category`).
