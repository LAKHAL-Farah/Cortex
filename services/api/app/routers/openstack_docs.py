"""POST/GET endpoints for the official-OpenStack-docs embedding pipeline
(v1.0, see docs/architecture/adr-0010-openstack-expert-v1-expansion.md).

Deliberately structured exactly like routers/knowledge.py (ingest/status/
search) minus a /chat endpoint -- see services/openstack_docs/search.py's
module docstring for why this corpus stays retrieval-only with no LLM
generation layered on top.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from .. import schemas
from ..auth import require_admin
from ..services.knowledge.embeddings import EmbeddingError
from ..services.openstack_docs import store as docs_store
from ..services.openstack_docs.ingest import run_ingest
from ..services.openstack_docs.search import search_official_docs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/openstack-docs", tags=["openstack-docs"])


@router.post("/ingest", response_model=schemas.OpenStackDocsIngestResult,
             dependencies=[Depends(require_admin)])
def ingest_openstack_docs():
    """Runs the on-demand pipeline: fetches every URL in
    services/openstack_docs/sources.py, embeds each chunk, and upserts it
    into the `cortex-openstack-official-docs` collection on the same
    Qdrant Cloud cluster the internal knowledge base uses (a different
    collection -- see store.py's module docstring on why they're kept
    separate).

    Never 404s/502s over a single bad source URL -- see ingest.py's
    `run_ingest`, which collects `failed_urls` and keeps going. This
    endpoint only fails outright if the pipeline itself can't run at all
    (e.g. the embedding model or Qdrant Cloud is unreachable).
    """
    try:
        result = run_ingest()
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except Exception as exc:
        logger.exception("openstack docs ingest failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ingest failed: {exc}") from exc
    return schemas.OpenStackDocsIngestResult(**result.__dict__)


@router.get("/status", response_model=schemas.OpenStackDocsStatus)
def openstack_docs_status():
    info = docs_store.collection_info()
    if info is None:
        return schemas.OpenStackDocsStatus(
            collection=docs_store.QDRANT_OFFICIAL_DOCS_COLLECTION, exists=False
        )
    return schemas.OpenStackDocsStatus(exists=True, **info)


@router.post("/search", response_model=schemas.OpenStackDocsSearchResponse,
             dependencies=[Depends(require_admin)])
def search_docs(payload: schemas.OpenStackDocsSearchQuery):
    """Mainly for verifying retrieval quality after an ingest -- the
    OpenStack Expert Agent's own fallback tier calls
    services/openstack_docs/search.py directly rather than through HTTP,
    same relationship routers/knowledge.py's /search has to rag_agent."""
    try:
        results = search_official_docs(payload.query, top_k=payload.top_k, service=payload.service)
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except Exception as exc:
        logger.exception("openstack docs search failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"search failed: {exc}") from exc

    return schemas.OpenStackDocsSearchResponse(
        results=[
            schemas.OpenStackDocsSearchResult(
                score=r.score,
                text=r.text,
                source_url=r.source_url,
                doc_title=r.doc_title,
                heading=r.heading,
                service=r.service,
            )
            for r in results
        ]
    )
