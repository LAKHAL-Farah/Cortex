"""Tests for services/openstack_docs/search.py -- everything that would
otherwise need a real embedding model or a real Qdrant Cloud cluster is
faked, same pattern tests/test_knowledge_chat.py uses for
services/knowledge/chat.py's retrieve().
"""
from types import SimpleNamespace

from app.services.openstack_docs import search


def _point(score: float, text: str = "chunk text", source_url: str = "https://docs.openstack.org/x.html",
           heading: str | None = "Troubleshooting", service: str = "nova", doc_title: str = "Nova Admin Guide"):
    return SimpleNamespace(
        score=score,
        payload={
            "text": text,
            "source_url": source_url,
            "doc_title": doc_title,
            "heading": heading,
            "service": service,
        },
    )


def test_search_official_docs_filters_below_min_score(monkeypatch):
    monkeypatch.setattr(search, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(
        search, "qdrant_search",
        lambda query_vector, top_k, service: [
            _point(0.9, text="strong match"),
            _point(0.01, text="noise, should be dropped"),
        ],
    )
    results = search.search_official_docs("how do I check a stuck cinder volume")
    assert len(results) == 1
    assert results[0].text == "strong match"


def test_search_official_docs_returns_empty_when_nothing_clears_the_bar(monkeypatch):
    monkeypatch.setattr(search, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(search, "qdrant_search", lambda query_vector, top_k, service: [_point(0.001)])
    assert search.search_official_docs("irrelevant question") == []


def test_search_official_docs_preserves_score_order(monkeypatch):
    monkeypatch.setattr(search, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(
        search, "qdrant_search",
        lambda query_vector, top_k, service: [_point(0.9, text="first"), _point(0.5, text="second")],
    )
    results = search.search_official_docs("q")
    assert [r.text for r in results] == ["first", "second"]


def test_search_official_docs_passes_through_service_filter(monkeypatch):
    captured = {}
    monkeypatch.setattr(search, "embed_query", lambda text: [0.0])

    def fake_qdrant_search(query_vector, top_k, service):
        captured["service"] = service
        captured["top_k"] = top_k
        return []

    monkeypatch.setattr(search, "qdrant_search", fake_qdrant_search)
    search.search_official_docs("q", top_k=7, service="cinder")
    assert captured == {"service": "cinder", "top_k": 7}


def test_search_official_docs_propagates_embedding_errors(monkeypatch):
    from app.services.knowledge.embeddings import EmbeddingError

    def _raise(text):
        raise EmbeddingError("model unavailable")

    monkeypatch.setattr(search, "embed_query", _raise)
    try:
        search.search_official_docs("q")
        assert False, "expected EmbeddingError to propagate"
    except EmbeddingError:
        pass
