from __future__ import annotations

import os

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

    print(
        f"DOCUMENTS_DIRECTORY="
        f"{documents_directory}"
    )

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

    if len(chunks) != len(vectors):
        raise RuntimeError(
            "Nombre de chunks différent "
            "du nombre de vecteurs."
        )

    points = [
        build_point(
            chunk=chunk,
            vector=vector,
        )
        for chunk, vector
        in zip(
            chunks,
            vectors,
            strict=True,
        )
    ]

    print()
    print(
        f"DOCUMENTS={len(documents)}"
    )

    print(
        f"CHUNKS={len(chunks)}"
    )

    print(
        f"POINTS_BUILT={len(points)}"
    )

    print()

    for point in points:
        payload = point.payload or {}

        print("=" * 70)

        print(
            f"POINT_ID="
            f"{point.id}"
        )

        print(
            "DOCUMENT_ID="
            f"{payload.get('document_id')}"
        )

        print(
            "TITLE="
            f"{payload.get('title')}"
        )

        print(
            "SERVICE="
            f"{payload.get('service')}"
        )

        print(
            "SOURCE_PATH="
            f"{payload.get('source_path')}"
        )

        print(
            "CHUNK_INDEX="
            f"{payload.get('chunk_index')}"
        )

        print(
            "TOKEN_COUNT="
            f"{payload.get('token_count')}"
        )

        print(
            "DOCUMENT_CHECKSUM="
            f"{str(payload.get('document_checksum'))[:16]}..."
        )

        print(
            "CHUNK_CHECKSUM="
            f"{str(payload.get('chunk_checksum'))[:16]}..."
        )

        print(
            "EMBEDDING_MODEL="
            f"{payload.get('embedding_model')}"
        )

        print(
            "EMBEDDING_DIMENSION="
            f"{payload.get('embedding_dimension')}"
        )

        print(
            "VECTOR_LENGTH="
            f"{len(point.vector)}"
        )

        print()

    print(
        "POINT_BUILD_TEST=OK"
    )


if __name__ == "__main__":
    main()