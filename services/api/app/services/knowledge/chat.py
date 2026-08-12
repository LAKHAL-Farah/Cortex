"""Grounded Q&A over docs/knowledge/, layered on top of the retrieval
pipeline in qdrant_store.py/embeddings.py (adr-0004).

Generation is done with an NVIDIA NIM-hosted chat model via LangChain's
ChatNVIDIA integration (adr-0005). Retrieval stays exactly as it is for
POST /api/v1/knowledge/search -- this module only adds an LLM turn on top
that is *required* to answer strictly from the retrieved chunks, so the
chat feature cites doc sources instead of answering from model memory.
"""
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_nvidia_ai_endpoints import ChatNVIDIA

from .embeddings import EmbeddingError, embed_query
from .qdrant_store import search as qdrant_search

logger = logging.getLogger(__name__)

NVIDIA_NIM_MODEL = os.environ.get("NVIDIA_NIM_MODEL", "nvidia/nemotron-3-super-120b-a12b")
# ChatNVIDIA defaults to NVIDIA's hosted NIM endpoint (integrate.api.nvidia.com)
# when base_url is omitted -- only set NVIDIA_NIM_BASE_URL if pointing at a
# self-hosted NIM container instead (see adr-0005).
NVIDIA_NIM_BASE_URL = os.environ.get("NVIDIA_NIM_BASE_URL") or None

# Cosine-similarity floor (qdrant_store.search's collection is COSINE
# distance) below which a retrieved chunk is considered irrelevant rather
# than weak-but-usable. Chosen empirically for bge-small-en-v1.5: unrelated
# query/chunk pairs on this corpus score well under this, on-topic pairs
# well above it. Tune via env without a code change if the corpus/model
# changes and the threshold needs to move.
MIN_RETRIEVAL_SCORE = float(os.environ.get("KNOWLEDGE_CHAT_MIN_SCORE", "0.2"))

_NO_CONTEXT_ANSWER = (
    "I don't have anything in the current docs/knowledge base that answers this. "
    "Try rephrasing, or check that the relevant doc has been ingested "
    "(POST /api/v1/knowledge/ingest)."
)

_SYSTEM_PROMPT_TEMPLATE = """You are Cortex Copilot, an assistant answering questions about RIF SAS's \
OpenStack infrastructure using ONLY the excerpts below, pulled from the team's own docs \
(docs/knowledge/).

Rules:
- Answer strictly from the excerpts. Do not add facts from general knowledge, training \
data, or assumptions about how OpenStack "usually" works if it isn't stated below.
- Every claim must be traceable to an excerpt. Cite the source after each claim using its \
label in square brackets, e.g. [nova.md]. If a sentence draws on two excerpts, cite both, \
e.g. [nova.md][admin-runbook.md].
- If the excerpts don't contain enough to answer, say so plainly instead of guessing -- do \
not fill gaps with speculation.
- Be concise and direct. Prefer short paragraphs or bullet points over long prose.

Excerpts:
{context}"""


class ChatConfigError(RuntimeError):
    """Raised when NVIDIA_API_KEY (or another required config value) is missing."""


@dataclass
class RetrievedChunk:
    text: str
    source_path: str
    doc_title: str
    heading: str | None
    category: str
    score: float


def _label(chunk: RetrievedChunk) -> str:
    # doc_title's H1 is often longer/less stable than the filename -- the
    # filename is what a person can actually go open under docs/knowledge/,
    # so it's the citation label the model is told to use.
    return chunk.source_path.rsplit("/", 1)[-1]


def retrieve(message: str, top_k: int, category: str | None) -> list[RetrievedChunk]:
    """Embeds the question and runs the same semantic search as
    POST /api/v1/knowledge/search, returning only chunks at or above
    MIN_RETRIEVAL_SCORE so weak/irrelevant matches never reach the prompt."""
    query_vector = embed_query(message)  # raises EmbeddingError, left to the caller
    points = qdrant_search(query_vector, top_k=top_k, category=category)
    chunks = [
        RetrievedChunk(
            text=p.payload["text"],
            source_path=p.payload["source_path"],
            doc_title=p.payload["doc_title"],
            heading=p.payload.get("heading"),
            category=p.payload["category"],
            score=p.score,
        )
        for p in points
    ]
    return [c for c in chunks if c.score >= MIN_RETRIEVAL_SCORE]


def _build_context_block(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for chunk in chunks:
        heading = f" -- {chunk.heading}" if chunk.heading else ""
        parts.append(f"[{_label(chunk)}]{heading}\n{chunk.text}")
    return "\n\n---\n\n".join(parts)


def _to_langchain_history(history) -> list[HumanMessage | AIMessage]:
    messages: list[HumanMessage | AIMessage] = []
    for turn in history:
        if turn.role == "user":
            messages.append(HumanMessage(content=turn.content))
        else:
            messages.append(AIMessage(content=turn.content))
    return messages


def require_configured() -> None:
    """Raises ChatConfigError if NVIDIA_API_KEY is missing. Called by the
    router *before* it starts the SSE stream (once it knows an LLM call is
    actually needed, i.e. retrieve() returned chunks) so a missing key comes
    back as a normal HTTP error instead of a mid-stream failure after the
    200 and headers are already on the wire."""
    if not os.environ.get("NVIDIA_API_KEY"):
        raise ChatConfigError(
            "NVIDIA_API_KEY is not set -- required to call the NVIDIA NIM chat endpoint"
        )


def _client() -> ChatNVIDIA:
    require_configured()
    kwargs = {
        "model": NVIDIA_NIM_MODEL,
        "api_key": os.environ["NVIDIA_API_KEY"],
        "temperature": 0.2,
    }
    if NVIDIA_NIM_BASE_URL:
        kwargs["base_url"] = NVIDIA_NIM_BASE_URL
    return ChatNVIDIA(**kwargs)


def stream_answer(
    message: str,
    history,
    chunks: list[RetrievedChunk],
) -> Iterator[str]:
    """Yields answer text incrementally. Caller (the router) has already
    retrieved `chunks` via retrieve() -- if it's empty, this streams the
    canned no-context answer and never calls the NIM endpoint at all, so an
    ungrounded question costs nothing and can't hallucinate."""
    if not chunks:
        yield _NO_CONTEXT_ANSWER
        return

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(context=_build_context_block(chunks))
    messages = [SystemMessage(content=system_prompt), *_to_langchain_history(history), HumanMessage(content=message)]

    llm = _client()
    try:
        for chunk in llm.stream(messages):
            text = chunk.content
            if text:
                yield text
    except Exception as exc:
        logger.exception("NVIDIA NIM chat generation failed")
        raise ChatConfigError(f"chat generation failed: {exc}") from exc
