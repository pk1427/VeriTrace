"""Phase 2 consent-gate tests (isolated, real-face aware).

These run against an **isolated temporary registry** (never the real
``data/consent_registry.json``) so your actual enrollment is untouched.

Auto-discovers sample photos in ``test/samples/``:
  * conventional names: same_a/same_b/diff_a/diff_b
  * descriptive names:  your_photo1/your_photo2 + teammate_photo

Run standalone or with pytest::

    python test/test_phase2_consent.py
    pytest -q test/test_phase2_consent.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.consent.registry import (  # noqa: E402
    enroll,
    check_consent,
    load_registry,
    reset_registry,
    ConsentResult,
    DEFAULT_THRESHOLD,
)

SAMPLES = ROOT / "test" / "samples"
EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic")


class Skip(Exception):
    """Raised by a test to signal 'skipped' (not a failure)."""


def _find(names):
    for n in names:
        for ext in EXTS:
            f = SAMPLES / f"{n}{ext}"
            if f.is_file():
                return f
    return None


def _discover():
    """Return (enroll, same, diff_a, diff_b) paths or None if unavailable."""
    enroll_p = _find(["same_a", "your_photo1", "you_photo1"])
    same_p = _find(["same_b", "your_photo2", "you_photo2"])
    diff_a = _find(["diff_a", "your_photo1"])
    diff_b = _find(["diff_b", "teammate_photo", "teammate"])
    if not all([enroll_p, same_p, diff_a, diff_b]):
        return None
    return enroll_p, same_p, diff_a, diff_b


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_enroll_store_and_retrieve() -> None:
    enroll_p = _find(["same_a", "your_photo1", "you_photo1"])
    if not enroll_p:
        raise Skip("need an enroll photo (same_a or your_photo1) in test/samples/")
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    rec = enroll("unit_subject", str(enroll_p), path=tmp)
    _assert(rec["subject_id"] == "unit_subject", "subject_id stored")
    _assert(rec["embedding_dim"] == 512, "embedding is 512-d")
    reg = load_registry(tmp)
    _assert(len(reg["subjects"]) == 1, "one subject persisted")
    # Biometric vector must be present for matching but never echoed in logs:
    _assert("embedding_b64" in reg["subjects"][0], "embedding stored for matching")


def _placeholder() -> Path:
    """Create a tiny non-face image for privacy-preserving empty-registry tests."""
    import cv2
    p = Path(tempfile.gettempdir()) / "_veritrace_placeholder.png"
    cv2.imwrite(str(p), (np.zeros((80, 80, 3), np.uint8)))
    return p


def test_gate_grants_enrolled_same_person() -> None:
    photos = _discover()
    if not photos:
        raise Skip("need same_a/same_b (or your_photo1/your_photo2) in test/samples/")
    enroll_p, same_p, _, _ = photos
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    enroll("owner", str(enroll_p), path=tmp)
    res: ConsentResult = check_consent(str(same_p), path=tmp)
    _assert(res.granted, f"gate should GRANT same person (score={res.best_score})")
    _assert(res.best_score > DEFAULT_THRESHOLD, "same-person score above threshold")
    _assert(res.best_subject == "owner", "matched enrolled subject")


def test_gate_refuses_unenrolled_different_person() -> None:
    photos = _discover()
    if not photos:
        raise Skip("need diff photos (or teammate_photo) in test/samples/")
    enroll_p, _, diff_a, diff_b = photos
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    enroll("owner", str(enroll_p), path=tmp)
    res = check_consent(str(diff_b), path=tmp)
    _assert(not res.granted, f"gate should REFUSE unenrolled face (score={res.best_score})")
    _assert(res.best_score < DEFAULT_THRESHOLD, "different-person score below threshold")


def test_duplicate_enroll_is_refused() -> None:
    photos = _discover()
    if not photos:
        raise Skip("need an enroll photo in test/samples/")
    enroll_p = photos[0]
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    enroll("owner", str(enroll_p), path=tmp)
    reg = load_registry(tmp)
    _assert(len(reg["subjects"]) == 1, "first enroll persisted")

    from src.consent.registry import is_enrolled
    _assert(is_enrolled("owner", path=tmp), "owner is enrolled")

    try:
        enroll("owner", str(enroll_p), path=tmp)
        _assert(False, "duplicate enroll should be refused")
    except ValueError:
        pass  # expected: refusing to overwrite an enrolled subject


def test_empty_registry_refuses_without_embedding_face() -> None:
    """A face is never embedded when the registry is empty — fail closed."""
    tmp = Path(tempfile.mkdtemp()) / "reg.json"
    reset_registry(tmp)  # empty subjects
    # Even a real (non-face) image must be refused as 'registry empty' and must
    # NOT be run through the face backend (privacy: no processing of an
    # unenrolled face).
    res = check_consent(str(_placeholder()), path=tmp)
    _assert(not res.granted, "empty registry must deny")
    _assert("empty" in res.message, f"reason should be 'empty', got: {res.message!r}")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures, skipped = 0, 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Skip as s:
            skipped += 1
            print(f"SKIP  {t.__name__}: {s}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures - skipped}/{len(tests)} passed, {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
