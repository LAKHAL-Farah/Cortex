"""Tests for services/knowledge/chat.py and POST /api/v1/knowledge/chat (adr-0005).

Everything that would otherwise need a real NVIDIA NIM call or a real Qdrant
Cloud cluster is faked -- CI has neither NVIDIA_API_KEY nor QDRANT_URL set
(see .github/workflows/ci.yml), matching how test_topology_router.py fakes
Neo4j rather than requiring a live instance.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import knowledge as knowledge_router
from app.services.knowledge import chat

client = TestClient(app)
HEADERS = {"X-API-Key": "test-key"}


def _point(score: float, text: str = "some chunk text", source_path: str = "nova.md", heading: str | None = "Overview"):
    return SimpleNamespace(
        score=score,
        payload={
            "text": text,
            "source_path": source_path,
            "doc_title": "Nova",
            "heading": heading,
            "category": "service-detail",
        },
    )


# --------------------------------------------------------------- retrieve() --

def test_retrieve_filters_below_min_score(monkeypatch):
    monkeypatch.setattr(chat, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(
        chat,
        "qdrant_search",
        lambda query_vector, top_k, category: [
            _point(0.9, text="strong match"),
            _point(0.05, text="noise, should be dropped"),
        ],
    )
    chunks = chat.retrieve("how does nova work", top_k=5, category=None)
    assert len(chunks) == 1
    assert chunks[0].text == "strong match"


def test_retrieve_returns_empty_when_nothing_clears_the_bar(monkeypatch):
    monkeypatch.setattr(chat, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(chat, "qdrant_search", lambda query_vector, top_k, category: [_point(0.01)])
    assert chat.retrieve("irrelevant question", top_k=5, category=None) == []


# ----------------------------------------------------------- stream_answer() --

def test_stream_answer_with_no_chunks_never_calls_nim(monkeypatch):
    def _fail_client():
        raise AssertionError("NIM client should not be constructed when there are no chunks")

    monkeypatch.setattr(chat, "_client", _fail_client)
    tokens = list(chat.stream_answer("anything", [], chunks=[]))
    assert tokens == [chat._NO_CONTEXT_ANSWER]


class _FakeChunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, captured_messages):
        self._captured = captured_messages

    def stream(self, messages):
        self._captured.append(messages)
        for piece in ["Nova ", "handles ", "compute [nova.md]."]:
            yield _FakeChunk(piece)


def test_stream_answer_grounds_prompt_in_retrieved_chunks(monkeypatch):
    captured = []
    monkeypatch.setattr(chat, "_client", lambda: _FakeLLM(captured))

    retrieved = chat.retrieve  # keep reference; not used, just documenting shape
    fake_chunks = [
        chat.RetrievedChunk(
            text="Nova is the compute service.",
            source_path="service-detail/nova.md",
            doc_title="Nova",
            heading="Overview",
            category="service-detail",
            score=0.8,
        )
    ]

    tokens = list(chat.stream_answer("what does nova do", [], fake_chunks))
    assert "".join(tokens) == "Nova handles compute [nova.md]."

    # The system prompt must actually contain the retrieved text and use the
    # filename (not the longer doc_title) as the citation label.
    messages = captured[0]
    system_content = messages[0].content
    assert "Nova is the compute service." in system_content
    assert "[nova.md]" in system_content


def test_stream_answer_wraps_nim_failures(monkeypatch):
    class _BrokenLLM:
        def stream(self, messages):
            raise RuntimeError("upstream 503")

    monkeypatch.setattr(chat, "_client", lambda: _BrokenLLM())
    fake_chunks = [
        chat.RetrievedChunk(
            text="x", source_path="a.md", doc_title="A", heading=None, category="general", score=0.9
        )
    ]
    with pytest.raises(chat.ChatConfigError):
        list(chat.stream_answer("q", [], fake_chunks))


# --------------------------------------------------------------- router --

def test_chat_endpoint_requires_api_key():
    resp = client.post("/api/v1/knowledge/chat", json={"message": "hi"})
    assert resp.status_code == 401


def test_chat_endpoint_streams_sse_events(monkeypatch):
    fake_chunks = [
        chat.RetrievedChunk(
            text="Nova is the compute service.",
            source_path="service-detail/nova.md",
            doc_title="Nova",
            heading="Overview",
            category="service-detail",
            score=0.8,
        )
    ]
    monkeypatch.setattr(knowledge_router.knowledge_chat, "retrieve", lambda message, top_k, category: fake_chunks)
    monkeypatch.setattr(knowledge_router.knowledge_chat, "require_configured", lambda: None)
    monkeypatch.setattr(
        knowledge_router.knowledge_chat,
        "stream_answer",
        lambda message, history, chunks: iter(["Nova ", "handles ", "compute."]),
    )

    resp = client.post("/api/v1/knowledge/chat", json={"message": "what does nova do"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.text
    assert "event: sources" in body
    assert '"source_path": "service-detail/nova.md"' in body
    assert "event: token" in body
    assert '"text": "Nova "' in body
    assert "event: done" in body


def test_chat_endpoint_no_context_skips_nim_and_still_streams(monkeypatch):
    monkeypatch.setattr(knowledge_router.knowledge_chat, "retrieve", lambda message, top_k, category: [])

    def _fail_require_configured():
        raise AssertionError("require_configured() should not be called when there are no chunks")

    monkeypatch.setattr(knowledge_router.knowledge_chat, "require_configured", _fail_require_configured)

    resp = client.post("/api/v1/knowledge/chat", json={"message": "totally unrelated question"}, headers=HEADERS)
    assert resp.status_code == 200
    assert '"sources": []' in resp.text
    assert chat._NO_CONTEXT_ANSWER in resp.text


def test_chat_endpoint_missing_nvidia_key_fails_before_streaming(monkeypatch):
    fake_chunks = [
        chat.RetrievedChunk(text="x", source_path="a.md", doc_title="A", heading=None, category="general", score=0.9)
    ]
    monkeypatch.setattr(knowledge_router.knowledge_chat, "retrieve", lambda message, top_k, category: fake_chunks)

    def _raise():
        raise chat.ChatConfigError("NVIDIA_API_KEY is not set")

    monkeypatch.setattr(knowledge_router.knowledge_chat, "require_configured", _raise)

    resp = client.post("/api/v1/knowledge/chat", json={"message": "what does nova do"}, headers=HEADERS)
    assert resp.status_code == 502
