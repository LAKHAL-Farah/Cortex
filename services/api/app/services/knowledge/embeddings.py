"""Local embedding client.

Unlike the original design (an HTTP call to an OpenAI-compatible
/embeddings endpoint), this loads a sentence-transformers model directly
in-process. No embeddings API key and no outbound network call at request
time -- the model is baked into the image at build time (see Dockerfile)
and HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE are set so a mismatched
EMBEDDING_MODEL fails loudly instead of silently reaching out to
huggingface.co.
"""
import os
from functools import lru_cache

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
# 384 matches bge-small-en-v1.5's native output size -- override via env if
# EMBEDDING_MODEL is swapped for a model with a different vector size, since
# the Qdrant collection is created with this dimensionality up front (see
# qdrant_store.ensure_collection).
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "384"))

# bge-small-en-v1.5's own model card recommends prefixing *queries* (not the
# indexed passages) with this instruction for retrieval -- it measurably
# improves query/passage matching for this model family. Only applied in
# embed_query, never in embed_texts, so the vectors stored in Qdrant stay
# unprefixed.
_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class EmbeddingError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    try:
        return SentenceTransformer(EMBEDDING_MODEL)
    except Exception as exc:
        raise EmbeddingError(
            f"failed to load embedding model {EMBEDDING_MODEL!r} -- if this is "
            "HF_HUB_OFFLINE=1 refusing a network call, check that the Dockerfile's "
            "bake-in RUN line matches EMBEDDING_MODEL and rebuild the image"
        ) from exc


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Returns one embedding vector per input text, preserving order."""
    if not texts:
        return []
    vectors = _model().encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vectors.tolist()


def embed_query(text: str) -> list[float]:
    vectors = _model().encode(
        [_QUERY_INSTRUCTION + text],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vectors[0].tolist()
