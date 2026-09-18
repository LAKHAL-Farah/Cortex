"""On-demand ingestion pipeline: OFFICIAL_DOC_SOURCES -> fetch -> chunk ->
embed -> Qdrant Cloud (`cortex-openstack-official-docs`). The docs-
embedding half of the v1.0 OpenStack Expert Agent expansion (see
docs/architecture/adr-0010-openstack-expert-v1-expansion.md, and the
original planning doc's OS.5).

Deliberately mirrors ../knowledge/ingest.py's shape: not on a timer (see
main.py -- the periodic jobs list is anomaly detection/baselines/
forecasting/topology sync, all things that drift on this deployment's own
clock; upstream OpenStack docs don't), triggered on demand via
`POST /api/v1/openstack-docs/ingest` (routers/openstack_docs.py) or the
`python -m app.scripts.ingest_openstack_docs` CLI -- matching the original
planning doc's own recommendation ("refresh quarterly" is "low effort,
good enough", not something that needs a background scheduler).

Unlike ../knowledge/ingest.py, a single source failing to fetch doesn't
fail the whole run: `sources.py`'s own docstring is explicit that these
URLs are curated by hand, not verified live, so a moved/404'd page is a
real, expected possibility this pipeline has to shrug off rather than
error out over -- `run_ingest` collects `failed_urls` and keeps going with
whatever fetched successfully, then still embeds/upserts those.
"""
import logging
import time
from dataclasses import dataclass

from ..knowledge.embeddings import EMBEDDING_MODEL, embed_texts
from .scraper import DocChunk, ScrapeError, fetch_and_chunk
from .sources import DocSource, OFFICIAL_DOC_SOURCES
from .store import QDRANT_OFFICIAL_DOCS_COLLECTION, ensure_collection, upsert_chunks

logger = logging.getLogger(__name__)


@dataclass
class DocsIngestResult:
    collection: str
    embedding_model: str
    sources_configured: int
    sources_fetched: int
    sources_failed: int
    chunks_embedded: int
    failed_urls: list[str]
    duration_seconds: float


def _fetch_all(sources: list[DocSource]) -> tuple[list[DocChunk], list[str]]:
    chunks: list[DocChunk] = []
    failed_urls: list[str] = []
    for source in sources:
        try:
            chunks.extend(fetch_and_chunk(source["url"], source["service"]))
        except ScrapeError:
            logger.warning(
                "openstack_docs ingest: failed to fetch %s -- skipping, "
                "continuing with the rest of the source list",
                source["url"],
                exc_info=True,
            )
            failed_urls.append(source["url"])
    return chunks, failed_urls


def run_ingest(sources: list[DocSource] | None = None) -> DocsIngestResult:
    started = time.monotonic()
    sources = sources if sources is not None else OFFICIAL_DOC_SOURCES

    chunks, failed_urls = _fetch_all(sources)
    logger.info(
        "openstack_docs ingest: %d chunks from %d/%d sources (%d failed)",
        len(chunks), len(sources) - len(failed_urls), len(sources), len(failed_urls),
    )

    if not chunks:
        return DocsIngestResult(
            collection=QDRANT_OFFICIAL_DOCS_COLLECTION,
            embedding_model=EMBEDDING_MODEL,
            sources_configured=len(sources),
            sources_fetched=len(sources) - len(failed_urls),
            sources_failed=len(failed_urls),
            chunks_embedded=0,
            failed_urls=failed_urls,
            duration_seconds=time.monotonic() - started,
        )

    ensure_collection()
    vectors = embed_texts([c.text for c in chunks])
    embedded = upsert_chunks(chunks, vectors)

    return DocsIngestResult(
        collection=QDRANT_OFFICIAL_DOCS_COLLECTION,
        embedding_model=EMBEDDING_MODEL,
        sources_configured=len(sources),
        sources_fetched=len(sources) - len(failed_urls),
        sources_failed=len(failed_urls),
        chunks_embedded=embedded,
        failed_urls=failed_urls,
        duration_seconds=time.monotonic() - started,
    )
