"""Smoke test for the local embeddings module (adr-0004).

Loads the real BAAI/bge-small-en-v1.5 model via sentence-transformers, so
this is slower than the rest of the unit tests and needs the model
available (either already cached from the Dockerfile's build-time
download, or reachable on huggingface.co if run outside that image).
"""
from app.services.knowledge.embeddings import EMBEDDING_DIMENSIONS, embed_query, embed_texts


def test_embed_texts_returns_correct_dimensions():
    vectors = embed_texts(["hello world", "cinder volume space"])
    assert len(vectors) == 2
    assert all(len(v) == EMBEDDING_DIMENSIONS for v in vectors)


def test_embed_texts_empty_input():
    assert embed_texts([]) == []


def test_embed_query_returns_correct_dimensions():
    vector = embed_query("how is Cinder storage backed?")
    assert len(vector) == EMBEDDING_DIMENSIONS
