from __future__ import annotations

import os
import sys

from app.services.knowledge.chunker import (
    chunk_documents,
)
from app.services.knowledge.document_loader import (
    load_documents,
)
from app.services.knowledge.embeddings import (
    embed_passages,
    get_embedding_model,
)
from app.services.knowledge.qdrant_store import (
    build_point,
    delete_document_points,
    upsert_points,
    validate_collection,
)


LEGACY_DOCUMENT_IDS = [
    "loki-verify-compute1",
]


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

    print("COLLECTION_VALIDATION=STARTED")
    validate_collection()
    print("COLLECTION_VALIDATION=OK")

    model = get_embedding_model()

    documents = load_documents(
        documents_directory
    )

    chunks = chunk_documents(
        documents=documents,
        tokenizer=model.tokenizer,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    vectors = embed_passages(
        [
            chunk.text
            for chunk in chunks
        ]
    )

    points_by_document: dict[
        str,
        list,
    ] = {}

    for chunk, vector in zip(
        chunks,
        vectors,
        strict=True,
    ):
        point = build_point(
            chunk=chunk,
            vector=vector,
        )

        points_by_document.setdefault(
            chunk.document_id,
            [],
        ).append(point)

    print()
    print(
        f"DOCUMENTS={len(documents)}"
    )

    print(
        f"CHUNKS={len(chunks)}"
    )

    print()

    # Nettoyage des anciens document_id
    # qui n'existent plus dans la nouvelle structure.
    for legacy_document_id in LEGACY_DOCUMENT_IDS:
        deleted = delete_document_points(
            legacy_document_id
        )

        print(
            f"LEGACY_DELETE "
            f"{legacy_document_id} "
            f"DELETED={deleted}"
        )

    # Remplacement propre document par document.
    for document_id, points in (
        points_by_document.items()
    ):
        deleted = delete_document_points(
            document_id
        )

        print(
            f"DOCUMENT={document_id} "
            f"OLD_POINTS_DELETED={deleted}"
        )

        upsert_points(
            points
        )

        print(
            f"DOCUMENT={document_id} "
            f"NEW_POINTS_INSERTED={len(points)}"
        )

    print()
    print("INGESTION=OK")


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "INGESTION=FAILED: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)