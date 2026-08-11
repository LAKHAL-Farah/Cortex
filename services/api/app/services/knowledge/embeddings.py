from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
import torch
from sentence_transformers import SentenceTransformer


DEFAULT_MODEL = "intfloat/multilingual-e5-base"
DEFAULT_DIMENSION = 768
DEFAULT_DEVICE = "cpu"


def resolve_device(requested_device: str) -> str:
    """
    Retourne le device réellement disponible.

    Si CUDA est demandé mais indisponible,
    le service revient automatiquement au CPU.
    """

    requested_device = requested_device.strip()

    if (
        requested_device.startswith("cuda")
        and not torch.cuda.is_available()
    ):
        return "cpu"

    return requested_device


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    """
    Charge et met en cache le modèle SentenceTransformer.
    """

    model_name = os.getenv(
        "EMBEDDING_MODEL",
        DEFAULT_MODEL,
    ).strip()

    requested_device = os.getenv(
        "EMBEDDING_DEVICE",
        DEFAULT_DEVICE,
    ).strip()

    device = resolve_device(
        requested_device
    )

    return SentenceTransformer(
        model_name,
        device=device,
    )


def get_embedding_dimension() -> int:
    return int(
        os.getenv(
            "EMBEDDING_DIMENSION",
            str(DEFAULT_DIMENSION),
        )
    )


def embed_passages(
    texts: list[str],
) -> np.ndarray:
    """
    Génère les embeddings des documents.

    E5 attend le préfixe :
        passage:
    """

    if not texts:
        return np.empty(
            (0, get_embedding_dimension()),
            dtype=np.float32,
        )

    model = get_embedding_model()

    passages = [
        f"passage: {text.strip()}"
        for text in texts
        if text.strip()
    ]

    if not passages:
        return np.empty(
            (0, get_embedding_dimension()),
            dtype=np.float32,
        )

    embeddings = model.encode(
        passages,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )

    return embeddings.astype(
        np.float32
    )


def embed_queries(
    queries: list[str],
) -> np.ndarray:
    """
    Génère les embeddings de plusieurs questions.

    E5 attend le préfixe :
        query:
    """

    cleaned_queries = [
        query.strip()
        for query in queries
        if query.strip()
    ]

    if not cleaned_queries:
        return np.empty(
            (0, get_embedding_dimension()),
            dtype=np.float32,
        )

    model = get_embedding_model()

    prepared_queries = [
        f"query: {query}"
        for query in cleaned_queries
    ]

    embeddings = model.encode(
        prepared_queries,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )

    return embeddings.astype(
        np.float32
    )


def embed_query(
    query: str,
) -> np.ndarray:
    """
    Génère l'embedding d'une seule question.
    """

    if not query.strip():
        raise ValueError(
            "La requête ne peut pas être vide."
        )

    embeddings = embed_queries(
        [query]
    )

    return embeddings[0]