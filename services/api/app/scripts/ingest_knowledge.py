#!/usr/bin/env python3
"""Run the docs/knowledge/ -> Qdrant Cloud embeddings pipeline on demand.

Equivalent to `POST /api/v1/knowledge/ingest`, for use from a shell/cron
context where hitting the API isn't convenient (e.g. right after a docs
change lands, before the API container even needs to be up).

    python -m app.scripts.ingest_knowledge
    python -m app.scripts.ingest_knowledge --knowledge-dir ../../docs/knowledge
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.knowledge.ingest import run_ingest  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge-dir",
        default=None,
        help="Path to docs/knowledge/ (defaults to CORTEX_KNOWLEDGE_DIR env var, "
             "or /app/docs/knowledge)",
    )
    args = parser.parse_args()

    result = run_ingest(args.knowledge_dir)
    print(
        f"ingested {result.chunks_embedded} chunks from {result.files_processed} files "
        f"in {result.knowledge_dir} -> Qdrant collection '{result.collection}' "
        f"(model={result.embedding_model}) in {result.duration_seconds:.1f}s"
    )


if __name__ == "__main__":
    main()
