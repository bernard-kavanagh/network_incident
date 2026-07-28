"""Local embedding model — all-MiniLM-L6-v2 (384-dim).

Runs locally with zero external API calls, so the whole stack works on an
air-gapped / sovereign GDC bare-metal cluster. Ported verbatim in spirit from
the EV-charger and fraud repos (same model, same dimension as VECTOR(384)).
"""
import os

_model = None


def get_model():
    """Lazy-load the SentenceTransformer to avoid import-time cost/crashes."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        print(f"🧠 Loading embedding model ({name})...")
        _model = SentenceTransformer(name)
    return _model


def embed(text: str) -> list:
    """Return a 384-float embedding for a single string."""
    return get_model().encode(text).tolist()


def embed_str(text: str) -> str:
    """Return the embedding as a TiDB VECTOR literal string, e.g. '[0.1, ...]'."""
    return str(embed(text))


def embed_batch(texts: list) -> list:
    """Return a list of embeddings for a list of strings."""
    return get_model().encode(texts).tolist()
