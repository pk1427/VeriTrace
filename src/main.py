"""VeriTrace CLI entry point.

Phase 1 subcommand:

    scan        Detect a face, print its bounding box, and generate the
                ArcFace embedding. Prints results to stdout and **saves
                nothing sensitive** to disk.

Future phases (added on confirmation) introduce the consent gate, live web
search, evidence fingerprinting, and blockchain upload/subcommand hooks.

Usage::

    python src/main.py scan data/input/face.jpg
    python src/main.py scan data/input/face.jpg --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python src/main.py ...` to resolve `src` as a package from repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np

# Face-backend imports are deferred to cmd_scan() so the CLI (--help, error
# paths) stays usable even before the optional InsightFace/cv2 stack is
# installed.

# Default cosine-similarity threshold for downstream matching (Phase 2+).
DEFAULT_SIM_THRESHOLD = 0.6


def _print(*parts) -> None:
    print(*parts)


def cmd_scan(args: argparse.Namespace) -> int:
    image_path = args.image
    p = Path(image_path)
    if not p.is_file():
        _print(f"error: input image not found: {image_path}")
        return 2

    # Defer the heavy backend import (cv2 / InsightFace) until we actually run.
    from src.face import backend_name, detect_largest_face, encode_largest_face

    backend = backend_name()
    _print(f"[veritrace] backend      : {backend or 'none'}")
    _print(f"[veritrace] input        : {p}")

    if backend is None:
        _print("refusal: no face backend available — install dependencies first "
               "(pip install -r requirements.txt).")
        return 1

    bbox = detect_largest_face(str(p))
    if bbox is None:
        _print("refusal: no face detected in the supplied image — nothing to embed.")
        return 1

    x1, y1, x2, y2 = bbox
    _print(f"[veritrace] bbox         : ({x1}, {y1}) -> ({x2}, {y2})  width={x2 - x1} height={y2 - y1}")

    emb = encode_largest_face(str(p), normalize=True)
    _print(f"[veritrace] embedding    : dim={emb.shape[0]} norm={float(np.linalg.norm(emb)):.4f}")
    _print("[veritrace] embedding    : generated (not persisted — Phase 1 keeps no sensitive data)")
    _print("[veritrace] status       : ok")
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    from src.consent.registry import enroll, is_enrolled, REGISTRY_PATH

    image = args.image
    p = Path(image)
    if not p.is_file():
        _print(f"error: enrollment image not found: {image}")
        return 2
    if is_enrolled(args.subject_id, args.registry):
        _print(f"refusal: subject '{args.subject_id}' is already enrolled — refusing to overwrite.")
        return 1

    try:
        rec = enroll(args.subject_id, str(p), path=args.registry)
    except ValueError as exc:
        _print(f"refusal: {exc}")
        return 1

    _print(f"[veritrace] enrolled  : subject_id={rec['subject_id']}")
    _print(f"[veritrace] enrolled  : photo={rec['enrollment_photo']} sha256={rec['enrollment_sha256'][:16]}…")
    _print(f"[veritrace] enrolled  : dim={rec['embedding_dim']} (512-d ArcFace) at={rec['enrolled_at']}")
    _print(f"[veritrace] registry  : {args.registry or REGISTRY_PATH}  (local, git-ignored)")
    _print("[veritrace] status    : ok")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from src.consent.registry import check_consent, list_subjects, REGISTRY_PATH

    image = args.image
    p = Path(image)
    if not p.is_file():
        _print(f"error: input image not found: {image}")
        return 2

    result = check_consent(str(p), threshold=args.threshold, path=args.registry)
    subs = list_subjects(args.registry)
    _print(f"[veritrace] backend    : (see scan)")
    _print(f"[veritrace] registry   : {args.registry or REGISTRY_PATH}")
    _print(f"[veritrace] enrolled   : {subs if subs else 'none'}")
    _print(result.message)
    if result.granted:
        _print("[veritrace] gate       : OPEN — may proceed to search (Phase 3)")
        return 0
    _print("[veritrace] gate       : CLOSED — refusing to proceed (unenrolled face)")
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veritrace",
        description="Consent-gated face verification + blockchain pipeline (Phase 2: consent gate).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Detect a face and generate its embedding (no persistence).")
    p_scan.add_argument("image", help="Path to the input face image.")
    p_scan.add_argument("--verbose", action="store_true", help="Verbose output.")
    p_scan.set_defaults(func=cmd_scan)

    p_enroll = sub.add_parser(
        "enroll", help="Enroll an owner-owned face into the local consent registry."
    )
    p_enroll.add_argument("subject_id", help="Owner-identified subject id, e.g. 'prasad'.")
    p_enroll.add_argument("image", help="Clear face photo of the owner to enroll.")
    p_enroll.add_argument("--registry", type=Path, default=None,
                          help="Override registry path (default: data/consent_registry.json).")
    p_enroll.set_defaults(func=cmd_enroll)

    p_check = sub.add_parser(
        "check", help="Run the consent gate on an image (must OPEN before any search)."
    )
    p_check.add_argument("image", help="Face photo to verify consent for.")
    p_check.add_argument("--threshold", type=float, default=None,
                         help="Override the consent cosine threshold (default 0.6).")
    p_check.add_argument("--registry", type=Path, default=None,
                         help="Override registry path (default: data/consent_registry.json).")
    p_check.set_defaults(func=cmd_check)

    # Reserved subcommand slots for later phases (implemented only after
    # explicit confirmation). They intentionally error out now.
    for name in ("search", "evidence", "blockchain"):
        p = sub.add_parser(name, help=f"[Phase N] {name} — not implemented yet.")
        p.set_defaults(func=_not_implemented(name))

    return parser


def _not_implemented(name: str):
    def _run(args: argparse.Namespace) -> int:  # pragma: no cover - gated by design
        print(f"refusal: '{name}' is not implemented (Phase 2).", file=sys.stderr)
        print("Ask the operator to confirm before starting Phase 3.", file=sys.stderr)
        return 62
    return _run


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
