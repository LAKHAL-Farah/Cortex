import uuid

from fastapi.testclient import TestClient

from app.auth import create_access_token, hash_password
from app.db import SessionLocal
from app.main import app
from app import crud

client = TestClient(app)


def _make_user_headers() -> dict:
    """Creates a real account directly against the DB (there's no
    self-service signup endpoint -- accounts are always admin-created, see
    routers/auth.py's module docstring) and returns Authorization headers
    for it, exactly the shape every real request carries (see
    services/web/lib/serverAuth.ts): a bearer JWT, not the old
    X-API-Key/X-Client-Id pair conversations used before real accounts
    existed (see models.Conversation's docstring for that migration).
    """
    db = SessionLocal()
    try:
        user = crud.create_user(
            db,
            username=f"test-user-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("irrelevant-for-this-test"),
            role="viewer",
        )
        token = create_access_token(user)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


def test_create_get_conversation():
    headers = _make_user_headers()
    res = client.post("/api/v1/conversations", json={"title": "Cinder storage", "category": None}, headers=headers)
    assert res.status_code == 201
    conv_id = res.json()["id"]
    assert res.json()["messages"] == []

    res = client.get(f"/api/v1/conversations/{conv_id}", headers=headers)
    assert res.status_code == 200
    assert res.json()["title"] == "Cinder storage"


def test_list_conversations_scoped_by_account():
    a, b = _make_user_headers(), _make_user_headers()
    client.post("/api/v1/conversations", json={"title": "Thread A"}, headers=a)
    client.post("/api/v1/conversations", json={"title": "Thread B"}, headers=b)

    res_a = client.get("/api/v1/conversations", headers=a)
    assert res_a.status_code == 200
    titles_a = [c["title"] for c in res_a.json()]
    assert "Thread A" in titles_a
    assert "Thread B" not in titles_a


def test_replace_conversation_messages():
    headers = _make_user_headers()
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
    headers = _make_user_headers()
    conv_id = client.post("/api/v1/conversations", json={"title": "Temp"}, headers=headers).json()["id"]
    assert client.delete(f"/api/v1/conversations/{conv_id}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/conversations/{conv_id}", headers=headers).status_code == 404


def test_cannot_access_another_accounts_conversation():
    owner, other = _make_user_headers(), _make_user_headers()
    conv_id = client.post("/api/v1/conversations", json={"title": "Private"}, headers=owner).json()["id"]
    assert client.get(f"/api/v1/conversations/{conv_id}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/conversations/{conv_id}", headers=other).status_code == 404


def test_requires_authentication():
    # No Authorization header at all -> get_current_user's own 401.
    assert client.get("/api/v1/conversations").status_code == 401
    # Malformed/garbage bearer token -> also 401, not a 500.
    assert client.get("/api/v1/conversations", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401
