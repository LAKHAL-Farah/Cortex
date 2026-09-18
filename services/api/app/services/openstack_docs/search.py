"""Semantic search over the official-docs collection -- the second
fallback tier the OpenStack Expert Agent (agents/nodes/openstack_expert.py)
reaches for on a standalone question that doesn't match anything in the
curated catalog (openstack_expert_catalog.py). See
docs/architecture/adr-0010-openstack-expert-v1-expansion.md.

Mirrors ../knowledge/chat.py's `retrieve()` shape (embed the query, search,
drop anything under a minimum score) but stays retrieval-only -- no LLM
generation layer on top, unlike knowledge/chat.py. This is deliberate:
adr-0008's decision #5 established that openstack_expert.py never lets an
LLM narrate a command (an incorrect but fluent command is actively
harmful), and the same logic extends to this fallback -- an official-docs
excerpt is shown as a citation the operator can read for themselves, not
rewritten into new prose that might subtly misstate what the docs actually
say. If the excerpt isn't self-explanatory, that's a real gap; synthesizing
around it would hide the gap instead of surfacing it.
"""
import os
from dataclasses import dataclass

from ..knowledge.embeddings import embed_query
from .store import search as qdrant_search

# Cosine-similarity floor below which a retrieved chunk is treated as noise
# rather than a real match -- same idea and same starting value as
# ../knowledge/chat.py's MIN_RETRIEVAL_SCORE, kept as its own env-tunable
# constant since this is a different collection/corpus and may need a
# different threshold once there's real query traffic to tune against.
MIN_DOCS_SCORE = float(os.environ.get("OPENSTACK_DOCS_MIN_SCORE", "0.25"))


@dataclass
class DocResult:
    text: str
    source_url: str
    doc_title: str
    heading: str | None
    service: str
    score: float


def search_official_docs(query: str, top_k: int = 3, service: str | None = None) -> list[DocResult]:
    """Raises EmbeddingError if the local embedding model can't be loaded
    -- callers (openstack_expert.py) treat that exactly like "this tier
    is unavailable right now" and fall through to the next fallback tier,
    the same way rag_agent already treats an embedding failure as
    something to degrade from rather than crash on."""
    query_vector = embed_query(query)
    points = qdrant_search(query_vector, top_k=top_k, service=service)
    results = [
        DocResult(
            text=p.payload["text"],
            source_url=p.payload["source_url"],
            doc_title=p.payload["doc_title"],
            heading=p.payload.get("heading"),
            service=p.payload["service"],
            score=p.score,
        )
        for p in points
    ]
    return [r for r in results if r.score >= MIN_DOCS_SCORE]
