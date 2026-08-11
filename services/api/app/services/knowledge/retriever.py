from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
)

from .embeddings import embed_query
from .qdrant_store import (
    get_collection_name,
    get_qdrant_client,
)


@dataclass(frozen=True)
class RetrievedChunk:
    point_id: str
    score: float

    document_id: str
    title: str
    text: str

    source_path: str
    service: str
    chunk_index: int

    metadata: dict[str, Any]


def build_filter(
    *,
    service: str | None = None,
    environment: str | None = None,
    document_type: str | None = None,
    language: str | None = None,
) -> Filter | None:

    conditions: list[FieldCondition] = []

    if service:
        conditions.append(
            FieldCondition(
                key="service",
                match=MatchValue(
                    value=service.lower(),
                ),
            )
        )

    if environment:
        conditions.append(
            FieldCondition(
                key="environment",
                match=MatchValue(
                    value=environment.lower(),
                ),
            )
        )

    if document_type:
        conditions.append(
            FieldCondition(
                key="document_type",
                match=MatchValue(
                    value=document_type.lower(),
                ),
            )
        )

    if language:
        conditions.append(
            FieldCondition(
                key="language",
                match=MatchValue(
                    value=language.lower(),
                ),
            )
        )

    if not conditions:
        return None

    return Filter(
        must=conditions
    )


def search_knowledge(
    query: str,
    *,
    limit: int = 5,
    service: str | None = None,
    environment: str | None = None,
    document_type: str | None = None,
    language: str | None = None,
) -> list[RetrievedChunk]:

    if not query.strip():
        raise ValueError(
            "La requête ne peut pas être vide."
        )

    if limit <= 0:
        raise ValueError(
            "limit doit être supérieur à zéro."
        )

    client = get_qdrant_client()

    collection_name = (
        get_collection_name()
    )

    query_vector = embed_query(
        query
    )

    query_filter = build_filter(
        service=service,
        environment=environment,
        document_type=document_type,
        language=language,
    )

    result = client.query_points(
        collection_name=collection_name,
        query=query_vector.tolist(),
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )

    retrieved: list[RetrievedChunk] = []

    for point in result.points:
        payload = point.payload or {}

        retrieved.append(
            RetrievedChunk(
                point_id=str(point.id),
                score=float(point.score),

                document_id=str(
                    payload.get(
                        "document_id",
                        "",
                    )
                ),

                title=str(
                    payload.get(
                        "title",
                        "",
                    )
                ),

                text=str(
                    payload.get(
                        "text",
                        "",
                    )
                ),

                source_path=str(
                    payload.get(
                        "source_path",
                        "",
                    )
                ),

                service=str(
                    payload.get(
                        "service",
                        "",
                    )
                ),

                chunk_index=int(
                    payload.get(
                        "chunk_index",
                        0,
                    )
                ),

                metadata=payload,
            )
        )

    return retrieved