from __future__ import annotations

import os
import uuid
from functools import lru_cache

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
)

from .chunker import DocumentChunk


DEFAULT_COLLECTION = "cortex_knowledge_v1"
DEFAULT_DIMENSION = 768


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """
    Crée et met en cache le client Qdrant.
    """

    url = os.getenv(
        "QDRANT_URL",
        "",
    ).strip()

    api_key = os.getenv(
        "QDRANT_API_KEY",
        "",
    ).strip()

    if not url:
        raise RuntimeError(
            "QDRANT_URL n'est pas défini."
        )

    if not api_key:
        raise RuntimeError(
            "QDRANT_API_KEY n'est pas défini."
        )

    return QdrantClient(
        url=url,
        api_key=api_key,
        timeout=30,
        check_compatibility=False,
    )


def get_collection_name() -> str:
    """
    Retourne le nom de la collection Knowledge.
    """

    return os.getenv(
        "QDRANT_COLLECTION",
        DEFAULT_COLLECTION,
    ).strip()


def get_expected_dimension() -> int:
    """
    Retourne la dimension attendue des embeddings.
    """

    return int(
        os.getenv(
            "EMBEDDING_DIMENSION",
            str(DEFAULT_DIMENSION),
        )
    )


def validate_collection() -> None:
    """
    Vérifie que la collection existe et correspond
    aux paramètres attendus par Cortex :
    - vecteur unique
    - dimension correcte
    - distance Cosine
    """

    client = get_qdrant_client()
    collection_name = get_collection_name()

    info = client.get_collection(
        collection_name
    )

    vector_params = (
        info.config.params.vectors
    )

    if isinstance(vector_params, dict):
        raise RuntimeError(
            "La collection utilise plusieurs vecteurs "
            "nommés. Cortex attend actuellement "
            "un vecteur unique."
        )

    expected_dimension = (
        get_expected_dimension()
    )

    if vector_params.size != expected_dimension:
        raise RuntimeError(
            f"Dimension Qdrant incorrecte : "
            f"{vector_params.size}. "
            f"Attendu : {expected_dimension}."
        )

    if vector_params.distance != Distance.COSINE:
        raise RuntimeError(
            f"Distance Qdrant incorrecte : "
            f"{vector_params.distance}. "
            f"Attendu : Cosine."
        )


def build_point_id(
    chunk: DocumentChunk,
) -> str:
    """
    Génère un UUID déterministe.

    Même :
        document_id
        chunk_index
        chunk_checksum

    => même UUID.
    """

    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            chunk.point_id_source,
        )
    )


def build_point(
    chunk: DocumentChunk,
    vector: np.ndarray,
) -> PointStruct:
    """
    Transforme un DocumentChunk et son embedding
    en point Qdrant.
    """

    expected_dimension = (
        get_expected_dimension()
    )

    if vector.ndim != 1:
        raise ValueError(
            "Le vecteur doit être "
            "un tableau à une dimension."
        )

    if vector.shape[0] != expected_dimension:
        raise ValueError(
            f"Dimension du vecteur incorrecte : "
            f"{vector.shape[0]} "
            f"au lieu de {expected_dimension}."
        )

    payload = {
        **chunk.payload,

        "embedding_model": os.getenv(
            "EMBEDDING_MODEL",
            "intfloat/multilingual-e5-base",
        ).strip(),

        "embedding_dimension":
            expected_dimension,
    }

    return PointStruct(
        id=build_point_id(
            chunk
        ),
        vector=vector.tolist(),
        payload=payload,
    )


def find_point_ids_by_document_id(
    document_id: str,
) -> list[str]:
    """
    Recherche tous les points Qdrant appartenant
    à un document_id.
    """

    client = get_qdrant_client()
    collection_name = get_collection_name()

    points, _ = client.scroll(
        collection_name=collection_name,

        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key="document_id",
                    match=MatchValue(
                        value=document_id,
                    ),
                )
            ]
        ),

        limit=1000,
        with_payload=False,
        with_vectors=False,
    )

    return [
        str(point.id)
        for point in points
    ]


def delete_document_points(
    document_id: str,
) -> int:
    """
    Supprime tous les chunks associés
    à un document_id.

    Retourne le nombre de points supprimés.
    """

    client = get_qdrant_client()
    collection_name = get_collection_name()

    point_ids = (
        find_point_ids_by_document_id(
            document_id
        )
    )

    if not point_ids:
        return 0

    client.delete(
        collection_name=collection_name,
        points_selector=PointIdsList(
            points=point_ids
        ),
        wait=True,
    )

    return len(point_ids)


def upsert_points(
    points: list[PointStruct],
) -> None:
    """
    Insère ou met à jour une liste
    de points dans Qdrant.
    """

    if not points:
        return

    client = get_qdrant_client()
    collection_name = get_collection_name()

    client.upsert(
        collection_name=collection_name,
        points=points,
        wait=True,
    )