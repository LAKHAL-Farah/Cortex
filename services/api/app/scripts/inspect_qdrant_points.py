from __future__ import annotations

from app.services.knowledge.qdrant_store import (
    get_collection_name,
    get_qdrant_client,
)


def main() -> None:
    client = get_qdrant_client()
    collection_name = get_collection_name()

    points, next_page = client.scroll(
        collection_name=collection_name,
        limit=100,
        with_payload=True,
        with_vectors=False,
    )

    print(f"COLLECTION={collection_name}")
    print(f"POINT_COUNT={len(points)}")
    print()

    for point in points:
        payload = point.payload or {}

        print("=" * 70)
        print(f"POINT_ID={point.id}")
        print(f"DOCUMENT_ID={payload.get('document_id')}")
        print(f"TITLE={payload.get('title')}")
        print(f"SOURCE_PATH={payload.get('source_path')}")
        print(f"SERVICE={payload.get('service')}")
        print(f"CHUNK_INDEX={payload.get('chunk_index')}")
        print(f"TOKEN_COUNT={payload.get('token_count')}")

        document_checksum = payload.get(
            "document_checksum"
        )

        chunk_checksum = payload.get(
            "chunk_checksum"
        )

        print(
            "DOCUMENT_CHECKSUM="
            f"{str(document_checksum)[:16]}..."
            if document_checksum
            else "DOCUMENT_CHECKSUM=None"
        )

        print(
            "CHUNK_CHECKSUM="
            f"{str(chunk_checksum)[:16]}..."
            if chunk_checksum
            else "CHUNK_CHECKSUM=None"
        )

        print(
            f"EMBEDDING_MODEL="
            f"{payload.get('embedding_model')}"
        )

        print(
            f"EMBEDDING_DIMENSION="
            f"{payload.get('embedding_dimension')}"
        )

        if "text" in payload:
            print("TEXT_FIELD=text")
            text = str(
                payload.get("text", "")
            )

        elif "content" in payload:
            print("TEXT_FIELD=content")
            text = str(
                payload.get("content", "")
            )

        else:
            print("TEXT_FIELD=MISSING")
            text = ""

        print(
            "TEXT_PREVIEW="
            + text[:180].replace(
                "\n",
                " ",
            )
        )

        print()

    print(f"NEXT_PAGE={next_page}")


if __name__ == "__main__":
    main()