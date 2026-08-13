"""On-demand ingestion pipeline: docs/knowledge/*.md -> chunks -> embeddings -> Qdrant Cloud.

Unlike the periodic jobs registered in main.py's lifespan (anomaly detection,
baselines, forecasting), this pipeline does NOT run on a timer. The knowledge
base changes when someone edits a runbook, not on a fixed schedule, so it's
triggered on demand: via `POST /api/v1/knowledge/ingest` (see
routers/knowledge.py) or the `python -m app.scripts.ingest_knowledge` CLI.
"""
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .embeddings import EMBEDDING_MODEL, embed_texts
from .loader import load_knowledge_chunks
from .qdrant_store import QDRANT_COLLECTION, ensure_collection, upsert_chunks

logger = logging.getLogger(__name__)

# Repo-relative default so the same code works locally (repo checked out at
# ../..) and in the container, where docker-compose mounts docs/knowledge/
# read-only at this path (see infra/docker-compose*.yml).
DEFAULT_KNOWLEDGE_DIR = os.environ.get("CORTEX_KNOWLEDGE_DIR", "/app/docs/knowledge")


@dataclass
class IngestResult:
    knowledge_dir: str
    collection: str
    embedding_model: str
    files_processed: int
    chunks_embedded: int
    duration_seconds: float


def run_ingest(knowledge_dir: str | os.PathLike | None = None) -> IngestResult:
    started = time.monotonic()
    knowledge_dir = Path(knowledge_dir or DEFAULT_KNOWLEDGE_DIR)
    if not knowledge_dir.exists():
        raise FileNotFoundError(f"knowledge directory not found: {knowledge_dir}")

    chunks = load_knowledge_chunks(knowledge_dir)
    files_processed = len({c.source_path for c in chunks})
    logger.info("knowledge ingest: %d chunks across %d files from %s", len(chunks), files_processed, knowledge_dir)

    if not chunks:
        return IngestResult(
            knowledge_dir=str(knowledge_dir),
            collection=QDRANT_COLLECTION,
            embedding_model=EMBEDDING_MODEL,
            files_processed=0,
            chunks_embedded=0,
            duration_seconds=time.monotonic() - started,
        )

    ensure_collection()
    vectors = embed_texts([c.text for c in chunks])
    embedded = upsert_chunks(chunks, vectors)

    return IngestResult(
        knowledge_dir=str(knowledge_dir),
        collection=QDRANT_COLLECTION,
        embedding_model=EMBEDDING_MODEL,
        files_processed=files_processed,
        chunks_embedded=embedded,
        duration_seconds=time.monotonic() - started,
    )
