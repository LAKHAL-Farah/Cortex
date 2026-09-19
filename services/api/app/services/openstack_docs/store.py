"""Qdrant Cloud client wrapper for the `cortex-openstack-official-docs`
collection (v1.0, see docs/architecture/adr-0010-openstack-expert-v1-
expansion.md).

Deliberately a *separate* Qdrant collection from `cortex-knowledge`
(../knowledge/qdrant_store.py) -- adr-0008's own §1 table drew this exact
line for the RAG/Knowledge Agent vs. the OpenStack Expert Agent: "how did
*we* fix this before" (our own runbooks/post-mortems) and "what does
OpenStack itself recommend" (official upstream docs) are different jobs,
grounded in different corpora, and mixing them into one retrieval index
would blur which citation type an answer is actually showing. Same reasoning
applies one level down here: the *catalog's* citations (a `doc_ref` pointing
at docs/knowledge/) and the *official-docs fallback's* citations (a
docs.openstack.org URL) need to stay visibly different sources too, which
is exactly what search.py's DocResult / openstack_expert.py's rendering
keeps distinct.

Reuses ../knowledge/qdrant_store.get_client() rather than opening a second
client -- same Qdrant Cloud cluster (one QDRANT_URL/QDRANT_API_KEY for the
whole deployment), just a different collection on it, so there's no reason
to duplicate the connection/caching logic.
"""
import os

from qdrant_client.http import models as qmodels

from ..knowledge.embeddings import EMBEDDING_DIMENSIONS
from ..knowledge.qdrant_store import get_client
from .scraper import DocChunk

QDRANT_OFFICIAL_DOCS_COLLECTION = os.environ.get(
    "QDRANT_OFFICIAL_DOCS_COLLECTION", "cortex-openstack-official-docs"
)


def ensure_collection(vector_size: int = EMBEDDING_DIMENSIONS) -> None:
    client = get_client()
    if client.collection_exists(QDRANT_OFFICIAL_DOCS_COLLECTION):
        return
    client.create_collection(
        collection_name=QDRANT_OFFICIAL_DOCS_COLLECTION,
        vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
    )
    # source_url mirrors source_path's role in ../knowledge/qdrant_store.py
    # -- lets a single page be re-ingested (delete_source + re-upsert)
    # without a full collection wipe. service is the coarse filter a
    # future category-scoped search (see search.py's module docstring)
    # would use.
    client.create_payload_index(
        collection_name=QDRANT_OFFICIAL_DOCS_COLLECTION,
        field_name="source_url",
        field_schema=qmodels.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=QDRANT_OFFICIAL_DOCS_COLLECTION,
        field_name="service",
        field_schema=qmodels.PayloadSchemaType.KEYWORD,
    )


def upsert_chunks(chunks: list[DocChunk], vectors: list[list[float]]) -> int:
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors must be the same length")
    if not chunks:
        return 0

    points = [
        qmodels.PointStruct(
            id=chunk.id,
            vector=vector,
            payload={
                "text": chunk.text,
                "source_url": chunk.source_url,
                "doc_title": chunk.doc_title,
                "heading": chunk.heading,
                "service": chunk.service,
                "chunk_index": chunk.chunk_index,
            },
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    client = get_client()
    client.upsert(collection_name=QDRANT_OFFICIAL_DOCS_COLLECTION, points=points, wait=True)
    return len(points)


def delete_source(source_url: str) -> None:
    """Removes every chunk belonging to one page -- used when a seed URL
    is dropped from sources.py, or re-ingested under a changed shape, so
    stale vectors don't linger and get retrieved after the source is gone."""
    client = get_client()
    client.delete(
        collection_name=QDRANT_OFFICIAL_DOCS_COLLECTION,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="source_url", match=qmodels.MatchValue(value=source_url))]
            )
        ),
    )


def search(query_vector: list[float], top_k: int = 5, service: str | None = None):
    client = get_client()
    query_filter = None
    if service:
        query_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(key="service", match=qmodels.MatchValue(value=service))]
        )
    return client.query_points(
        collection_name=QDRANT_OFFICIAL_DOCS_COLLECTION,
        query=query_vector,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    ).points


def collection_info() -> dict | None:
    client = get_client()
    if not client.collection_exists(QDRANT_OFFICIAL_DOCS_COLLECTION):
        return None
    info = client.get_collection(QDRANT_OFFICIAL_DOCS_COLLECTION)
    return {
        "collection": QDRANT_OFFICIAL_DOCS_COLLECTION,
        "points_count": info.points_count,
        "vectors_count": info.vectors_count,
        "status": info.status.value if hasattr(info.status, "value") else str(info.status),
    }
