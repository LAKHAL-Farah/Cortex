from __future__ import annotations

import os
from typing import Any

from .retriever import RetrievedChunk, search_knowledge
from ..llm.openai_adapter import get_llm_adapter


NO_INFORMATION_ANSWER = (
    "Je ne trouve pas cette information dans la base documentaire Cortex."
)


SYSTEM_PROMPT = """
Tu es Cortex Copilot, un assistant technique spécialisé dans
l'infrastructure Cortex/OpenStack.

RÈGLES OBLIGATOIRES :

1. Réponds uniquement à partir des extraits de documentation fournis.
2. N'utilise pas tes connaissances générales pour compléter une information absente.
3. N'invente jamais une commande, une procédure, une cause ou un diagnostic.
4. Si les extraits ne permettent pas de répondre à la question, réponds exactement :
   "Je ne trouve pas cette information dans la base documentaire Cortex."
5. Pour chaque information technique importante, cite la source correspondante
   avec le format :
   [source_path:chunk_index]
6. Réponds en français.
7. Sois synthétique, clair et opérationnel.
""".strip()


class KnowledgeChatService:
    def __init__(self) -> None:
        self.llm = get_llm_adapter()

        self.model = os.getenv(
            "LLM_MODEL",
            self.llm.default_model,
        ).strip()

        self.max_chunks = int(
            os.getenv(
                "CHAT_MAX_CHUNKS",
                "5",
            )
        )

        # Seuil temporaire et configurable.
        # Il sera calibré avec nos tests positifs/négatifs.
        self.minimum_relevance_score = float(
            os.getenv(
                "CHAT_MIN_RELEVANCE_SCORE",
                "0.82",
            )
        )

    def _is_relevant(
        self,
        results: list[RetrievedChunk],
    ) -> bool:
        """
        Vérifie qu'au moins un résultat Qdrant est suffisamment pertinent.

        Aucun appel LLM n'est effectué si aucun résultat
        ne dépasse le seuil.
        """

        if not results:
            return False

        return results[0].score >= self.minimum_relevance_score

    def _filter_relevant_chunks(
        self,
        results: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """
        Garde uniquement les chunks réellement proches
        du meilleur résultat.

        Cela évite d'envoyer au LLM des documents
        techniquement au-dessus du seuil global,
        mais peu utiles pour la question.
        """

        if not results:
            return []

        best_score = results[0].score

        relative_margin = 0.04

        minimum_relative_score = max(
            self.minimum_relevance_score,
            best_score - relative_margin,
        )

        return [
            result
            for result in results
            if result.score >= minimum_relative_score
        ]

    def _build_user_prompt(
        self,
        question: str,
        chunks: list[RetrievedChunk],
    ) -> str:
        """
        Construit le contexte RAG envoyé au LLM.
        """

        parts: list[str] = [
            f"Question utilisateur :\n{question}",
            "",
            "Extraits autorisés :",
        ]

        for chunk in chunks:
            citation = (
                f"[{chunk.source_path}:{chunk.chunk_index}]"
            )

            parts.append(
                "\n".join(
                    [
                        "---",
                        f"Source : {citation}",
                        f"Titre : {chunk.title}",
                        f"Document ID : {chunk.document_id}",
                        f"Service : {chunk.service or 'non spécifié'}",
                        f"Score de recherche : {chunk.score:.4f}",
                        "",
                        chunk.text,
                    ]
                )
            )

        parts.extend(
            [
                "---",
                "",
                "Instructions de réponse :",
                "- Réponds uniquement avec les informations présentes dans les extraits.",
                "- Cite les sources avec le format [source_path:chunk_index].",
                "- N'ajoute aucune information issue de tes connaissances générales.",
                (
                    "- Si les extraits ne répondent pas réellement à la question, "
                    f'réponds exactement : "{NO_INFORMATION_ANSWER}"'
                ),
            ]
        )

        return "\n".join(parts)

    def _build_sources(
        self,
        chunks: list[RetrievedChunk],
    ) -> list[dict[str, Any]]:
        """
        Prépare les sources retournées par l'API
        pour le frontend Cortex.
        """

        return [
            {
                "point_id": chunk.point_id,
                "document_id": chunk.document_id,
                "title": chunk.title,
                "source_path": chunk.source_path,
                "chunk_index": chunk.chunk_index,
                "score": round(chunk.score, 6),
                "service": chunk.service,
                "citation": (
                    f"[{chunk.source_path}:{chunk.chunk_index}]"
                ),
                "snippet": chunk.text[:400],
            }
            for chunk in chunks
        ]

    def answer(
        self,
        question: str,
        *,
        service: str | None = None,
        environment: str | None = "production",
        document_type: str | None = "runbook",
        language: str | None = "fr",
        limit: int | None = None,
    ) -> dict[str, Any]:

        question = question.strip()

        if not question:
            raise ValueError(
                "La question ne peut pas être vide."
            )

        search_limit = limit or self.max_chunks

        results = search_knowledge(
            query=question,
            limit=search_limit,
            service=service,
            environment=environment,
            document_type=document_type,
            language=language,
        )

        # --------------------------------------------------
        # RELEVANCE GATE
        # --------------------------------------------------
        # Si Qdrant ne trouve rien d'assez pertinent,
        # on n'appelle PAS le LLM.
        # --------------------------------------------------

        if not self._is_relevant(results):
            return {
                "answer": NO_INFORMATION_ANSWER,
                "sources": [],
                "model": None,
                "grounded": False,
                "llm_called": False,
                "top_score": (
                    round(results[0].score, 6)
                    if results
                    else None
                ),
            }

        relevant_chunks = self._filter_relevant_chunks(
            results
        )

        if not relevant_chunks:
            return {
                "answer": NO_INFORMATION_ANSWER,
                "sources": [],
                "model": None,
                "grounded": False,
                "llm_called": False,
                "top_score": (
                    round(results[0].score, 6)
                    if results
                    else None
                ),
            }

        user_prompt = self._build_user_prompt(
            question,
            relevant_chunks,
        )

        answer_text = self.llm.chat(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            model=self.model,
            max_tokens=512,
            temperature=0.0,
        )

        # Sécurité supplémentaire :
        # si le LLM lui-même indique que l'information
        # est absente, aucune source n'est annoncée.
        if (
            NO_INFORMATION_ANSWER.lower()
            in answer_text.lower()
        ):
            return {
                "answer": NO_INFORMATION_ANSWER,
                "sources": [],
                "model": self.model,
                "grounded": False,
                "llm_called": True,
                "top_score": round(
                    results[0].score,
                    6,
                ),
            }

        return {
            "answer": answer_text,
            "sources": self._build_sources(
                relevant_chunks
            ),
            "model": self.model,
            "grounded": True,
            "llm_called": True,
            "top_score": round(
                results[0].score,
                6,
            ),
        }