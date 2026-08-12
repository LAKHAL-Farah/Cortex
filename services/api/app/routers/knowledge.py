import logging

from fastapi import APIRouter, Depends, HTTPException, status

from .. import schemas
from ..security import require_api_key
from ..services.knowledge import qdrant_store
from ..services.knowledge.embeddings import EmbeddingError, embed_query
from ..services.knowledge.ingest import run_ingest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


@router.post("/ingest", response_model=schemas.KnowledgeIngestResult,
             dependencies=[Depends(require_api_key)])
def ingest_knowledge():
    """Runs the on-demand embeddings pipeline: scrapes every .md file under
    docs/knowledge/ (including service-detail/), embeds each chunk, and
    upserts it into the `cortex-knowledge` collection on Qdrant Cloud.

    Idempotent -- chunk IDs are derived from (source file, chunk index), so
    re-running this after editing a doc updates existing vectors instead of
    duplicating them.
    """
    try:
        result = run_ingest()
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except Exception as exc:
        logger.exception("knowledge ingest failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ingest failed: {exc}") from exc
    return schemas.KnowledgeIngestResult(**result.__dict__)


@router.get("/status", response_model=schemas.KnowledgeStatus)
def knowledge_status():
    info = qdrant_store.collection_info()
    if info is None:
        return schemas.KnowledgeStatus(collection=qdrant_store.QDRANT_COLLECTION, exists=False)
    return schemas.KnowledgeStatus(exists=True, **info)


@router.post("/search", response_model=schemas.KnowledgeSearchResponse,
             dependencies=[Depends(require_api_key)])
def search_knowledge(payload: schemas.KnowledgeSearchQuery):
    """Semantic search over the ingested knowledge base -- mainly for
    verifying retrieval quality after an ingest, and for any future
    assistant/chat feature that needs to ground answers in docs/knowledge/."""
    try:
        query_vector = embed_query(payload.query)
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    try:
        points = qdrant_store.search(query_vector, top_k=payload.top_k, category=payload.category)
    except Exception as exc:
        logger.exception("knowledge search failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"search failed: {exc}") from exc

    results = [
        schemas.KnowledgeSearchResult(
            score=p.score,
            text=p.payload["text"],
            source_path=p.payload["source_path"],
            doc_title=p.payload["doc_title"],
            heading=p.payload.get("heading"),
            category=p.payload["category"],
        )
        for p in points
    ]
    return schemas.KnowledgeSearchResponse(results=results)
