import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
API_HEADERS = {"X-API-Key": "test-key"}


def _headers(client_id: str | None = None) -> dict:
    return {**API_HEADERS, "X-Client-Id": client_id or f"test-client-{uuid.uuid4().hex[:8]}"}


def test_create_get_conversation():
    headers = _headers()
    res = client.post("/api/v1/conversations", json={"title": "Cinder storage", "category": None}, headers=headers)
    assert res.status_code == 201
    conv_id = res.json()["id"]
    assert res.json()["messages"] == []

    res = client.get(f"/api/v1/conversations/{conv_id}", headers=headers)
    assert res.status_code == 200
    assert res.json()["title"] == "Cinder storage"


def test_list_conversations_scoped_by_client_id():
    a, b = _headers(), _headers()
    client.post("/api/v1/conversations", json={"title": "Thread A"}, headers=a)
    client.post("/api/v1/conversations", json={"title": "Thread B"}, headers=b)

    res_a = client.get("/api/v1/conversations", headers=a)
    assert res_a.status_code == 200
    titles_a = [c["title"] for c in res_a.json()]
    assert "Thread A" in titles_a
    assert "Thread B" not in titles_a


def test_replace_conversation_messages():
    headers = _headers()
    conv_id = client.post("/api/v1/conversations", json={"title": "New conversation"}, headers=headers).json()["id"]

    payload = {
        "title": "How is Cinder storage backed?",
        "category": None,
        "messages": [
            {"role": "user", "content": "How is Cinder storage backed?"},
            {
                "role": "assistant",
                "content": "It's backed by [cinder.md].",
                "sources": [
                    {"source_path": "docs/knowledge/cinder.md", "doc_title": "Cinder", "heading": None, "score": 0.9}
                ],
                "errored": False,
            },
        ],
    }
    res = client.put(f"/api/v1/conversations/{conv_id}", json=payload, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "How is Cinder storage backed?"
    assert len(body["messages"]) == 2
    assert body["messages"][1]["sources"][0]["doc_title"] == "Cinder"

    # A second PUT with a shorter message list should fully replace, not append.
    payload["messages"] = [{"role": "user", "content": "Just one message now"}]
    res = client.put(f"/api/v1/conversations/{conv_id}", json=payload, headers=headers)
    assert len(res.json()["messages"]) == 1


def test_delete_conversation():
    headers = _headers()
    conv_id = client.post("/api/v1/conversations", json={"title": "Temp"}, headers=headers).json()["id"]
    assert client.delete(f"/api/v1/conversations/{conv_id}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/conversations/{conv_id}", headers=headers).status_code == 404


def test_cannot_access_another_clients_conversation():
    owner, other = _headers(), _headers()
    conv_id = client.post("/api/v1/conversations", json={"title": "Private"}, headers=owner).json()["id"]
    assert client.get(f"/api/v1/conversations/{conv_id}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/conversations/{conv_id}", headers=other).status_code == 404


def test_requires_api_key_and_client_id():
    # No X-API-Key header at all -> FastAPI's own 422 (missing required
    # header), same convention as test_nodes.py's test_write_endpoints_require_api_key.
    # A *wrong* key is what actually reaches require_api_key's 401.
    assert client.get("/api/v1/conversations", headers={"X-Client-Id": "abcdefgh"}).status_code in (401, 422)
    assert (
        client.get("/api/v1/conversations", headers={**API_HEADERS, "X-API-Key": "wrong-key"}).status_code == 401
    )
    # X-API-Key present but no X-Client-Id -> 422 (missing required header)
    assert client.get("/api/v1/conversations", headers=API_HEADERS).status_code == 422
