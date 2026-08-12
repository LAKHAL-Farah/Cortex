from __future__ import annotations

import logging
import os
from functools import lru_cache

from openai import OpenAI


logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def optional_env(
    name: str,
    default: str = "",
) -> str:
    return os.getenv(
        name,
        default,
    ).strip()


def require_env(
    name: str,
) -> str:
    value = optional_env(name)

    if not value:
        raise RuntimeError(
            f"Missing environment variable: {name}"
        )

    return value


class OpenAIAdapter:
    """
    Client générique pour les APIs compatibles OpenAI.

    Peut être utilisé avec :

    - OpenAI
    - NVIDIA NIM
    - une instance NIM self-hosted
    - toute API compatible /v1/chat/completions
    """

    def __init__(self) -> None:
        self.base_url = optional_env(
            "LLM_BASE_URL",
            DEFAULT_BASE_URL,
        )

        self.api_key = require_env(
            "LLM_API_KEY"
        )

        self.default_model = optional_env(
            "LLM_MODEL",
            DEFAULT_MODEL,
        )

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def chat(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        """
        Envoie une requête Chat Completions.

        Retourne uniquement le texte généré.
        """

        selected_model = (
            model
            or self.default_model
        )

        if not system.strip():
            raise ValueError(
                "Le system prompt ne peut pas être vide."
            )

        if not user.strip():
            raise ValueError(
                "Le user prompt ne peut pas être vide."
            )

        try:
            response = (
                self.client.chat.completions.create(
                    model=selected_model,
                    messages=[
                        {
                            "role": "system",
                            "content": system,
                        },
                        {
                            "role": "user",
                            "content": user,
                        },
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            )

            if not response.choices:
                raise RuntimeError(
                    "Le fournisseur LLM "
                    "n'a retourné aucun choix."
                )

            content = (
                response
                .choices[0]
                .message
                .content
            )

            if not content:
                raise RuntimeError(
                    "Le fournisseur LLM "
                    "a retourné une réponse vide."
                )

            return content.strip()

        except Exception:
            logger.exception(
                "LLM chat call failed "
                "using model=%s base_url=%s",
                selected_model,
                self.base_url,
            )

            raise


@lru_cache(maxsize=1)
def get_llm_adapter() -> OpenAIAdapter:
    """
    Réutilise le même client LLM
    pendant toute la durée du processus API.
    """

    return OpenAIAdapter()