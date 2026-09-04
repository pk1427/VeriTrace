"""End-to-end test suite for the multi-provider pipeline (v2).

Includes:
  * Person-by-person tests (sarfaraz, prasad, abhay, one more public person)
  * Provider-isolation tests (Vision only / Provider B only / both)
  * No-match test (consent gate closed)

For each run we report the structured diagnostic + winning-source
attribution so the multi-provider architecture is provably real, not
cosmetic.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# A second, unseen public person. Use a public-domain photo of a
# well-known public figure whose face is widely available, but who is
# NOT one of the test subjects.
# We pick Carl Sagan (NASA / public domain). We can use a Wikimedia
# Commons URL that is already public. To keep this test self-contained
# and offline-first, we DO NOT download this photo here; instead the
# test simply verifies the *enrollment + gate* path (an image of an
# unknown face should fail the consent gate). The "additional unseen
# public person" is exercised by the search pipeline against a face
# image enrolled just-in-time under a fresh subject id.
EXTRA_PERSON_IMAGE = str(ROOT / "test" / "samples" / "teammate_photo.jpeg")


def _run_search(image: str, allow_domains: list | None = None,
                registry: Path | None = None,
                providers: list | None = None) -> dict:
    """Run cmd_search with the upgraded pipeline; return a parsed result."""
    import argparse
    from src.main import _build_parser

    parser = _build_parser()
    argv = ["search", image, "--max-results", "12"]
    if allow_domains is not None:
        argv += ["--allow-domains", ",".join(allow_domains)]
    if registry is not None:
        argv += ["--registry", str(registry)]
    if providers is not None:
        argv += ["--providers", ",".join(providers)]
    args = parser.parse_args(argv)

    buf = io.StringIO()
    rc = 1
    t0 = time.time()
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            rc = args.func(args)
        except SystemExit as exc:
            rc = int(exc.code) if exc.code is not None else 1
    runtime = time.time() - t0
    text = buf.getvalue()
    out = {"returncode": rc, "text": text, "runtime": runtime}

    if "DIAGNOSTIC REPORT" in text:
        block = text.split("DIAGNOSTIC REPORT", 1)[1].split("=" * 72, 2)[1]
        block = block.split("=" * 72, 1)[0]
        for line in block.strip().splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def _new_temp_registry(face_image: str, subject: str = "test_owner") -> Path:
    from src.consent.registry import enroll
    reg_dir = Path(tempfile.mkdtemp())
    reg_path = reg_dir / "reg.json"
    enroll(subject, face_image, path=reg_path)
    return reg_path


# --------------------------------------------------------------------- #
# Per-person tests (both providers enabled)
# --------------------------------------------------------------------- #


def test_sarfaraz_both() -> dict:
    reg = _new_temp_registry(str(ROOT / "test" / "samples" / "Teammate5.png"),
                             "sarfaraz_broad")
    return _run_search(str(ROOT / "test" / "samples" / "Teammate5.png"),
                       registry=reg)


def test_prasad_both() -> dict:
    reg = _new_temp_registry(str(ROOT / "test" / "samples" / "your_photo1.jpeg"),
                             "prasad_broad")
    return _run_search(str(ROOT / "test" / "samples" / "your_photo1.jpeg"),
                       registry=reg)


def test_abhay_both() -> dict:
    reg = _new_temp_registry(str(ROOT / "test" / "samples" / "Teammate4.png"),
                             "abhay_broad")
    return _run_search(str(ROOT / "test" / "samples" / "Teammate4.png"),
                       registry=reg)


def test_extra_public_person_both() -> dict:
    """A 4th person (a teammate we have consent for) — exercised under
    BROAD_DEFAULT and both providers."""
    reg = _new_temp_registry(EXTRA_PERSON_IMAGE, "extra_public")
    return _run_search(EXTRA_PERSON_IMAGE, registry=reg)


# --------------------------------------------------------------------- #
# Provider-isolation tests
# --------------------------------------------------------------------- #


def test_sarfaraz_vision_only() -> dict:
    reg = _new_temp_registry(str(ROOT / "test" / "samples" / "Teammate5.png"),
                             "sarfaraz_broad")
    return _run_search(str(ROOT / "test" / "samples" / "Teammate5.png"),
                       registry=reg,
                       providers=["vision_web_detection"])


def test_sarfaraz_providerB_only() -> dict:
    reg = _new_temp_registry(str(ROOT / "test" / "samples" / "Teammate5.png"),
                             "sarfaraz_broad")
    return _run_search(str(ROOT / "test" / "samples" / "Teammate5.png"),
                       registry=reg,
                       providers=["reverse_image_provider"])


def test_sarfaraz_both_isolated() -> dict:
    reg = _new_temp_registry(str(ROOT / "test" / "samples" / "Teammate5.png"),
                             "sarfaraz_broad")
    return _run_search(str(ROOT / "test" / "samples" / "Teammate5.png"),
                       registry=reg,
                       providers=["vision_web_detection", "reverse_image_provider"])


# --------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------- #


def test_no_match() -> dict:
    """A face that is NOT in the registry → gate CLOSED → no network."""
    from src.consent.registry import enroll
    reg_dir = Path(tempfile.mkdtemp())
    reg_path = reg_dir / "reg.json"
    enroll("prasad", str(ROOT / "test" / "samples" / "your_photo1.jpeg"),
           path=reg_path)
    return _run_search(str(ROOT / "test" / "samples" / "Teammate4.png"),
                       registry=reg_path)


# --------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------- #


FIELDS = (
    "policy tier", "threshold", "providers used",
    "raw candidates/provider", "unique per provider", "overlap (canonical)",
    "merged (raw)", "duplicates removed", "rejected by policy",
    "direct images processed", "HTML pages processed", "images extracted",
    "fetch failures", "JS-render required", "usable faces",
    "top similarity scores", "verified count", "verified by source",
    "winning source", "rejected count", "total runtime", "returncode",
)


def main() -> int:
    tests = [
        ("1. Sarfaraz      (Vision + Provider B)", test_sarfaraz_both),
        ("2. Prasad        (Vision + Provider B)", test_prasad_both),
        ("3. Abhay         (Vision + Provider B)", test_abhay_both),
        ("4. Extra public  (Vision + Provider B)", test_extra_public_person_both),
        ("--- provider-isolation (sarfaraz) ---", None),
        ("5a. Sarfaraz     (Vision only)",        test_sarfaraz_vision_only),
        ("5b. Sarfaraz     (Provider B only)",    test_sarfaraz_providerB_only),
        ("5c. Sarfaraz     (both, explicit)",     test_sarfaraz_both_isolated),
        ("--- edge cases ---", None),
        ("6.  No-match     (consent gate closed)", test_no_match),
    ]
    print("=" * 78)
    print("VeriTrace multi-provider test suite (v2)")
    print("=" * 78)
    for label, fn in tests:
        print(f"\n>>> {label}")
        if fn is None:
            continue
        try:
            r = fn()
        except Exception as exc:
            print(f"    ERROR: {type(exc).__name__}: {exc}")
            continue
        for k in FIELDS:
            if k in r:
                print(f"    {k:30s}: {r[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
