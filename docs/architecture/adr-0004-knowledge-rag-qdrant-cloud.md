# ADR-0004: Knowledge base embeddings on Qdrant Cloud, embedded locally, ingested on demand

**Status:** Accepted
**Related code:** `services/api/app/services/knowledge/` (`loader.py`, `embeddings.py`,
`qdrant_store.py`, `ingest.py`), `services/api/app/routers/knowledge.py`,
`services/api/app/scripts/ingest_knowledge.py`
**Related tests:** `services/api/tests/test_knowledge_loader.py`
**Related docs:** `docs/knowledge/` (the corpus itself)

## Context

RIF SAS's own docs and runbooks for the OpenStack infra (`docs/knowledge/`) needed to be
retrievable by embedding, so that an assistant or search feature can ground answers in the
actual current infra instead of relying on model memory or one very long system prompt.

This is a second vector-store/embeddings addition to the codebase after
`adr-0002-topology-graph.md`'s Neo4j graph and `adr-0003-prometheus-cross-check.md`'s health
overlay -- unlike those, the knowledge base isn't derived from OpenStack/Prometheus state at
all, so its lifecycle and dependencies are kept fully separate (see Decision below).

## Decision

**Vectors live on Qdrant Cloud, not pgvector.** Cortex already runs a Postgres instance
(`infra/docker-compose*.yml`), so pgvector was the obvious low-effort option, but Qdrant
Cloud was chosen instead: it's a separate managed service reachable via `QDRANT_URL`/
`QDRANT_API_KEY`, with no vector-store container added to docker-compose (the same reasoning
that already kept the topology graph on its own Neo4j container rather than folding it into
Postgres). This keeps the knowledge base's read/write and scaling profile independent of
both the operational Postgres database (nodes, anomalies, baselines) and the topology graph,
and avoids adding the pgvector extension/migration surface to `alembic/` for a dataset
(~14 markdown files) that doesn't share a lifecycle with the rest of the schema.

**Embeddings are computed locally with sentence-transformers, not via an external API.**
The original design called an OpenAI-compatible `/embeddings` HTTP endpoint
(`EMBEDDING_API_BASE`/`EMBEDDING_API_KEY`), reusing the `requests` dependency already in
`requirements.txt`. That was dropped in favor of `sentence-transformers` running
`BAAI/bge-small-en-v1.5` in-process: no embeddings-provider credential to provision or
rotate, no per-call latency/cost to an external API, and no outbound dependency at request
time -- only Qdrant Cloud remains as an external service this feature needs. The model is
baked into the Docker image at build time (`Dockerfile`'s `RUN python -c
"...SentenceTransformer(...)"` step) with `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` set at
runtime, so ingest/search never make a Hugging Face Hub call either. The trade-off is a
larger image (`torch`+`transformers` add roughly 500MB-1GB) and a slower first build; both
are one-time costs paid at image-build time rather than recurring per-request costs.

**Ingestion is on-demand, not a periodic background task.** `main.py`'s lifespan already
runs anomaly detection, baseline refresh, forecast training, and the two topology sync loops
on fixed intervals, because those depend on continuously-updating Prometheus/Loki/OpenStack
state. The knowledge base doesn't -- it changes when someone edits a doc under
`docs/knowledge/`, which happens on the order of days/weeks, not minutes. Ingestion is
instead triggered explicitly, either via `POST /api/v1/knowledge/ingest` or the
`ingest_knowledge` CLI script, mirroring the existing on-demand script pattern (see
`app/scripts/run_topology_sync.py` for the closest existing precedent) rather than the
periodic-task pattern.

**Chunking splits on markdown headings (`##`/`###`), not fixed-size windows.** Every file
under `docs/knowledge/` is a series of short, topic-scoped sections (a table, a command
block, a short paragraph) rather than continuous prose, so heading boundaries already line
up with retrieval-sized, semantically coherent units. A chunk larger than
`MAX_CHUNK_CHARS` (2000) is further split on paragraph boundaries as a fallback, but this is
rare in practice -- see `loader.py`.

**Chunk IDs are deterministic (`uuid5` of `source_path::chunk_index`), not random.**
Re-running ingestion after editing a doc upserts the same Qdrant points instead of
accumulating duplicates, so the pipeline is safe to call repeatedly without a full
collection wipe first.

## Consequences

- Qdrant Cloud is now part of the deployment surface, alongside Neo4j (topology graph) and
  Postgres (operational data) -- but unlike those two, it's optional at container-start time
  (nothing in `main.py`'s lifespan touches it), so the API still boots and serves the
  existing nodes/metrics/anomalies/forecast/topology endpoints with `QDRANT_URL` unset; only
  `/api/v1/knowledge/*` calls fail until it's configured.
- Changing `EMBEDDING_MODEL` requires rebuilding the image (the `RUN` line in `Dockerfile`
  that pre-downloads the model must be updated to match) and re-ingesting into a fresh Qdrant
  collection if the new model's vector size differs -- `ensure_collection()` is a no-op when
  the collection already exists at the old dimensionality, it won't resize it in place.
- `docs/knowledge/` is mounted read-only into the API container
  (`infra/docker-compose*.yml`) rather than copied at build time, so editing a doc and
  re-running ingestion doesn't require a rebuild -- only a model change does.
- If the knowledge base's write/query pattern ever needs to line up with the rest of
  Cortex's data (e.g. joining a retrieved chunk against `nodes`/topology graph vertices),
  revisit whether pgvector or Neo4j would be a better fit at that point -- this ADR only
  covers the standalone case of embedding static docs.
