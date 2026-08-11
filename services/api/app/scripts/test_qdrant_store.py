from __future__ import annotations

from app.services.knowledge.qdrant_store import (
    get_collection_name,
    get_qdrant_client,
    validate_collection,
)


def main() -> None:
    print(
        f"COLLECTION={get_collection_name()}"
    )

    print(
        "QDRANT_CONNECTION=STARTED"
    )

    client = get_qdrant_client()

    collection_name = (
        get_collection_name()
    )

    info = client.get_collection(
        collection_name
    )

    print(
        "QDRANT_CONNECTION=OK"
    )

    print(
        f"STATUS={info.status}"
    )

    print(
        f"POINTS={info.points_count}"
    )

    print(
        "COLLECTION_VALIDATION=STARTED"
    )

    validate_collection()

    print(
        "COLLECTION_VALIDATION=OK"
    )


if __name__ == "__main__":
    main()