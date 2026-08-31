"""VeriTrace CLI entry point.

Phase 1 subcommand:

    scan        Detect a face, print its bounding box, and generate the
                ArcFace embedding. Prints results to stdout and **saves
                nothing sensitive** to disk.

Phase 2 (consent gate) subcommands:

    enroll      Enroll an owner-identified subject into the local consent registry.
    check       Run the consent gate on an image (must OPEN before any search/chain).

Phase 3 (evidence + chain) subcommands:

    evidence    Fingerprint an image into tamper-evident SHA-256 evidence (gated).
    blockchain  Anchor/verify evidence on EvidenceRegistry.sol (gated; Amoy/localhost).

The consent gate is the hard pre-condition for every Phase 3 path: if `check`
does not grant consent, no evidence is produced and no on-chain call is made.

Usage::

    python src/main.py scan data/input/face.jpg
    python src/main.py scan data/input/face.jpg --verbose
    python src/main.py enroll prasad data/input/prasad.jpg
    python src/main.py check data/input/face.jpg
    python src/main.py evidence data/input/face.jpg
    python src/main.py blockchain anchor data/input/face.jpg --key <pk>
    python src/main.py blockchain verify data/input/face.jpg --rpc <rpc>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow `python src/main.py ...` to resolve `src` as a package from repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load a local .env (git-ignored) so Phase 3 blockchain vars resolve whether
# they were `export`ed or only written to .env.example's twin. Optional.
try:
    from dotenv import load_dotenv

    load_dotenv(str(_ROOT / ".env"))
except Exception:  # pragma: no cover - dotenv is optional
    pass

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


def _run_gate(args: argparse.Namespace):
    """Enforce the Phase 2 consent gate. Returns ConsentResult."""
    from src.consent.registry import check_consent
    return check_consent(args.image, threshold=args.threshold, path=args.registry)


def cmd_evidence(args: argparse.Namespace) -> int:
    from src.evidence import build_evidence_record

    result = _run_gate(args)
    _print("[veritrace] gate        :", "OPEN" if result.granted else "CLOSED")
    if not result.granted:
        _print(result.message)
        _print("[veritrace] gate        : CLOSED — refusing to produce evidence (unenrolled face)")
        return 1

    rec = build_evidence_record(result.best_subject, args.image)
    payload = rec.to_anchor_payload()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{result.best_subject}__{rec.image_sha256[:8]}.json"
    out_file.write_text(json.dumps(payload, indent=2))
    _print(f"[veritrace] subject     : {result.best_subject}")
    _print(f"[veritrace] fingerprint : {rec.image_sha256}")
    _print(f"[veritrace] evidence    : {out_file}")
    _print("[veritrace] tamper      : SHA-256 — any change to the image invalidates the anchor")
    _print("[veritrace] next        : anchor with `veritrace blockchain anchor {image} --key <pk>`")
    _print("[veritrace] status      : ok")
    return 0


def cmd_blockchain_anchor(args: argparse.Namespace) -> int:
    from src.blockchain import anchor_evidence

    result = _run_gate(args)
    _print("[veritrace] gate        :", "OPEN" if result.granted else "CLOSED")
    if not result.granted:
        _print(result.message)
        _print("[veritrace] gate        : CLOSED — refusing to anchor (unenrolled face)")
        return 1

    try:
        out = anchor_evidence(
            result.best_subject, args.image, args.contract, args.rpc,
            private_key=args.key, out_dir=Path(args.out_dir),
        )
    except RuntimeError as exc:
        _print(f"refusal: anchor failed — {exc}")
        return 1
    if out.get("_status") == "pending_signer":
        _print("[veritrace] anchor      : unsigned — wrote", out["_request_file"])
        _print("[veritrace] signer      : none provided (--key / PRIVATE_KEY); broadcast yourself")
        _print("[veritrace] calldata    :", out["calldata"])
        return 0
    _print(f"[veritrace] tx          : {out.get('tx_hash')}")
    _print("[veritrace] anchored    : on-chain EvidenceRegistry recorded the evidence fingerprint")
    return 0


def cmd_blockchain_verify(args: argparse.Namespace) -> int:
    from src.blockchain import verify_onchain
    from src.evidence import build_evidence_record

    result = _run_gate(args)
    _print("[veritrace] gate        :", "OPEN" if result.granted else "CLOSED")
    if not result.granted:
        _print(result.message)
        _print("[veritrace] gate        : CLOSED — refusing to verify (unenrolled face)")
        return 1

    rec = build_evidence_record(result.best_subject, args.image)
    try:
        out = verify_onchain(rec.image_sha256, args.contract, args.rpc)
    except RuntimeError as exc:
        _print(f"refusal: on-chain verify failed — {exc}")
        return 1
    _print(f"[veritrace] fingerprint : {rec.image_sha256}")
    _print(f"[veritrace] subject     : {result.best_subject}")
    if out["is_recorded"]:
        r = out["record"] or {}
        _print(f"[veritrace] on-chain    : RECORDED  submitter={r.get('submitter')} createdAt={r.get('createdAt')}")
        _print("[veritrace] verify      : evidence fingerprint matches the on-chain anchor")
        return 0
    _print("[veritrace] on-chain    : UNRECORDED — fingerprint was never anchored (or tampered)")
    return 1


def cmd_blockchain_deploy(args: argparse.Namespace) -> int:
    import shutil

    npx = shutil.which("npx")
    if npx is None:
        _print("refusal: npx not found — install Node.js + run `npm install` first.", file=sys.stderr)
        _print("Then: npx hardhat run scripts/deploy.js --network", args.network, file=sys.stderr)
        return 1
    if args.network == "amoy" and not (os.environ.get("POLYGON_RPC_URL") and os.environ.get("PRIVATE_KEY")):
        _print("refusal: Amoy deploy requires POLYGON_RPC_URL + PRIVATE_KEY in .env (git-ignored).", file=sys.stderr)
        _print("Local node? run: npx hardhat node   (then use --network localhost)", file=sys.stderr)
        return 1
    _print(f"[veritrace] deploy      : EvidenceRegistry via hardhat ({args.network})")
    import subprocess
    proc = subprocess.run([npx, "hardhat", "run", "scripts/deploy.js", "--network", args.network], check=False)
    return int(proc.returncode)


def cmd_search(args: argparse.Namespace) -> int:
    from src.search import default_allowed_domains, search as web_search

    result = _run_gate(args)
    _print("[veritrace] gate        :", "OPEN" if result.granted else "CLOSED")
    if not result.granted:
        _print(result.message)
        _print("[veritrace] gate        : CLOSED — refusing to search (unenrolled face)")
        return 1

    allowed = default_allowed_domains()
    try:
        matches = web_search(args.image, allowed, max_results=args.max_results)
    except Exception as exc:
        _print(f"refusal: reverse-image search failed — {type(exc).__name__}: {exc}")
        return 1

    _print(f"[veritrace] subject     : {result.best_subject}")
    _print(f"[veritrace] matches     : {len(matches)} (allow-list={','.join(allowed)})")
    for m in matches[: args.max_results]:
        _print(f"  - {m.get('domain') or m.get('host')}  {m['url'][:120]}")
    _print("[veritrace] gate        : OPEN — results filtered to approved domains only")
    _print("[veritrace] status      : ok")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veritrace",
        description="Consent-gated face verification + blockchain pipeline (Phase 2 consent gate, Phase 3 evidence/chain).",
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

    p_search = sub.add_parser(
        "search", help="Consent-gated reverse-image search of approved domains (Phase 2b).",
    )
    p_search.add_argument("image", help="Face photo to search (consent-gated).")
    p_search.add_argument("--registry", type=Path, default=None,
                          help="Override consent registry path (default: data/consent_registry.json).")
    p_search.add_argument("--threshold", type=float, default=None,
                          help="Override the consent cosine threshold (default 0.6).")
    p_search.add_argument("--max-results", type=int, default=10,
                          help="Max web-detection matches per image (default 10).")
    p_search.set_defaults(func=cmd_search)

    p_ev = sub.add_parser(
        "evidence", help="Fingerprint an image into tamper-evident SHA-256 evidence (consent-gated)."
    )
    p_ev.add_argument("image", help="Face photo to fingerprint (must pass the consent gate).")
    p_ev.add_argument("--registry", type=Path, default=None,
                      help="Override consent registry path (default: data/consent_registry.json).")
    p_ev.add_argument("--threshold", type=float, default=None,
                      help="Override the consent cosine threshold (default 0.6).")
    p_ev.add_argument("--out-dir", type=Path, default=Path("data/evidence"),
                      help="Directory for the evidence JSON (default: data/evidence).")
    p_ev.set_defaults(func=cmd_evidence)

    p_bc = sub.add_parser("blockchain", help="Anchor or verify evidence on-chain (consent-gated).")
    bc = p_bc.add_subparsers(dest="action", required=True, metavar="{anchor,verify,deploy}")

    p_anchor = bc.add_parser("anchor", help="Anchor this image's fingerprint on EvidenceRegistry.sol.")
    p_anchor.add_argument("image", help="Face photo whose fingerprint to anchor.")
    p_anchor.add_argument("--registry", type=Path, default=None,
                          help="Override consent registry path (default: data/consent_registry.json).")
    p_anchor.add_argument("--threshold", type=float, default=None,
                          help="Override the consent cosine threshold (default 0.6).")
    p_anchor.add_argument("--contract", default=os.environ.get("VERIFACE_CONTRACT_ADDRESS"),
                          help="EvidenceRegistry contract address (or VERIFACE_CONTRACT_ADDRESS).")
    p_anchor.add_argument("--rpc", default=os.environ.get("POLYGON_AMOY_RPC_URL") or os.environ.get("POLYGON_RPC_URL"),
                          help="JSON-RPC endpoint (or POLYGON_AMOY_RPC_URL).")
    p_anchor.add_argument("--key", default=os.environ.get("PRIVATE_KEY"),
                          help="Owner private key (hex) (or PRIVATE_KEY). Omitting writes an unsigned request.")
    p_anchor.add_argument("--out-dir", type=Path, default=Path("data/evidence"),
                          help="Directory for unsigned anchor requests.")
    p_anchor.set_defaults(func=cmd_blockchain_anchor)

    p_verify = bc.add_parser("verify", help="Independently verify an evidence fingerprint on-chain.")
    p_verify.add_argument("image", help="Face photo whose fingerprint to verify.")
    p_verify.add_argument("--registry", type=Path, default=None,
                          help="Override consent registry path (default: data/consent_registry.json).")
    p_verify.add_argument("--threshold", type=float, default=None,
                          help="Override the consent cosine threshold (default 0.6).")
    p_verify.add_argument("--contract", default=os.environ.get("VERIFACE_CONTRACT_ADDRESS"),
                          help="EvidenceRegistry contract address (or VERIFACE_CONTRACT_ADDRESS).")
    p_verify.add_argument("--rpc", default=os.environ.get("POLYGON_AMOY_RPC_URL") or os.environ.get("POLYGON_RPC_URL"),
                          help="JSON-RPC endpoint (or POLYGON_AMOY_RPC_URL).")
    p_verify.set_defaults(func=cmd_blockchain_verify)

    p_deploy = bc.add_parser("deploy", help="Deploy EvidenceRegistry.sol via Hardhat (Amoy/localhost).")
    p_deploy.add_argument("--network", default="amoy", choices=["amoy", "localhost"],
                          help="Target network (default: amoy).")
    p_deploy.set_defaults(func=cmd_blockchain_deploy)

    return parser


def _not_implemented(name: str, reason: str = "not implemented yet."):
    def _run(args: argparse.Namespace) -> int:  # pragma: no cover - gated by design
        _print(f"refusal: '{name}' is deferred — {reason}")
        _print("All Phase 3 paths are consent-gated via `check` first.", file=sys.stderr)
        return 62
    return _run


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
