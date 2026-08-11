from __future__ import annotations

import os
from dataclasses import dataclass

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
    find_point_ids_by_document_id,
    upsert_points,
    validate_collection,
)


LEGACY_DOCUMENT_IDS = {
    "loki-verify-compute1",
}


@dataclass(frozen=True)
class DocumentIngestionResult:
    document_id: str
    status: str
    old_points_deleted: int
    new_points_inserted: int


@dataclass(frozen=True)
class IngestionResult:
    documents_found: int
    chunks_generated: int
    inserted: int
    skipped: int
    updated: int
    legacy_deleted: int
    documents: list[DocumentIngestionResult]


def _existing_document_checksum(
    document_id: str,
) -> str | None:
    """
    Retourne le document_checksum actuellement stocké
    dans Qdrant pour ce document.

    None signifie que le document n'existe pas encore.
    """

    from app.services.knowledge.qdrant_store import (
        get_collection_name,
        get_qdrant_client,
    )

    client = get_qdrant_client()

    points, _ = client.scroll(
        collection_name=get_collection_name(),
        scroll_filter={
            "must": [
                {
                    "key": "document_id",
                    "match": {
                        "value": document_id,
                    },
                }
            ]
        },
        limit=1,
        with_payload=True,
        with_vectors=False,
    )

    if not points:
        return None

    payload = points[0].payload or {}

    checksum = payload.get(
        "document_checksum"
    )

    if checksum is None:
        return None

    return str(checksum)


def ingest_knowledge_documents() -> IngestionResult:
    """
    Lance l'ingestion Knowledge à la demande.

    Comportement :
    - document absent       -> insertion
    - document inchangé     -> skip
    - document modifié      -> suppression anciens chunks + réinsertion
    """

    validate_collection()

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

    model = get_embedding_model()

    documents = load_documents(
        documents_directory
    )

    result_items: list[
        DocumentIngestionResult
    ] = []

    inserted = 0
    skipped = 0
    updated = 0
    legacy_deleted = 0

    # Nettoyage exceptionnel des anciens IDs.
    for legacy_id in LEGACY_DOCUMENT_IDS:
        legacy_deleted += (
            delete_document_points(
                legacy_id
            )
        )

    total_chunks = 0

    for document in documents:
        current_checksum = (
            _existing_document_checksum(
                document.document_id
            )
        )

        # --------------------------------------------------
        # Document inchangé
        # --------------------------------------------------

        if (
            current_checksum
            == document.document_checksum
        ):
            existing_points = (
                find_point_ids_by_document_id(
                    document.document_id
                )
            )

            skipped += 1
            total_chunks += len(
                existing_points
            )

            result_items.append(
                DocumentIngestionResult(
                    document_id=(
                        document.document_id
                    ),
                    status="skipped",
                    old_points_deleted=0,
                    new_points_inserted=0,
                )
            )

            continue

        # --------------------------------------------------
        # Nouveau document ou document modifié
        # --------------------------------------------------

        chunks = chunk_documents(
            documents=[document],
            tokenizer=model.tokenizer,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        total_chunks += len(chunks)

        vectors = embed_passages(
            [
                chunk.text
                for chunk in chunks
            ]
        )

        points = [
            build_point(
                chunk=chunk,
                vector=vector,
            )
            for chunk, vector in zip(
                chunks,
                vectors,
                strict=True,
            )
        ]

        old_points_deleted = (
            delete_document_points(
                document.document_id
            )
        )

        upsert_points(
            points
        )

        if current_checksum is None:
            status_value = "inserted"
            inserted += 1

        else:
            status_value = "updated"
            updated += 1

        result_items.append(
            DocumentIngestionResult(
                document_id=(
                    document.document_id
                ),
                status=status_value,
                old_points_deleted=(
                    old_points_deleted
                ),
                new_points_inserted=len(
                    points
                ),
            )
        )

    return IngestionResult(
        documents_found=len(documents),
        chunks_generated=total_chunks,
        inserted=inserted,
        skipped=skipped,
        updated=updated,
        legacy_deleted=legacy_deleted,
        documents=result_items,
    )