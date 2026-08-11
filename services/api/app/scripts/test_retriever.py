from __future__ import annotations

import sys

from app.services.knowledge.retriever import (
    search_knowledge,
)


TEST_QUERIES = [
    (
        "Pourquoi un nœud ne remonte plus "
        "ses métriques dans Prometheus ?"
    ),
    (
        "Comment vérifier les logs "
        "d'un nœud dans Loki ?"
    ),
    (
        "Que faire après une panne RabbitMQ "
        "qui affecte cinder-backup ?"
    ),
    (
        "Pourquoi une instance OpenStack "
        "est en état ERROR ?"
    ),
]


def main() -> None:
    for index, query in enumerate(
        TEST_QUERIES,
        start=1,
    ):
        print()
        print("=" * 70)

        print(
            f"QUERY_{index}={query}"
        )

        results = search_knowledge(
            query,
            limit=4,
        )

        if not results:
            print("NO_RESULTS")
            continue

        for rank, result in enumerate(
            results,
            start=1,
        ):
            print()
            print(
                f"RANK={rank}"
            )

            print(
                f"SCORE={result.score:.4f}"
            )

            print(
                f"DOCUMENT_ID="
                f"{result.document_id}"
            )

            print(
                f"SERVICE="
                f"{result.service}"
            )

            print(
                f"SOURCE="
                f"{result.source_path}"
            )

            print(
                "TEXT_PREVIEW="
                f"{result.text[:180]}"
            )

    print()
    print(
        "RETRIEVER_TEST=OK"
    )


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "RETRIEVER_TEST=FAILED: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)