"""Phase 1 end-to-end test (requires real face images + the InsightFace model).

This script exercises the *full* Phase 1 pipeline — detection -> embedding ->
cosine similarity — on real photos:

* two photos of the SAME person  -> similarity > 0.6
* two photos of DIFFERENT people  -> similarity < 0.4

It is **skipped** (not failed) when the sample images or the face backend are
missing, printing clear instructions instead. Provide the images and the test
will validate the real thresholds.

Required sample images (copy into ``test/samples/``)::

    same_a.jpg   same_b.jpg    # same person, two photos
    diff_a.jpg   diff_b.jpg    # two different people

Run::

    python test/test_phase1_e2e.py
    # or:  pytest -q test/test_phase1_e2e.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.face.matcher import cosine_similarity  # noqa: E402  (numpy-only, safe at import)

SAMPLES = ROOT / "test" / "samples"
SAME_A = SAMPLES / "same_a.jpg"
SAME_B = SAMPLES / "same_b.jpg"
DIFF_A = SAMPLES / "diff_a.jpg"
DIFF_B = SAMPLES / "diff_b.jpg"

SAME_GATE = 0.6      # same identity should exceed this
DIFF_GATE = 0.4      # different identity should sit below this


def _embed(path: Path, encode_largest_face) -> np.ndarray:
    return encode_largest_face(str(path), normalize=True)


def _missing() -> list[str]:
    return [str(p) for p in (SAME_A, SAME_B, DIFF_A, DIFF_B) if not p.is_file()]


def main() -> int:
    missing = _missing()
    if missing:
        print("skip: end-to-end test needs sample face images.")
        print("      Place identical-resolution-ish photos of faces at:")
        for p in (SAME_A, SAME_B, DIFF_A, DIFF_B):
            print(f"        {p.relative_to(ROOT)}")
        print("      (same_a/same_b = one person, diff_a/diff_b = two people)")
        print("      Then re-run: python test/test_phase1_e2e.py")
        return 0  # skip, not a failure

    # Backend import is deferred so the "no images" skip path works without
    # the optional InsightFace/cv2 stack installed.
    try:
        from src.face import backend_name, encode_largest_face
    except ImportError as exc:
        print(f"skip: face backend unavailable ({exc}).")
        print("      Install deps: pip install -r requirements.txt")
        return 0

    if backend_name() is None:
        print("skip: no face backend installed (insightface/fallback unavailable).")
        print("      Install deps: pip install -r requirements.txt")
        return 0

    same_score = cosine_similarity(_embed(SAME_A, encode_largest_face), _embed(SAME_B, encode_largest_face))
    diff_score = cosine_similarity(_embed(DIFF_A, encode_largest_face), _embed(DIFF_B, encode_largest_face))

    print(f"same-person similarity : {same_score:.4f}  (expect > {SAME_GATE})")
    print(f"different-person sim.  : {diff_score:.4f}  (expect < {DIFF_GATE})")
    print(f"backend                : {backend_name()}")

    ok = True
    if not (same_score > SAME_GATE):
        print(f"FAIL: same-person similarity {same_score:.4f} not > {SAME_GATE}")
        ok = False
    if not (diff_score < DIFF_GATE):
        print(f"FAIL: different-person similarity {diff_score:.4f} not < {DIFF_GATE}")
        ok = False
    if ok:
        print("PASS: e2e thresholds satisfied")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())