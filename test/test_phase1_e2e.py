"""Phase 1 end-to-end test (requires real face images + the InsightFace model).

This script exercises the *full* Phase 1 pipeline — detection -> embedding ->
cosine similarity — on real photos:

* two photos of the SAME person  -> similarity > 0.6
* two photos of DIFFERENT people  -> similarity < 0.4

It is **skipped** (not failed) when the sample images or the face backend are
missing, printing clear instructions instead. Provide the images and the test
will validate the real thresholds.

Two ways to supply images:

1. Conventional filenames (any extension) in ``test/samples/``::

       same_a.jpg  same_b.jpg  diff_a.jpg  diff_b.jpg
       # (.jpeg / .png / .webp all accepted)

   then simply run ``python test/test_phase1_e2e.py``.

2. Explicit paths (e.g. when you have 3 photos and want to reuse one)::

       python test/test_phase1_e2e.py \
           --same-a test/samples/your_photo1.jpeg \
           --same-b test/samples/your_photo2.jpeg \
           --diff-a test/samples/your_photo1.jpeg \
           --diff-b test/samples/teammate_photo.jpeg

Run::

    python test/test_phase1_e2e.py
    # or:  pytest -q test/test_phase1_e2e.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.face.matcher import cosine_similarity  # noqa: E402  (numpy-only, safe at import)

SAMPLES = ROOT / "test" / "samples"
EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic")
ROLES = ("same_a", "same_b", "diff_a", "diff_b")
SAME_GATE = 0.6      # same identity should exceed this
DIFF_GATE = 0.4      # different identity should sit below this


def _resolve_conventional() -> dict:
    """Find conventional sample files (same_a/same_b/diff_a/diff_b) in test/samples/."""
    found = {}
    for role in ROLES:
        for ext in EXTS:
            cand = SAMPLES / f"{role}{ext}"
            if cand.is_file():
                found[role] = cand
                break
    return found


def _embed(path: Path, encode_largest_face) -> np.ndarray:
    emb = encode_largest_face(str(path), normalize=True)
    # Guard: embedding must be the expected 512-dim ArcFace size.
    if emb.shape[0] != 512:
        raise RuntimeError(f"{path.name}: expected 512-dim embedding, got {emb.shape[0]}")
    return emb


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VeriTrace Phase 1 end-to-end face-similarity test.")
    for role in ROLES:
        p.add_argument(f"--{role.replace('_', '-')}", dest=role, type=Path, default=None,
                       help=f"Path to the {role} image (overrides test/samples auto-discovery).")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    # Explicit overrides take priority; fall back to conventional discovery.
    resolved = {}
    conventional = _resolve_conventional()
    for role in ROLES:
        explicit = getattr(args, role)
        if explicit is not None:
            resolved[role] = explicit
        elif role in conventional:
            resolved[role] = conventional[role]
        else:
            resolved[role] = None

    missing = [r for r in ROLES if resolved[r] is None]
    if missing:
        print("skip: end-to-end test needs four sample face images.")
        print("      Either name files conventionally in test/samples/ (any extension):")
        for r in ROLES:
            print(f"        test/samples/{r}.jpg  (.jpeg/.png/.webp ok)")
        print("      ...or pass them explicitly:")
        print("        python test/test_phase1_e2e.py \\")
        for r in ROLES:
            print(f"            --{r} path/to/{r}.jpg \\")
        print(f"      (same_a/same_b = one person in two photos; diff_a/diff_b = two people)")
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

    print(f"[veritrace] backend: {backend_name()}")

    try:
        same_score = cosine_similarity(_embed(resolved["same_a"], encode_largest_face),
                                       _embed(resolved["same_b"], encode_largest_face))
        diff_score = cosine_similarity(_embed(resolved["diff_a"], encode_largest_face),
                                       _embed(resolved["diff_b"], encode_largest_face))
    except ValueError as ve:
        # Raised by encode_largest_face when no face is detected in a photo.
        print(f"FAIL: {ve}")
        print("      Tip: ensure each photo shows a clear, frontal face that fills "
              "a good portion of the frame.")
        return 1
    except Exception as exc:
        print(f"FAIL: embedding step raised {type(exc).__name__}: {exc}")
        return 1

    print(f"same-person similarity : {same_score:.4f}  (expect > {SAME_GATE})")
    print(f"different-person sim.  : {diff_score:.4f}  (expect < {DIFF_GATE})")

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