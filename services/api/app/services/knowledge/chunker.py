from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from transformers import PreTrainedTokenizerBase

from .document_loader import LoadedDocument


@dataclass(frozen=True)
class DocumentChunk:
    point_id_source: str

    document_id: str
    chunk_index: int

    text: str
    token_count: int

    chunk_checksum: str

    payload: dict[str, Any]


def build_chunk_checksum(text: str) -> str:
    """
    Crée une empreinte SHA256 stable du contenu du chunk.
    """

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def chunk_document(
    document: LoadedDocument,
    tokenizer: PreTrainedTokenizerBase,
    chunk_size: int = 400,
    chunk_overlap: int = 70,
) -> list[DocumentChunk]:
    """
    Découpe un document en blocs de tokens avec chevauchement.

    Exemple :
        chunk_size = 400
        chunk_overlap = 70

    Le premier chunk contient au maximum 400 tokens.
    Le chunk suivant reprend les 70 derniers tokens du précédent.
    """

    if chunk_size <= 0:
        raise ValueError(
            "chunk_size doit être supérieur à zéro."
        )

    if chunk_overlap < 0:
        raise ValueError(
            "chunk_overlap ne peut pas être négatif."
        )

    if chunk_overlap >= chunk_size:
        raise ValueError(
            "chunk_overlap doit être inférieur à chunk_size."
        )

    token_ids = tokenizer.encode(
        document.text,
        add_special_tokens=False,
    )

    if not token_ids:
        return []

    step = chunk_size - chunk_overlap

    chunks: list[DocumentChunk] = []

    for start in range(
        0,
        len(token_ids),
        step,
    ):
        end = min(
            start + chunk_size,
            len(token_ids),
        )

        current_token_ids = token_ids[
            start:end
        ]

        chunk_text = tokenizer.decode(
            current_token_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        ).strip()

        if chunk_text:
            chunk_index = len(chunks)

            chunk_checksum = (
                build_chunk_checksum(
                    chunk_text
                )
            )

            payload: dict[str, Any] = {
                "document_id":
                    document.document_id,

                "title":
                    document.title,

                "source_path":
                    document.source_path,

                "source_name":
                    document.source_name,

                "document_checksum":
                    document.document_checksum,

                "chunk_checksum":
                    chunk_checksum,

                "chunk_index":
                    chunk_index,

                "token_count":
                    len(current_token_ids),

                "text":
                    chunk_text,

                **document.metadata,
            }

            point_id_source = (
                f"{document.document_id}:"
                f"{chunk_index}:"
                f"{chunk_checksum}"
            )

            chunks.append(
                DocumentChunk(
                    point_id_source=(
                        point_id_source
                    ),

                    document_id=(
                        document.document_id
                    ),

                    chunk_index=(
                        chunk_index
                    ),

                    text=(
                        chunk_text
                    ),

                    token_count=(
                        len(
                            current_token_ids
                        )
                    ),

                    chunk_checksum=(
                        chunk_checksum
                    ),

                    payload=payload,
                )
            )

        if end >= len(token_ids):
            break

    return chunks


def chunk_documents(
    documents: list[LoadedDocument],
    tokenizer: PreTrainedTokenizerBase,
    chunk_size: int = 400,
    chunk_overlap: int = 70,
) -> list[DocumentChunk]:
    """
    Découpe plusieurs documents avec le même tokenizer.
    """

    chunks: list[DocumentChunk] = []

    for document in documents:

        document_chunks = (
            chunk_document(
                document=document,
                tokenizer=tokenizer,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        )

        chunks.extend(
            document_chunks
        )

    return chunks