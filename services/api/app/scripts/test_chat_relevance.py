from __future__ import annotations

import os

from app.services.knowledge.retriever import search_knowledge


DEFAULT_THRESHOLD = float(
    os.getenv(
        "CHAT_MIN_RELEVANCE_SCORE",
        "0.82",
    )
)


POSITIVE_QUESTIONS = [
    "Pourquoi un nœud ne remonte plus ses métriques dans Prometheus ?",
    "Comment vérifier si node_exporter fonctionne correctement ?",
    "Que faire si les logs d'un nœud ne remontent plus dans Loki ?",
    "Comment diagnostiquer une instance OpenStack en état ERROR ?",
    "Comment récupérer cinder-backup après un problème RabbitMQ ?",
]


NEGATIVE_QUESTIONS = [
    "Quel est le mot de passe Wi-Fi du bureau ?",
    "Quelle est la capitale du Japon ?",
    "Comment installer Docker Desktop sur Windows ?",
    "Quel temps fera-t-il demain ?",
    "Comment créer une présentation PowerPoint ?",
]


def test_question(
    question: str,
    expected: str,
) -> dict:
    results = search_knowledge(
        query=question,
        limit=3,
        environment="production",
        document_type="runbook",
        language="fr",
    )

    if not results:
        return {
            "question": question,
            "expected": expected,
            "score": None,
            "document_id": None,
            "decision": "REJECT",
        }

    best = results[0]

    decision = (
        "ACCEPT"
        if best.score >= DEFAULT_THRESHOLD
        else "REJECT"
    )

    return {
        "question": question,
        "expected": expected,
        "score": best.score,
        "document_id": best.document_id,
        "decision": decision,
    }


def main() -> None:
    print("=" * 80)
    print("CORTEX - CHAT RELEVANCE CALIBRATION")
    print("=" * 80)
    print()

    print(
        f"CURRENT_THRESHOLD = "
        f"{DEFAULT_THRESHOLD:.4f}"
    )
    print()

    all_results = []

    print("POSITIVE QUESTIONS")
    print("-" * 80)

    for question in POSITIVE_QUESTIONS:
        result = test_question(
            question,
            expected="ACCEPT",
        )

        all_results.append(result)

        print()
        print("QUESTION =", result["question"])
        print(
            "TOP_DOCUMENT =",
            result["document_id"],
        )

        if result["score"] is not None:
            print(
                "TOP_SCORE =",
                f'{result["score"]:.6f}',
            )
        else:
            print("TOP_SCORE = None")

        print(
            "DECISION =",
            result["decision"],
        )

    print()
    print("=" * 80)
    print("NEGATIVE QUESTIONS")
    print("-" * 80)

    for question in NEGATIVE_QUESTIONS:
        result = test_question(
            question,
            expected="REJECT",
        )

        all_results.append(result)

        print()
        print("QUESTION =", result["question"])
        print(
            "TOP_DOCUMENT =",
            result["document_id"],
        )

        if result["score"] is not None:
            print(
                "TOP_SCORE =",
                f'{result["score"]:.6f}',
            )
        else:
            print("TOP_SCORE = None")

        print(
            "DECISION =",
            result["decision"],
        )

    positive_scores = [
        r["score"]
        for r in all_results
        if (
            r["expected"] == "ACCEPT"
            and r["score"] is not None
        )
    ]

    negative_scores = [
        r["score"]
        for r in all_results
        if (
            r["expected"] == "REJECT"
            and r["score"] is not None
        )
    ]

    print()
    print("=" * 80)
    print("CALIBRATION SUMMARY")
    print("=" * 80)

    if positive_scores:
        min_positive = min(
            positive_scores
        )

        print(
            "MIN_POSITIVE_SCORE =",
            f"{min_positive:.6f}",
        )
    else:
        min_positive = None

    if negative_scores:
        max_negative = max(
            negative_scores
        )

        print(
            "MAX_NEGATIVE_SCORE =",
            f"{max_negative:.6f}",
        )
    else:
        max_negative = None

    if (
        min_positive is not None
        and max_negative is not None
    ):
        print()

        if max_negative < min_positive:
            suggested = (
                max_negative
                + min_positive
            ) / 2

            print(
                "SEPARATION = GOOD"
            )

            print(
                "SUGGESTED_THRESHOLD =",
                f"{suggested:.6f}",
            )

        else:
            print(
                "SEPARATION = OVERLAP"
            )

            print(
                "WARNING = Positive and negative "
                "scores overlap."
            )

            print(
                "A simple cosine threshold "
                "may not be sufficient."
            )

    print()
    print("=" * 80)
    print("DECISION CHECK")
    print("=" * 80)

    failures = []

    for result in all_results:
        if (
            result["decision"]
            != result["expected"]
        ):
            failures.append(
                result
            )

    if not failures:
        print(
            "ALL_TESTS_MATCH_EXPECTED=OK"
        )

    else:
        print(
            "ALL_TESTS_MATCH_EXPECTED=FAILED"
        )

        for failure in failures:
            print()
            print(
                "QUESTION =",
                failure["question"],
            )
            print(
                "EXPECTED =",
                failure["expected"],
            )
            print(
                "ACTUAL =",
                failure["decision"],
            )
            print(
                "SCORE =",
                failure["score"],
            )


if __name__ == "__main__":
    main()