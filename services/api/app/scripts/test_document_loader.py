from app.services.knowledge.document_loader import (
    load_documents,
)


documents = load_documents(
    "documents/runbooks",
)


print()
print("DOCUMENTS_FOUND =", len(documents))
print()


for document in documents:

    print("=" * 70)

    print(
        "DOCUMENT_ID       =",
        document.document_id,
    )

    print(
        "TITLE             =",
        document.title,
    )

    print(
        "SOURCE_PATH       =",
        document.source_path,
    )

    print(
        "SOURCE_NAME       =",
        document.source_name,
    )

    print(
        "DOCUMENT_CHECKSUM =",
        document.document_checksum[:16] + "...",
    )

    print(
        "SERVICE           =",
        document.metadata["service"],
    )

    print(
        "ENVIRONMENT       =",
        document.metadata["environment"],
    )

    print(
        "CRITICALITY       =",
        document.metadata["criticality"],
    )

    print(
        "DOCUMENT_TYPE     =",
        document.metadata["document_type"],
    )

    print(
        "LANGUAGE          =",
        document.metadata["language"],
    )

    print(
        "EXTENSION         =",
        document.metadata["extension"],
    )

    print(
        "TEXT_LENGTH       =",
        len(document.text),
    )

    print()

    print("PREVIEW:")

    print(
        document.text[:200]
        .replace("\n", " ")
    )

    print()