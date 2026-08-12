from __future__ import annotations

from app.services.knowledge.chat_service import (
    KnowledgeChatService,
    NO_INFORMATION_ANSWER,
)


TESTS = [
    {
        "name": "Prometheus",
        "question": "Comment diagnostiquer un nœud qui ne remonte plus ses métriques dans Prometheus ?",        "expected_document": "prometheus-node-down",
        "should_be_grounded": True,
    },
    {
        "name": "Loki",
        "question": "Que faire si les logs d'un nœud ne remontent plus dans Loki ?",
        "expected_document": "loki-logs-missing",
        "should_be_grounded": True,
    },
    {
        "name": "OpenStack",
        "question": "Comment diagnostiquer une instance OpenStack en état ERROR ?",
        "expected_document": "openstack-instance-error",
        "should_be_grounded": True,
    },
    {
        "name": "Cinder RabbitMQ",
        "question": "Comment récupérer cinder-backup après un problème RabbitMQ ?",
        "expected_document": "cinder-recovery-rabbitmq",
        "should_be_grounded": True,
    },
    {
        "name": "WiFi hors documentation",
        "question": "Quel est le mot de passe Wi-Fi du bureau ?",
        "expected_document": None,
        "should_be_grounded": False,
    },
    {
        "name": "Météo hors documentation",
        "question": "Quel temps fera-t-il demain ?",
        "expected_document": None,
        "should_be_grounded": False,
    },
]


def validate_test(
    service: KnowledgeChatService,
    test: dict,
) -> tuple[bool, list[str]]:
    errors: list[str] = []

    result = service.answer(
        test["question"]
    )

    grounded = result.get("grounded")
    llm_called = result.get("llm_called")
    sources = result.get("sources", [])
    answer = result.get("answer", "")
    top_score = result.get("top_score")
    model = result.get("model")

    print("=" * 80)
    print("TEST =", test["name"])
    print("=" * 80)

    print("QUESTION:")
    print(test["question"])
    print()

    print("ANSWER:")
    print(answer)
    print()

    print("GROUNDED =", grounded)
    print("LLM_CALLED =", llm_called)
    print("TOP_SCORE =", top_score)
    print("MODEL =", model)

    print()
    print("SOURCES:")

    if not sources:
        print("[]")

    for source in sources:
        print(
            "-",
            source.get("document_id"),
            "|",
            source.get("citation"),
            "| score=",
            source.get("score"),
        )

    print()

    # --------------------------------------------------
    # TESTS GROUNDED
    # --------------------------------------------------

    if test["should_be_grounded"]:

        if grounded is not True:
            errors.append(
                "grounded devrait être True"
            )

        if llm_called is not True:
            errors.append(
                "llm_called devrait être True"
            )

        if not sources:
            errors.append(
                "aucune source retournée"
            )

        expected_document = test[
            "expected_document"
        ]

        returned_documents = {
            source.get("document_id")
            for source in sources
        }

        if (
            expected_document
            not in returned_documents
        ):
            errors.append(
                f"document attendu absent : "
                f"{expected_document}"
            )

        # Vérifie qu'au moins une citation
        # est présente dans la réponse.
        citation_found = any(
            source.get("citation", "")
            in answer
            for source in sources
        )

        if not citation_found:
            errors.append(
                "aucune citation retournée "
                "dans le texte de réponse"
            )

        if answer == NO_INFORMATION_ANSWER:
            errors.append(
                "fallback retourné pour une "
                "question documentée"
            )

    # --------------------------------------------------
    # TESTS HORS DOCUMENTATION
    # --------------------------------------------------

    else:

        if grounded is not False:
            errors.append(
                "grounded devrait être False"
            )

        if llm_called is not False:
            errors.append(
                "le LLM ne devrait pas être appelé"
            )

        if sources:
            errors.append(
                "sources devrait être vide"
            )

        if answer != NO_INFORMATION_ANSWER:
            errors.append(
                "le fallback exact n'a pas été "
                "retourné"
            )

        if model is not None:
            errors.append(
                "model devrait être None "
                "si le LLM n'est pas appelé"
            )

    if errors:
        print("RESULT = FAILED")

        for error in errors:
            print("ERROR =", error)

        return False, errors

    print("RESULT = OK")

    return True, []


def main() -> None:
    service = KnowledgeChatService()

    print()
    print("=" * 80)
    print("CORTEX STORY 2.7 - CHAT SERVICE VALIDATION")
    print("=" * 80)
    print()

    passed = 0
    failed = 0

    failed_tests: list[
        tuple[str, list[str]]
    ] = []

    for test in TESTS:

        success, errors = validate_test(
            service,
            test,
        )

        if success:
            passed += 1
        else:
            failed += 1
            failed_tests.append(
                (
                    test["name"],
                    errors,
                )
            )

        print()

    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)

    print(
        "TOTAL_TESTS =",
        len(TESTS),
    )

    print(
        "PASSED =",
        passed,
    )

    print(
        "FAILED =",
        failed,
    )

    if not failed_tests:
        print(
            "CHAT_SERVICE_VALIDATION=OK"
        )

    else:
        print(
            "CHAT_SERVICE_VALIDATION=FAILED"
        )

        print()

        for name, errors in failed_tests:
            print(
                "FAILED_TEST =",
                name,
            )

            for error in errors:
                print(
                    " -",
                    error,
                )

    print()
    print("=" * 80)


if __name__ == "__main__":
    main()