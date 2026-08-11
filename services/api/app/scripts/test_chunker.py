import os

from transformers import AutoTokenizer

from app.services.knowledge.chunker import (
    chunk_documents,
)
from app.services.knowledge.document_loader import (
    load_documents,
)


model_name = os.getenv(
    "EMBEDDING_MODEL",
    "intfloat/multilingual-e5-base",
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


print("MODEL =", model_name)
print("Loading tokenizer...")


tokenizer = AutoTokenizer.from_pretrained(
    model_name
)


print("TOKENIZER = OK")
print()


documents = load_documents(
    "documents/runbooks"
)


chunks = chunk_documents(
    documents=documents,
    tokenizer=tokenizer,
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
)


print("DOCUMENTS =", len(documents))
print("CHUNKS    =", len(chunks))
print("SIZE      =", chunk_size)
print("OVERLAP   =", chunk_overlap)
print()


for chunk in chunks:

    print("=" * 70)

    print(
        "DOCUMENT_ID       =",
        chunk.document_id,
    )

    print(
        "CHUNK_INDEX       =",
        chunk.chunk_index,
    )

    print(
        "TOKEN_COUNT       =",
        chunk.token_count,
    )

    print(
        "SERVICE           =",
        chunk.payload["service"],
    )

    print(
        "SOURCE_PATH       =",
        chunk.payload["source_path"],
    )

    print(
        "DOCUMENT_CHECKSUM =",
        chunk.payload["document_checksum"][:16] + "...",
    )

    print(
        "CHUNK_CHECKSUM    =",
        chunk.chunk_checksum[:16] + "...",
    )

    print(
        "POINT_ID_SOURCE   =",
        chunk.point_id_source[:70] + "...",
    )

    print()

    print("TEXT PREVIEW:")

    print(
        chunk.text[:300]
        .replace("\n", " ")
    )

    print()