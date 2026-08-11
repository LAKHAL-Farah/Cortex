from __future__ import annotations

import os
import sys
import time

import numpy as np
import torch

from app.services.knowledge.embeddings import (
    DEFAULT_DIMENSION,
    DEFAULT_MODEL,
    embed_passages,
    embed_queries,
    get_embedding_model,
    resolve_device,
)


def cosine_similarity(
    vector_a: np.ndarray,
    vector_b: np.ndarray,
) -> float:
    """
    Calcule la similarité cosinus entre deux vecteurs.
    """

    denominator = (
        np.linalg.norm(vector_a)
        * np.linalg.norm(vector_b)
    )

    if denominator == 0:
        raise ValueError(
            "Impossible de comparer un vecteur nul."
        )

    return float(
        np.dot(
            vector_a,
            vector_b,
        )
        / denominator
    )


def main() -> None:
    model_name = os.getenv(
        "EMBEDDING_MODEL",
        DEFAULT_MODEL,
    ).strip()

    requested_device = os.getenv(
        "EMBEDDING_DEVICE",
        "cpu",
    ).strip()

    expected_dimension = int(
        os.getenv(
            "EMBEDDING_DIMENSION",
            str(DEFAULT_DIMENSION),
        )
    )

    active_device = resolve_device(
        requested_device
    )

    print(
        f"MODEL={model_name}"
    )

    print(
        f"REQUESTED_DEVICE={requested_device}"
    )

    print(
        f"ACTIVE_DEVICE={active_device}"
    )

    if (
        requested_device.startswith("cuda")
        and active_device == "cpu"
    ):
        print(
            "WARNING=CUDA indisponible, utilisation du CPU"
        )

    if active_device.startswith("cuda"):
        print(
            f"GPU={torch.cuda.get_device_name(0)}"
        )

    print(
        "MODEL_LOADING=STARTED"
    )

    load_start = time.perf_counter()

    get_embedding_model()

    load_duration = (
        time.perf_counter()
        - load_start
    )

    documents = [
        (
            "Pour redémarrer le service cinder-backup "
            "après une panne RabbitMQ, vérifier la "
            "connectivité RabbitMQ, consulter les "
            "journaux du service, puis redémarrer "
            "cinder-backup."
        ),
        (
            "Pour vérifier que Loki reçoit les logs "
            "d'un nœud, vérifier Promtail, Loki et les "
            "labels associés au hostname."
        ),
        (
            "Nova Compute gère le cycle de vie des "
            "instances virtuelles sur les nœuds de "
            "calcul OpenStack."
        ),
    ]

    queries = [
        (
            "comment redémarrer cinder-backup "
            "après une panne RabbitMQ"
        ),
        (
            "comment vérifier les logs "
            "d'un nœud dans Loki"
        ),
    ]

    encode_start = time.perf_counter()

    document_embeddings = (
        embed_passages(
            documents
        )
    )

    query_embeddings = (
        embed_queries(
            queries
        )
    )

    encode_duration = (
        time.perf_counter()
        - encode_start
    )

    if document_embeddings.ndim != 2:
        raise RuntimeError(
            "Les embeddings documents "
            "doivent être une matrice."
        )

    if query_embeddings.ndim != 2:
        raise RuntimeError(
            "Les embeddings requêtes "
            "doivent être une matrice."
        )

    dimension = int(
        document_embeddings.shape[1]
    )

    if dimension != expected_dimension:
        raise RuntimeError(
            f"Dimension incorrecte : {dimension}. "
            f"Dimension attendue : "
            f"{expected_dimension}."
        )

    if (
        query_embeddings.shape[1]
        != expected_dimension
    ):
        raise RuntimeError(
            "Dimension incorrecte pour "
            "les requêtes."
        )

    print(
        "EMBEDDING_GENERATION=OK"
    )

    print(
        f"DIMENSION={dimension}"
    )

    print(
        f"DOCUMENT_COUNT={len(documents)}"
    )

    print(
        f"QUERY_COUNT={len(queries)}"
    )

    print(
        f"MODEL_LOAD_SECONDS={load_duration:.2f}"
    )

    print(
        f"ENCODING_SECONDS={encode_duration:.2f}"
    )

    first_document_norm = (
        np.linalg.norm(
            document_embeddings[0]
        )
    )

    print(
        "FIRST_DOCUMENT_NORM="
        f"{first_document_norm:.6f}"
    )

    for query_index, query in enumerate(
        queries
    ):
        scores = [
            cosine_similarity(
                query_embeddings[
                    query_index
                ],
                document_embedding,
            )
            for document_embedding
            in document_embeddings
        ]

        best_document_index = int(
            np.argmax(scores)
        )

        print()

        print(
            f"QUERY_{query_index + 1}="
            f"{query}"
        )

        print(
            "BEST_DOCUMENT_INDEX="
            f"{best_document_index}"
        )

        print(
            "BEST_SCORE="
            f"{scores[best_document_index]:.4f}"
        )

        print(
            "BEST_DOCUMENT="
            f"{documents[best_document_index]}"
        )

    print()

    print(
        "EMBEDDING_TEST=OK"
    )


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "EMBEDDING_TEST=FAILED: "
            f"{type(exc).__name__}: "
            f"{exc}",
            file=sys.stderr,
        )

        sys.exit(1)