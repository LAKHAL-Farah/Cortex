"""Grounded Q&A over docs/knowledge/, layered on top of the retrieval
pipeline in qdrant_store.py/embeddings.py (adr-0004).

Generation is done with an NVIDIA NIM-hosted chat model via LangChain's
ChatNVIDIA integration (adr-0005). Retrieval stays exactly as it is for
POST /api/v1/knowledge/search -- this module adds an LLM turn on top that
answers deployment-specific facts strictly from the retrieved chunks (cited
per claim, see _SYSTEM_PROMPT_TEMPLATE), but is explicitly allowed to draw
on the model's own OpenStack knowledge to explain *how*/*why* on top of
those facts, rather than being confined to one-line excerpt lookups.
"""
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..llm_client import LLMConfigError, get_chat_model
from .embeddings import EmbeddingError, embed_query
from .qdrant_store import search as qdrant_search

logger = logging.getLogger(__name__)

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

_SYSTEM_PROMPT_TEMPLATE = """You are Cortex Copilot, an assistant that explains RIF SAS's \
OpenStack infrastructure. The excerpts below are pulled from the team's own docs \
(docs/knowledge/) and are the source of truth for anything specific to *this* deployment.

How to use the excerpts vs. your own knowledge:
- Any claim about this specific deployment -- hostnames, IPs, ports, service names, \
topology, procedures, configuration -- must come from the excerpts. Never invent specifics \
that aren't stated below. Cite the excerpt right after each such claim using its label in \
square brackets, e.g. [nova.md]. If a sentence draws on two excerpts, cite both, e.g. \
[nova.md][admin-runbook.md].
- Beyond that, use your own knowledge of OpenStack and the underlying technology (Nova, \
Neutron, Cinder, Ceph, KVM, etc.) freely to explain *why* things are set up this way, how the \
mechanism actually works, what the tradeoffs are, and how the pieces fit together. This is \
general background, not a claim about this deployment -- don't cite an excerpt label for it, \
and don't imply it's confirmed by the docs unless it also appears in the excerpts.
- If the excerpts don't cover the deployment-specific part of the question at all, say so \
plainly instead of guessing -- but still give the general-knowledge explanation if it's \
relevant context.

Format every answer for someone who wants to actually understand the topic, not just get a \
one-liner:
- Use Markdown ## headings to break the answer into sections when it covers more than one \
idea (e.g. what it is, how it works, why it's configured that way). Pick headings that fit \
the actual topic rather than generic labels.
- Use short paragraphs and bullet points -- whichever reads more clearly for that part of the \
answer. Avoid a single wall of text.
- Elaborate. Explain the mechanism and the reasoning, not just the fact. A bare one- or \
two-sentence answer is only appropriate for a genuinely simple yes/no question.

Excerpts:
{context}"""



class ChatConfigError(LLMConfigError):
    """Raised when NVIDIA_API_KEY (or another required config value) is missing.
    Subclasses the shared LLMConfigError (services/llm_client.py) so this
    stays catchable both as the specific ChatConfigError callers here have
    always expected, and generically alongside every other LLM call site."""


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
    try:
        from ..llm_client import require_configured as _require_llm_configured

        _require_llm_configured()
    except LLMConfigError as exc:
        raise ChatConfigError(str(exc)) from exc


def _client():
    # The system prompt now asks for headed, multi-section, elaborated
    # answers (not one-liners) -- give that enough room to finish a
    # section instead of getting cut off mid-heading on a long answer.
    try:
        return get_chat_model(temperature=0.2, max_tokens=1536)
    except LLMConfigError as exc:
        raise ChatConfigError(str(exc)) from exc


def answer_sync(
    message: str,
    history=(),
    top_k: int = 5,
    category: str | None = None,
) -> tuple[str, list[RetrievedChunk]]:
    """Non-streaming counterpart to retrieve()+stream_answer(), for callers
    that want one complete answer back instead of an SSE token stream --
    specifically the agentic RAG node (app/agents/nodes/rag.py), which
    promotes this exact retrieval+generation logic into the LangGraph
    orchestrator rather than reimplementing it. Same grounding rules, same
    system prompt, same LLM client as POST /api/v1/knowledge/chat."""
    chunks = retrieve(message, top_k=top_k, category=category)
    if not chunks:
        return _NO_CONTEXT_ANSWER, chunks

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(context=_build_context_block(chunks))
    messages = [SystemMessage(content=system_prompt), *_to_langchain_history(history), HumanMessage(content=message)]

    llm = _client()
    try:
        response = llm.invoke(messages)
    except Exception as exc:
        logger.exception("NVIDIA NIM chat generation failed")
        raise ChatConfigError(f"chat generation failed: {exc}") from exc

    text = (response.content or "").strip()
    return text or _NO_CONTEXT_ANSWER, chunks


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
