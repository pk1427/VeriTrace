"""Phase 1 matcher tests.

These validate the cosine-similarity gate logic with **synthetic**
embeddings so they run with zero dependencies (no face model, no images):

* same-person pair  -> cosine similarity > 0.6  -> match == True
* different-person pair -> cosine similarity < 0.4 -> match == False

The functions double as pytest cases (`test_*`) and can be run standalone::

    python test/test_matcher.py
    # or, if pytest is installed:
    pytest -q test/test_matcher.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

# Make `src` importable when running the file directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.face.matcher import cosine_similarity, match, DEFAULT_THRESHOLD  # noqa: E402


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    assert n > 0
    return v / n


def test_identical_vectors_are_one() -> None:
    a = _unit(np.array([3.0, 4.0, 0.0, 0.0]))
    assert math.isclose(cosine_similarity(a, a), 1.0, abs_tol=1e-6)


def test_identical_vectors_match() -> None:
    a = _unit(np.array([1.0, 2.0, 3.0, 4.0]))
    result = match(a, a)
    assert result["is_match"] is True
    assert result["score"] >= DEFAULT_THRESHOLD


def test_same_person_similarity_above_threshold() -> None:
    """Two embeddings with a small angular offset should be similar (>0.6)."""
    base = _unit(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    # Tiny perturbation keeps similarity high (well above the 0.6 gate).
    twin = _unit(np.array([1.1, 2.1, 3.0, 4.0, 4.9]))
    score = cosine_similarity(base, twin)
    assert score > 0.6, f"expected >0.6 for same person, got {score:.4f}"
    assert match(base, twin, threshold=0.6)["is_match"] is True


def test_different_people_similarity_below_threshold() -> None:
    """Two nearly-orthogonal embeddings should be dissimilar (<0.4)."""
    a = _unit(np.array([1.0, 0.0, 0.0, 0.0, 0.0]))
    b = _unit(np.array([0.0, 1.0, 0.0, 0.0, 0.0]))
    score = cosine_similarity(a, b)
    assert score < 0.4, f"expected <0.4 for different people, got {score:.4f}"
    assert match(a, b, threshold=0.6)["is_match"] is False


def test_zero_vector_is_zero_similarity() -> None:
    a = np.zeros(5, dtype=np.float32)
    b = _unit(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert cosine_similarity(a, b) == 0.0


def test_strict_threshold_can_reject_a_match() -> None:
    # Mid-range pair: similar enough to pass a lenient gate, not similar enough
    # to pass a strict one.
    base = _unit(np.array([1.0, 1.0, 1.0, 1.0]))
    twin = _unit(np.array([1.0, 1.0, 1.0, 0.0]))  # cos ~0.866
    assert 0.6 < cosine_similarity(base, twin) < 0.99
    assert match(base, twin, threshold=0.6)["is_match"] is True
    assert match(base, twin, threshold=0.99)["is_match"] is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} matcher tests passed")
    sys.exit(1 if failures else 0)