"""Face comparison via NumPy cosine similarity.

Dimension-agnostic: works for 512-dim (InsightFace/ArcFace) or 128-dim
(face_recognition) embeddings.
"""
from __future__ import annotations

import numpy as np

# ArcFace embeddings are L2-normalized, so cosine similarity == dot product.
# 0.6 is a conservative same-identity threshold (ArcFace typical 0.45-0.6).
DEFAULT_THRESHOLD = 0.6


def cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
    """Cosine similarity in ``[-1, 1]`` between two embedding vectors."""
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def match(a: "np.ndarray", b: "np.ndarray", threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Compare two embeddings and return a structured result.

    Example::

        {"score": 0.712, "threshold": 0.6, "is_match": True}
    """
    score = cosine_similarity(a, b)
    return {"score": score, "threshold": threshold, "is_match": bool(score >= threshold)}
