"""CLI entry point for the v1.0 official-OpenStack-docs ingest pipeline
(docs/architecture/adr-0010-openstack-expert-v1-expansion.md). Mirrors
app/scripts/ingest_knowledge.py's shape exactly -- see that file's
docstring for why this is a standalone script rather than only reachable
through the API (a fresh checkout/CI job can run it without the API
process up).

Usage: python -m app.scripts.ingest_openstack_docs
"""
import logging
import sys

from ..services.openstack_docs.ingest import run_ingest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    result = run_ingest()
    logger.info(
        "official-docs ingest complete: %d chunks embedded into %r from %d/%d sources "
        "(%.1fs, model=%s)",
        result.chunks_embedded, result.collection, result.sources_fetched,
        result.sources_configured, result.duration_seconds, result.embedding_model,
    )
    if result.failed_urls:
        logger.warning("sources that failed to fetch: %s", result.failed_urls)
    return 0 if result.chunks_embedded > 0 or not result.failed_urls else 1


if __name__ == "__main__":
    sys.exit(main())
