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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veritrace",
        description="Consent-gated face verification + blockchain pipeline (Phase 1: scan).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Detect a face and generate its embedding (no persistence).")
    p_scan.add_argument("image", help="Path to the input face image.")
    p_scan.add_argument("--verbose", action="store_true", help="Verbose output.")
    p_scan.set_defaults(func=cmd_scan)

    # Reserved subcommand slots for later phases (implemented only after
    # explicit confirmation). They intentionally error out now.
    for name in ("consent", "search", "evidence", "blockchain"):
        p = sub.add_parser(name, help=f"[Phase N] {name} — not implemented yet.")
        p.set_defaults(func=_not_implemented(name))

    return parser


def _not_implemented(name: str):
    def _run(args: argparse.Namespace) -> int:  # pragma: no cover - gated by design
        print(f"refusal: '{name}' is not implemented in Phase 1.", file=sys.stderr)
        print("Ask the operator to confirm before touching anything outside src/face/.", file=sys.stderr)
        return 62
    return _run


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
