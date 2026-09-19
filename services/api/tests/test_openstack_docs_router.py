"""Tests for routers/openstack_docs.py -- the actual pipeline
(fetch/embed/Qdrant) is faked here, same pattern
tests/test_knowledge_chat.py uses for routers/knowledge.py: this module's
own logic (auth gating, status codes, response shape) is what's under
test, not services/openstack_docs/ itself (see test_openstack_docs_ingest.py
/ test_openstack_docs_search.py for that).
"""
import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app import crud
from app.main import app
from app.routers import openstack_docs as openstack_docs_router
from app.services.knowledge.embeddings import EmbeddingError
from app.services.openstack_docs.ingest import DocsIngestResult
from app.services.openstack_docs.search import DocResult

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    """See test_security_router.py's identical fixture for why this
    guards against test_topology_router.py's module-level override."""
    had_override = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had_override:
        app.dependency_overrides[get_current_user] = saved


def _make_user_headers(role: str = "viewer") -> dict:
    import uuid
    db = SessionLocal()
    try:
        user = crud.create_user(
            db,
            username=f"docs-router-test-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("irrelevant-for-this-test"),
            role=role,
        )
        token = create_access_token(user)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


def test_ingest_requires_auth():
    resp = client.post("/api/v1/openstack-docs/ingest")
    assert resp.status_code == 401


def test_ingest_requires_admin_role():
    resp = client.post("/api/v1/openstack-docs/ingest", headers=_make_user_headers(role="viewer"))
    assert resp.status_code == 403


def test_ingest_runs_pipeline_and_returns_summary(monkeypatch):
    fake_result = DocsIngestResult(
        collection="cortex-openstack-official-docs", embedding_model="BAAI/bge-small-en-v1.5",
        sources_configured=8, sources_fetched=7, sources_failed=1, chunks_embedded=42,
        failed_urls=["https://docs.openstack.org/moved.html"], duration_seconds=3.2,
    )
    monkeypatch.setattr(openstack_docs_router, "run_ingest", lambda: fake_result)

    resp = client.post("/api/v1/openstack-docs/ingest", headers=_make_user_headers(role="admin"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["chunks_embedded"] == 42
    assert body["sources_failed"] == 1
    assert body["failed_urls"] == ["https://docs.openstack.org/moved.html"]


def test_ingest_returns_502_on_embedding_error(monkeypatch):
    def _raise():
        raise EmbeddingError("model unavailable")

    monkeypatch.setattr(openstack_docs_router, "run_ingest", _raise)
    resp = client.post("/api/v1/openstack-docs/ingest", headers=_make_user_headers(role="admin"))
    assert resp.status_code == 502


def test_status_reports_missing_collection(monkeypatch):
    monkeypatch.setattr(openstack_docs_router.docs_store, "collection_info", lambda: None)
    resp = client.get("/api/v1/openstack-docs/status", headers=_make_user_headers())
    assert resp.status_code == 200
    assert resp.json()["exists"] is False


def test_status_reports_existing_collection(monkeypatch):
    monkeypatch.setattr(
        openstack_docs_router.docs_store, "collection_info",
        lambda: {"collection": "cortex-openstack-official-docs", "points_count": 120,
                  "vectors_count": 120, "status": "green"},
    )
    resp = client.get("/api/v1/openstack-docs/status", headers=_make_user_headers())
    body = resp.json()
    assert body["exists"] is True
    assert body["points_count"] == 120


def test_search_requires_admin_role():
    resp = client.post(
        "/api/v1/openstack-docs/search", json={"query": "cinder volume stuck"},
        headers=_make_user_headers(role="viewer"),
    )
    assert resp.status_code == 403


def test_search_returns_results(monkeypatch):
    fake_hits = [
        DocResult(text="reset the volume state after confirming the backend is healthy",
                  source_url="https://docs.openstack.org/cinder/latest/admin/blockstorage-troubleshoot.html",
                  doc_title="Block Storage Troubleshooting", heading="Volume stuck in error", service="cinder",
                  score=0.71)
    ]
    monkeypatch.setattr(openstack_docs_router, "search_official_docs", lambda query, top_k, service: fake_hits)

    resp = client.post(
        "/api/v1/openstack-docs/search", json={"query": "cinder volume stuck in error"},
        headers=_make_user_headers(role="admin"),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["service"] == "cinder"
    assert body["results"][0]["score"] == 0.71
