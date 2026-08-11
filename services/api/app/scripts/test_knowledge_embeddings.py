from __future__ import annotations

import os
import sys

import numpy as np

from app.services.knowledge.chunker import (
    chunk_documents,
)
from app.services.knowledge.document_loader import (
    load_documents,
)
from app.services.knowledge.embeddings import (
    embed_passages,
    embed_query,
    get_embedding_model,
)


def cosine_similarity(
    vector_a: np.ndarray,
    vector_b: np.ndarray,
) -> float:
    denominator = (
        np.linalg.norm(vector_a)
        * np.linalg.norm(vector_b)
    )

    if denominator == 0:
        raise ValueError(
            "Impossible de comparer un vecteur nul."
        )

    return float(
        np.dot(vector_a, vector_b)
        / denominator
    )


def main() -> None:
    documents_directory = os.getenv(
        "KNOWLEDGE_DOCUMENTS_DIR",
        "documents/runbooks",
    )

    chunk_size = int(
        os.getenv(
            "KNOWLEDGE_CHUNK_SIZE",
            "400",
        )
    )

    chunk_overlap = int(
        os.getenv(
            "KNOWLEDGE_CHUNK_OVERLAP",
            "70",
        )
    )

    expected_dimension = int(
        os.getenv(
            "EMBEDDING_DIMENSION",
            "768",
        )
    )

    print(
        f"DOCUMENTS_DIRECTORY={documents_directory}"
    )

    print(
        f"CHUNK_SIZE={chunk_size}"
    )

    print(
        f"CHUNK_OVERLAP={chunk_overlap}"
    )

    print(
        "MODEL_LOADING=STARTED"
    )

    model = get_embedding_model()

    print(
        "MODEL_LOADING=OK"
    )

    documents = load_documents(
        documents_directory
    )

    chunks = chunk_documents(
        documents=documents,
        tokenizer=model.tokenizer,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    print(
        f"DOCUMENT_COUNT={len(documents)}"
    )

    print(
        f"CHUNK_COUNT={len(chunks)}"
    )

    if not chunks:
        raise RuntimeError(
            "Aucun chunk généré."
        )

    chunk_texts = [
        chunk.text
        for chunk in chunks
    ]

    print(
        "PASSAGE_EMBEDDING=STARTED"
    )

    embeddings = embed_passages(
        chunk_texts
    )

    print(
        "PASSAGE_EMBEDDING=OK"
    )

    print(
        f"VECTOR_SHAPE={embeddings.shape}"
    )

    if embeddings.shape[0] != len(chunks):
        raise RuntimeError(
            "Le nombre de vecteurs ne correspond "
            "pas au nombre de chunks."
        )

    if embeddings.shape[1] != expected_dimension:
        raise RuntimeError(
            f"Dimension incorrecte : "
            f"{embeddings.shape[1]} "
            f"au lieu de {expected_dimension}."
        )

    print(
        f"DIMENSION={embeddings.shape[1]}"
    )

    print(
        "DIMENSION_CHECK=OK"
    )

    print()

    print("CHUNKS:")

    for index, chunk in enumerate(chunks):
        vector_norm = np.linalg.norm(
            embeddings[index]
        )

        print(
            f"- [{index}] "
            f"{chunk.document_id} "
            f"chunk={chunk.chunk_index} "
            f"tokens={chunk.token_count} "
            f"norm={vector_norm:.6f}"
        )

    test_queries = [
        (
            "Pourquoi un nœud ne remonte plus "
            "ses métriques dans Prometheus ?"
        ),
        (
            "Comment vérifier les logs "
            "d'un nœud dans Loki ?"
        ),
        (
            "Que faire si cinder-backup "
            "ne fonctionne plus après "
            "une panne RabbitMQ ?"
        ),
        (
            "Pourquoi une instance OpenStack "
            "est en état ERROR ?"
        ),
    ]

    print()
    print("SEMANTIC_TESTS:")

    for query_index, query in enumerate(
        test_queries,
        start=1,
    ):
        query_embedding = embed_query(
            query
        )

        scores = [
            cosine_similarity(
                query_embedding,
                document_embedding,
            )
            for document_embedding
            in embeddings
        ]

        best_index = int(
            np.argmax(scores)
        )

        best_chunk = chunks[
            best_index
        ]

        best_score = scores[
            best_index
        ]

        print()
        print(
            f"QUERY_{query_index}={query}"
        )

        print(
            f"BEST_DOCUMENT="
            f"{best_chunk.document_id}"
        )

        print(
            f"BEST_SERVICE="
            f"{best_chunk.payload['service']}"
        )

        print(
            f"BEST_CHUNK="
            f"{best_chunk.chunk_index}"
        )

        print(
            f"BEST_SCORE="
            f"{best_score:.4f}"
        )

        print(
            f"SOURCE="
            f"{best_chunk.payload['source_path']}"
        )

    print()
    print(
        "KNOWLEDGE_EMBEDDING_TEST=OK"
    )


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "KNOWLEDGE_EMBEDDING_TEST=FAILED: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)