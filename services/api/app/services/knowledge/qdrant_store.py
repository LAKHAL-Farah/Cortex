"""Qdrant Cloud client wrapper for the `cortex-knowledge` collection.

This project uses Qdrant Cloud (not a self-hosted container) as the vector
store -- QDRANT_URL/QDRANT_API_KEY point at a managed cluster, so there is no
`qdrant` service in docker-compose. See infra/.env.example for the variables
this module reads.
"""
import os
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from .embeddings import EMBEDDING_DIMENSIONS
from .loader import KnowledgeChunk

QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "cortex-knowledge")


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    url = os.environ.get("QDRANT_URL")
    if not url:
        raise RuntimeError("QDRANT_URL is not set -- required to reach Qdrant Cloud")
    return QdrantClient(url=url, api_key=os.environ.get("QDRANT_API_KEY"), timeout=30)


def ensure_collection(vector_size: int = EMBEDDING_DIMENSIONS) -> None:
    """Creates the collection if it doesn't exist yet. Safe to call on every
    ingest run -- collection creation is a no-op when it already exists with
    a matching config."""
    client = get_client()
    if client.collection_exists(QDRANT_COLLECTION):
        return
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=qmodels.VectorParams(
            size=vector_size,
            distance=qmodels.Distance.COSINE,
        ),
    )
    # source_path is used to filter/replace all chunks for a single knowledge
    # file (e.g. re-ingesting just service-detail/nova.md after an edit)
    # without needing a full collection wipe.
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="source_path",
        field_schema=qmodels.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="category",
        field_schema=qmodels.PayloadSchemaType.KEYWORD,
    )


def upsert_chunks(chunks: list[KnowledgeChunk], vectors: list[list[float]]) -> int:
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
                "source_path": chunk.source_path,
                "doc_title": chunk.doc_title,
                "heading": chunk.heading,
                "category": chunk.category,
                "chunk_index": chunk.chunk_index,
            },
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    client = get_client()
    client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)
    return len(points)


def delete_source(source_path: str) -> None:
    """Removes every chunk belonging to one knowledge file -- used when a file
    is deleted from docs/knowledge/ so stale vectors don't linger and get
    retrieved after the source doc is gone."""
    client = get_client()
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="source_path", match=qmodels.MatchValue(value=source_path))]
            )
        ),
    )


def search(query_vector: list[float], top_k: int = 5, category: str | None = None):
    client = get_client()
    query_filter = None
    if category:
        query_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(key="category", match=qmodels.MatchValue(value=category))]
        )
    return client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vector,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    ).points


def collection_info() -> dict | None:
    client = get_client()
    if not client.collection_exists(QDRANT_COLLECTION):
        return None
    info = client.get_collection(QDRANT_COLLECTION)
    return {
        "collection": QDRANT_COLLECTION,
        "points_count": info.points_count,
        "vectors_count": info.vectors_count,
        "status": info.status.value if hasattr(info.status, "value") else str(info.status),
    }
