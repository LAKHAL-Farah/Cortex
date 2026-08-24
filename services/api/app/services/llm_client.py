"""Single place that knows how to build the LangChain chat model Cortex's
LLM-touching code shares -- the agentic layer's router/agents (app/agents/)
and the knowledge chat endpoint (services/knowledge/chat.py) all go through
this instead of each constructing their own ChatNVIDIA and re-reading the
same env vars, so there's exactly one model/one config to change.

Same NVIDIA NIM setup this always used (adr-0005): NVIDIA_API_KEY is
required, NVIDIA_NIM_MODEL/NVIDIA_NIM_BASE_URL are optional overrides.
"""
import os

from langchain_nvidia_ai_endpoints import ChatNVIDIA

NVIDIA_NIM_MODEL = os.environ.get("NVIDIA_NIM_MODEL", "nvidia/nemotron-3-super-120b-a12b")
# ChatNVIDIA defaults to NVIDIA's hosted NIM endpoint (integrate.api.nvidia.com)
# when base_url is omitted -- only set NVIDIA_NIM_BASE_URL if pointing at a
# self-hosted NIM container instead.
NVIDIA_NIM_BASE_URL = os.environ.get("NVIDIA_NIM_BASE_URL") or None


class LLMConfigError(RuntimeError):
    """Raised when NVIDIA_API_KEY is missing. Every LLM-touching call site
    in the agentic layer catches this specifically and degrades gracefully
    (a cheaper/dumber fallback, never a crash) -- an agent's job is to
    answer the question, not to insist an LLM is reachable."""


def require_configured() -> None:
    if not os.environ.get("NVIDIA_API_KEY"):
        raise LLMConfigError(
            "NVIDIA_API_KEY is not set -- required to call the NVIDIA NIM chat endpoint"
        )


def get_chat_model(temperature: float = 0.2, max_tokens: int | None = None) -> ChatNVIDIA:
    """Returns a fresh ChatNVIDIA instance. Callers doing structured output
    (`.with_structured_output(...)`) should pass temperature=0 -- a
    classification/extraction call has one right answer, not something to
    sample creatively from."""
    require_configured()
    kwargs = {
        "model": NVIDIA_NIM_MODEL,
        "api_key": os.environ["NVIDIA_API_KEY"],
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if NVIDIA_NIM_BASE_URL:
        kwargs["base_url"] = NVIDIA_NIM_BASE_URL
    return ChatNVIDIA(**kwargs)
