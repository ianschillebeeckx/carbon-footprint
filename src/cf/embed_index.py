"""Vector retrieval over the NAICS index using model2vec static embeddings
(small, fast, fully local). Builds data/naics_vectors.npz once; queries
return the top-k candidate NAICS entries for a merchant string."""

import json
from pathlib import Path

import numpy as np

INDEX_FILE = Path("data/naics_index.json")
VECTORS_FILE = Path("data/naics_vectors.npz")
MODEL_NAME = "minishlab/potion-base-8M"

_model = None
_entries = None
_matrix = None


def _load_model():
    global _model
    if _model is None:
        from model2vec import StaticModel
        _model = StaticModel.from_pretrained(MODEL_NAME)
    return _model


def build():
    index = json.loads(INDEX_FILE.read_text())
    entries = index["entries"]
    model = _load_model()
    vecs = model.encode([e["search_text"] for e in entries], show_progress_bar=False)
    vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
    np.savez_compressed(VECTORS_FILE, vectors=vecs.astype(np.float32))
    print(f"Built {VECTORS_FILE}: {vecs.shape[0]} vectors, dim {vecs.shape[1]}")


def _ensure_loaded():
    global _entries, _matrix
    if _entries is None:
        _entries = json.loads(INDEX_FILE.read_text())["entries"]
        if not VECTORS_FILE.exists():
            build()
        _matrix = np.load(VECTORS_FILE)["vectors"]


def top_k(query: str, k: int = 10) -> list[dict]:
    """Top-k NAICS entries for a query, each with a `score` added."""
    _ensure_loaded()
    q = _load_model().encode([query], show_progress_bar=False)[0]
    q = q / (np.linalg.norm(q) + 1e-9)
    scores = _matrix @ q
    order = np.argsort(-scores)[:k]
    return [{**_entries[i], "score": round(float(scores[i]), 4)} for i in order]


if __name__ == "__main__":
    build()
    for probe in ["Yoga Flow SF · Fitness", "Weatherflow · Sailing subscription", "The Home Depot"]:
        print(f"\n== {probe}")
        for e in top_k(probe, 5):
            print(f"  {e['score']:.3f}  {e['code']}  {e['title'][:60]}")
