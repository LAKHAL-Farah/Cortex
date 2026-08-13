import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from .. import schemas
from ..security import require_api_key
from ..services.knowledge import chat as knowledge_chat
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


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _chat_event_stream(message: str, history, chunks):
    """Generator consumed by StreamingResponse below. Runs after retrieval
    (and, if chunks is non-empty, the NVIDIA_API_KEY check) already
    succeeded outside this generator -- see chat_knowledge() -- so
    everything here streams as a 200 once it starts."""
    sources = [
        schemas.ChatSource(
            source_path=c.source_path,
            doc_title=c.doc_title,
            heading=c.heading,
            score=c.score,
        ).model_dump()
        for c in chunks
    ]
    yield _sse_event("sources", {"sources": sources})
    try:
        for token in knowledge_chat.stream_answer(message, history, chunks):
            yield _sse_event("token", {"text": token})
    except knowledge_chat.ChatConfigError as exc:
        yield _sse_event("error", {"message": str(exc)})
        return
    yield _sse_event("done", {})


@router.post("/chat", dependencies=[Depends(require_api_key)])
def chat_knowledge(payload: schemas.ChatQuery):
    """Grounded Q&A over docs/knowledge/ (adr-0005): retrieves the same way
    as /search, then streams an NVIDIA NIM-generated answer (via LangChain's
    ChatNVIDIA) as Server-Sent Events over that retrieved context only.

    Event order: one `sources` event (the chunks the answer is grounded in,
    possibly empty), then zero or more `token` events, then either `done` or
    `error`. Streaming means citations only make sense once the client has
    the full token stream, so `sources` is emitted first and the client
    should hold onto it and render it once `done` arrives (or immediately,
    since it never changes mid-stream).
    """
    try:
        chunks = knowledge_chat.retrieve(payload.message, top_k=payload.top_k, category=payload.category)
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except Exception as exc:
        logger.exception("knowledge chat retrieval failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"retrieval failed: {exc}") from exc

    if chunks:
        try:
            knowledge_chat.require_configured()
        except knowledge_chat.ChatConfigError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return StreamingResponse(
        _chat_event_stream(payload.message, payload.history, chunks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
