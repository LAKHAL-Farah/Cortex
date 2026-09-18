"""Tests for services/openstack_docs/ingest.py -- fetch, embedding, and
Qdrant upsert are all faked; only run_ingest's own orchestration
(collecting failures, not stopping the whole run over one bad URL,
building the result summary) is under test here.
"""
from app.services.openstack_docs import ingest
from app.services.openstack_docs.scraper import DocChunk, ScrapeError


def _chunk(source_url="https://docs.openstack.org/a.html", idx=0):
    return DocChunk(
        id=f"id-{idx}", text=f"text {idx}", source_url=source_url,
        doc_title="Title", heading=None, service="nova", chunk_index=idx,
    )


def test_run_ingest_fetches_every_configured_source(monkeypatch):
    sources = [
        {"url": "https://docs.openstack.org/a.html", "category": "compute", "service": "nova"},
        {"url": "https://docs.openstack.org/b.html", "category": "storage", "service": "cinder"},
    ]
    fetched_urls = []

    def fake_fetch_and_chunk(url, service):
        fetched_urls.append(url)
        return [_chunk(source_url=url)]

    monkeypatch.setattr(ingest, "fetch_and_chunk", fake_fetch_and_chunk)
    monkeypatch.setattr(ingest, "ensure_collection", lambda: None)
    monkeypatch.setattr(ingest, "embed_texts", lambda texts: [[0.1, 0.2]] * len(texts))
    monkeypatch.setattr(ingest, "upsert_chunks", lambda chunks, vectors: len(chunks))

    result = ingest.run_ingest(sources)

    assert fetched_urls == [s["url"] for s in sources]
    assert result.sources_configured == 2
    assert result.sources_fetched == 2
    assert result.sources_failed == 0
    assert result.chunks_embedded == 2
    assert result.failed_urls == []


def test_run_ingest_continues_past_a_failed_source(monkeypatch):
    sources = [
        {"url": "https://docs.openstack.org/good.html", "category": "compute", "service": "nova"},
        {"url": "https://docs.openstack.org/moved.html", "category": "compute", "service": "nova"},
    ]

    def fake_fetch_and_chunk(url, service):
        if "moved" in url:
            raise ScrapeError(f"404: {url}")
        return [_chunk(source_url=url)]

    monkeypatch.setattr(ingest, "fetch_and_chunk", fake_fetch_and_chunk)
    monkeypatch.setattr(ingest, "ensure_collection", lambda: None)
    monkeypatch.setattr(ingest, "embed_texts", lambda texts: [[0.1]] * len(texts))
    monkeypatch.setattr(ingest, "upsert_chunks", lambda chunks, vectors: len(chunks))

    result = ingest.run_ingest(sources)

    assert result.sources_configured == 2
    assert result.sources_fetched == 1
    assert result.sources_failed == 1
    assert result.failed_urls == ["https://docs.openstack.org/moved.html"]
    assert result.chunks_embedded == 1


def test_run_ingest_all_sources_failing_skips_embedding_and_upsert(monkeypatch):
    sources = [{"url": "https://docs.openstack.org/gone.html", "category": "compute", "service": "nova"}]

    monkeypatch.setattr(ingest, "fetch_and_chunk", lambda url, service: (_ for _ in ()).throw(ScrapeError("404")))

    def _fail(*a, **k):
        raise AssertionError("should not be called when there's nothing to embed")

    monkeypatch.setattr(ingest, "ensure_collection", _fail)
    monkeypatch.setattr(ingest, "embed_texts", _fail)
    monkeypatch.setattr(ingest, "upsert_chunks", _fail)

    result = ingest.run_ingest(sources)

    assert result.chunks_embedded == 0
    assert result.sources_failed == 1
    assert result.failed_urls == ["https://docs.openstack.org/gone.html"]


def test_run_ingest_defaults_to_official_doc_sources(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_and_chunk", lambda url, service: [])
    monkeypatch.setattr(ingest, "ensure_collection", lambda: None)
    monkeypatch.setattr(ingest, "embed_texts", lambda texts: [])
    monkeypatch.setattr(ingest, "upsert_chunks", lambda chunks, vectors: 0)

    result = ingest.run_ingest()

    assert result.sources_configured == len(ingest.OFFICIAL_DOC_SOURCES)


def test_run_ingest_result_includes_collection_and_model_name(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_and_chunk", lambda url, service: [_chunk()])
    monkeypatch.setattr(ingest, "ensure_collection", lambda: None)
    monkeypatch.setattr(ingest, "embed_texts", lambda texts: [[0.1]] * len(texts))
    monkeypatch.setattr(ingest, "upsert_chunks", lambda chunks, vectors: len(chunks))

    result = ingest.run_ingest([{"url": "https://docs.openstack.org/a.html", "category": "compute", "service": "nova"}])

    assert result.collection == ingest.QDRANT_OFFICIAL_DOCS_COLLECTION
    assert result.embedding_model == ingest.EMBEDDING_MODEL
    assert result.duration_seconds >= 0
