"""Local embedding with sentence-transformers + brute-force search."""

import numpy as np
from sentence_transformers import SentenceTransformer

from . import config, db

_model: SentenceTransformer | None = None

# Embedding cache — reloaded only when new embeddings are added
_embed_cache: list[tuple[str, bytes]] | None = None
_embed_version: int = 0
_embed_cached_version: int = -1


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(config.EMBED_MODEL, device="cpu")
    return _model


def embed_text(text: str) -> np.ndarray:
    """Return a normalized float32 vector of shape (384,)."""
    model = get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.astype(np.float32)


def vector_to_blob(vec: np.ndarray) -> bytes:
    return vec.tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def embed_and_store(conn, message_id: str, text: str):
    """Embed text and save the vector to the database."""
    global _embed_version
    vec = embed_text(text)
    db.save_embedding(conn, message_id, vector_to_blob(vec))
    _embed_version += 1


def _get_cached_embeddings(conn) -> list[tuple[str, bytes]]:
    global _embed_cache, _embed_cached_version
    if _embed_cache is None or _embed_cached_version != _embed_version:
        _embed_cache = db.load_all_embeddings(conn)
        _embed_cached_version = _embed_version
    return _embed_cache


def search_similar(
    conn, query: str, top_k: int = config.TOP_K_MEMORIES,
    exclude_ids: set[str] | None = None,
) -> list[dict]:
    """Find the top-k most similar messages by cosine similarity.

    Returns list of dicts with id, role, content, created_at, score.
    """
    all_embeddings = _get_cached_embeddings(conn)
    if not all_embeddings:
        return []

    # Filter out excluded IDs
    if exclude_ids:
        all_embeddings = [
            (mid, blob) for mid, blob in all_embeddings if mid not in exclude_ids
        ]
    if not all_embeddings:
        return []

    ids = [mid for mid, _ in all_embeddings]
    matrix = np.stack([blob_to_vector(blob) for _, blob in all_embeddings])

    query_vec = embed_text(query)
    scores = matrix @ query_vec  # dot product (vectors are normalized)

    top_indices = np.argsort(scores)[-top_k:][::-1]
    top_ids = [ids[i] for i in top_indices]
    top_scores = {ids[i]: float(scores[i]) for i in top_indices}

    messages = db.get_messages_by_ids(conn, top_ids)
    for msg in messages:
        msg["score"] = top_scores.get(msg["id"], 0.0)

    # Sort by score descending
    messages.sort(key=lambda m: m["score"], reverse=True)
    return messages
