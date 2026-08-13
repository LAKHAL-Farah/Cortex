# ADR-0005: Grounded knowledge chat via NVIDIA NIM + LangChain, streamed over SSE

**Status:** Accepted
**Related code:** `services/api/app/services/knowledge/chat.py`,
`services/api/app/routers/knowledge.py` (`POST /api/v1/knowledge/chat`), `services/web/app/copilot/`,
`services/web/components/CopilotChat.tsx`
**Related tests:** `services/api/tests/test_knowledge_chat.py`, `services/api/tests/test_knowledge_router.py`
**Depends on:** `adr-0004-knowledge-rag-qdrant-cloud.md` (retrieval: embeddings, Qdrant Cloud, chunking)

## Context

adr-0004 built retrieval (`POST /api/v1/knowledge/search`) but left generation for later --
its own docstring on that endpoint says it's "for any future assistant/chat feature." This
ADR is that feature: `POST /api/v1/knowledge/chat`, backing the "AI Copilot" nav entry
already present in `Sidebar.tsx` (previously a dead link to `/copilot`). The requirement is
answers grounded in `docs/knowledge/` with visible citations, not model-memory answers dressed
up as if they came from the docs.

## Decision

**Generation is a thin layer on top of adr-0004's retrieval, not a new retrieval path.**
`chat.py`'s `retrieve()` calls the same `embed_query`/`qdrant_store.search` functions
`routers/knowledge.py`'s `/search` endpoint already used. This keeps retrieval behavior
(chunking, scoring, category filter) identical between `/search` and `/chat` by construction
-- there's only one retrieval implementation to keep correct.

**NVIDIA NIM for the chat model, called through LangChain's `ChatNVIDIA`.** Unlike adr-0004's
embeddings (deliberately moved in-process to avoid an external API), generation needs a much
larger model than is practical to self-host on the API container, so an external call is
unavoidable here. LangChain's `langchain-nvidia-ai-endpoints` package wraps NIM's OpenAI-compatible
endpoint as a standard `BaseChatModel`, giving message-list construction (`SystemMessage`/
`HumanMessage`/`AIMessage`) and `.stream()` for free instead of hand-rolling SSE parsing
against the raw NIM HTTP API. `NVIDIA_NIM_MODEL` defaults to
`nvidia/nemotron-3-super-120b-a12b`; `NVIDIA_NIM_BASE_URL` is only set to point at a
self-hosted NIM container instead of the hosted endpoint, and is left unset by default.

**Grounding is enforced two ways, not just via prompt instructions.** A system prompt alone is
easy for a model to ignore under a leading question, so:
1. `MIN_RETRIEVAL_SCORE` (default 0.2, env-tunable via `KNOWLEDGE_CHAT_MIN_SCORE`) filters out
   weak/irrelevant matches *before* they reach the prompt -- retrieval noise never becomes
   something the model can cite as if it were relevant.
2. If nothing clears that bar, `chat.py` never calls the NIM endpoint at all -- it streams a
   fixed "nothing in the knowledge base answers this" message. This is a hard guarantee against
   hallucination, not a request not to hallucinate: with no chunks in the prompt, the model
   physically cannot produce a grounded answer, so the code doesn't give it the chance.

The system prompt (built fresh per request from the retrieved chunks) also requires
inline citations of the form `[source-file.md]`, using the knowledge-file's filename as the
label -- the thing a person can actually go open under `docs/knowledge/`, rather than the
looser `doc_title` (a doc's H1, which can differ from its filename).

**Conversation memory is client-side, not server-side.** `ChatQuery.history` carries the prior
turns on every request; the API itself stores nothing between calls. This matches the rest of
Cortex's chat-adjacent state handling (nothing else in the API holds a session), avoids adding
a sessions table/store for what the frontend already needs to hold in React state to render the
transcript anyway, and keeps `/chat` calls independent/retriable -- a dropped connection loses
nothing server-side. Trade-off: no cross-device/cross-tab continuation and no server-side
transcript audit trail; if that's ever needed, revisit alongside `models.py`.

**Responses stream over Server-Sent Events, not a single JSON response.** A grounded answer
can run to several sentences with multiple citations, and NIM (like other hosted LLM APIs)
exposes generation as a token stream -- buffering the whole answer server-side before replying
would throw that latency benefit away for no reason. SSE (`event: sources` /
`event: token` / `event: done` / `event: error`) was chosen over a raw chunked-text stream so
the client can distinguish the sources payload from answer tokens without a custom
framing protocol, and over WebSockets because this is a strictly server-to-client stream per
request -- there's no bidirectional/persistent-connection need `/chat` has that a plain HTTP
streaming response doesn't already cover.

**Retrieval happens synchronously before the stream opens; only generation is streamed.**
`chat_knowledge()` calls `retrieve()` (and, if it returned chunks, `require_configured()` to
check `NVIDIA_API_KEY`) *before* constructing the `StreamingResponse`. A retrieval failure
(bad embedding call, Qdrant unreachable) or a missing API key therefore comes back as a normal
`HTTPException` with a real status code, not a mid-stream error after the client has already
received a 200 -- the only thing that can still fail mid-stream is the NIM call itself once
generation is underway, which surfaces as an `event: error` SSE frame.

**The `/copilot` frontend is a new page/component, not a retrofit of `LogViewer`'s or any other
existing view's layout.** It's the first chat-style UI in `services/web`; the rest of the app
is dashboards/tables. Deliberately GitHub/Notion-flavored (see `frontend-design` skill) using
the existing `--surface`/`--border`/`--accent` design tokens in `globals.css` rather than a
new visual language, and citations render as small source chips under each answer (linking
back to `source_path`) instead of inline footnote numbers, since the corpus is small enough
(~14 files) that "which doc" is more useful at a glance than a numbered reference list.

## Consequences

- A second external dependency (`NVIDIA_API_KEY`) joins `QDRANT_URL`/`QDRANT_API_KEY` as
  optional-at-boot config -- `main.py`'s lifespan doesn't touch either, so the API still starts
  and serves every other endpoint with both unset. Only `POST /api/v1/knowledge/chat` needs
  `NVIDIA_API_KEY`; `/search` and `/ingest` are unaffected (they never import `chat.py`'s NIM
  client).
- Changing `NVIDIA_NIM_MODEL` is a config change only, no rebuild/re-ingest needed (unlike
  `EMBEDDING_MODEL` in adr-0004) -- generation doesn't touch the Qdrant collection's
  dimensionality.
- `MIN_RETRIEVAL_SCORE` is currently one global threshold. If some categories of docs turn out
  to need a different bar (e.g. `service-detail/` chunks score systematically lower/higher than
  `general/` ones), this would need to become per-category rather than a single constant --
  not needed yet at the current corpus size.
- No rate limiting or per-user cost tracking on `/chat` beyond the existing `X-API-Key` gate --
  every call that clears the retrieval-score bar makes a billed NIM call. Fine at RIF SAS's
  current scale; revisit if `/copilot` gets exposed more broadly.
