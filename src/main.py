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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

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

    allowed_domains = None
    if args.allowed_domains:
        allowed_domains = [d.strip().lower() for d in args.allowed_domains.split(",") if d.strip()]

    try:
        rec = enroll(args.subject_id, str(p), path=args.registry, allowed_domains=allowed_domains)
    except (ValueError, FileNotFoundError) as exc:
        _print(f"refusal: {exc}")
        return 1

    _print(f"[veritrace] enrolled  : subject_id={rec['subject_id']}")
    _print(f"[veritrace] enrolled  : photo={rec['enrollment_photo']} sha256={rec['enrollment_sha256'][:16]}…")
    _print(f"[veritrace] enrolled  : dim={rec['embedding_dim']} (512-d ArcFace) at={rec['enrolled_at']}")
    _print(f"[veritrace] scope     : allowed_domains={','.join(allowed_domains) if allowed_domains else '<none -> default>'}")
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
    """Multi-provider consent-gated reverse-image search.

    Pipeline (per request):
        1. consent gate (Phase 2)
        2. resolve public-surface policy (owner / run / broad default)
        3. multi-provider candidate discovery
        4. URL canonicalize + dedup
        5. apply policy (never silently)
        6. fetch (direct image OR HTML page with img/og/twitter/link/source extraction)
        7. InsightFace verify
        8. structured diagnostic report
    """
    res = _run_search(args)
    _print_diagnostic(res)
    return 0 if res.gate_open else 1


def _run_search(args: argparse.Namespace) -> "SearchResult":
    """The full search pipeline, returning a structured :class:`SearchResult`.

    Both ``cmd_search`` (prints the diagnostic) and ``cmd_full`` (uses
    the structured result for ranking + canonical evidence + chain)
    consume this function.
    """
    import time
    from src.search import (
        Candidate,
        ActivePolicy,
        resolve_policy,
        filter_candidates,
        discover_candidates,
        fetch_candidate,
        JS_RENDER_REQUIRED,
        extract_image_urls_from_html,
    )
    from src.search.candidate_normalizer import normalize_url
    from src.face import encode_largest_face, detect_largest_face, cosine_similarity

    t0 = time.time()

    result = _run_gate(args)
    if not result.granted:
        return SearchResult(
            gate_open=False,
            gate_message=result.message,
            subject_id=result.best_subject,
            best_score=result.best_score,
            threshold=result.threshold,
            policy=None,
            providers_used=[],
            by_provider={},
            unique_per_provider={},
            overlap_by_pair={},
            raw_count=0,
            duplicates_removed=0,
            rejected_by_policy_count=0,
            direct_image_count=0,
            html_page_count=0,
            images_extracted_count=0,
            fetch_failures=0,
            js_render_required=0,
            js_render_attempts=0,
            js_render_successes=0,
            js_render_imgs_recovered=0,
            js_render_faces_recovered=0,
            js_render_verified_recovered=0,
            js_renders_capped=0,
            js_render_time_s=0.0,
            js_status="JS_RENDER_DISABLED",
            browser_unavailable=False,
            js_render_skipped_deferred=0,
            usable_faces=0,
            verified=[],
            rejected=[],
            top_scores=[],
            verified_by_source={},
            winning_source="",
            runtime_s=0.0,
        )

    # Policy: owner (per-record) > run (--allow-domains) > broad default
    run_domains = (
        [d.strip().lower() for d in (args.allow_domains or "").split(",") if d.strip()]
        if args.allow_domains else None
    )
    policy = resolve_policy(result.allowed_domains, run_domains)
    threshold = args.threshold if args.threshold is not None else result.threshold

    enabled = (
        [p.strip() for p in args.providers.split(",") if p.strip()]
        if args.providers else None
    )

    # 1. multi-provider discovery
    raw = discover_candidates(args.image, enabled=enabled, max_results=args.max_results)

    by_provider: dict = {}
    for c in raw:
        by_provider.setdefault(c.source, 0)
        by_provider[c.source] += 1

    # 2. dedup by canonical URL
    seen_keys: set = set()
    deduped: list = []
    for c in raw:
        if c.url.startswith("__provider_error__::"):
            deduped.append(c)
            continue
        k = c.canonical_key()
        if k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(c)
    duplicates_removed = len(raw) - len(deduped)

    # 3. apply public-surface policy
    real_candidates = [c for c in deduped if not c.url.startswith("__provider_error__::")]
    kept, rejected_by_policy = filter_candidates(real_candidates, policy)

    per_provider_keys: dict = {n: set() for n in by_provider.keys()}
    for c in kept:
        per_provider_keys.setdefault(c.source, set()).add(c.canonical_key())
    providers_used = sorted(by_provider.keys())
    overlap_by_pair = {}
    for i, a in enumerate(providers_used):
        for b in providers_used[i + 1:]:
            inter = per_provider_keys[a] & per_provider_keys[b]
            if inter:
                overlap_by_pair[f"{a}+{b}"] = len(inter)
    unique_per_provider = {n: len(per_provider_keys.get(n, set())) for n in providers_used}

    # 4. fetch + verify
    try:
        reference = encode_largest_face(args.image, normalize=True)
    except Exception:
        return SearchResult(
            gate_open=True, gate_message="reference face embedding failed",
            subject_id=result.best_subject, best_score=result.best_score,
            threshold=threshold, policy=policy,
            providers_used=providers_used, by_provider=by_provider,
            unique_per_provider=unique_per_provider, overlap_by_pair=overlap_by_pair,
            raw_count=len(raw), duplicates_removed=duplicates_removed,
            rejected_by_policy_count=len(rejected_by_policy),
            direct_image_count=0, html_page_count=0, images_extracted_count=0,
            fetch_failures=0, js_render_required=0,
            js_render_attempts=0, js_render_successes=0,
            js_render_imgs_recovered=0, js_render_faces_recovered=0,
            js_render_verified_recovered=0, js_renders_capped=0,
            js_render_time_s=0.0, js_status="JS_RENDER_DISABLED",
            browser_unavailable=False, js_render_skipped_deferred=0,
            usable_faces=0, verified=[], rejected=[],
            top_scores=[], verified_by_source={}, winning_source="",
            runtime_s=time.time() - t0,
        )

    verified: list = []   # (host, url, score, source, fetch_mode, image_sha256)
    image_sha256_lookup: dict = {}  # image_url -> sha256
    rejected: list = []
    direct_image_count = 0
    html_page_count = 0
    images_extracted_count = 0
    fetch_failures = 0
    js_render_required = 0
    usable_faces = 0

    js_required_urls: list = []
    js_render_attempts = 0
    js_render_successes = 0
    js_render_imgs_recovered = 0
    js_render_faces_recovered = 0
    js_render_verified_recovered = 0
    js_render_time_s = 0.0
    js_renders_capped = 0
    browser_unavailable = False
    js_render_skipped_deferred = 0

    from src.search.ranking import sha256_file as _sha256_file
    from src.evidence import sha256_file as _ev_sha256_file

    for c in kept[: args.max_results]:
        url = c.url
        host = (urlparse(url).hostname or "").lower()
        path, _ct, reason = fetch_candidate(url)
        fetch_mode = "static"
        if path is None:
            fetch_failures += 1
            if reason == JS_RENDER_REQUIRED:
                js_render_required += 1
                js_required_urls.append((host, url, c))
            rejected.append((host, url, f"fetch: {reason}"))
            _print(f"  x {host or '?'}  {url[:95]}  ... {reason}  [rejected]")
            continue
        if c.candidate_type == "image":
            direct_image_count += 1
        else:
            html_page_count += 1
            try:
                import urllib.request as _ur
                with _ur.urlopen(_ur.Request(url, headers={"User-Agent": "veritrace/1.0"}), timeout=10) as r:
                    html = r.read(2 * 1024 * 1024).decode("utf-8", "ignore")
                imgs = extract_image_urls_from_html(html, base_url=url)
                images_extracted_count += len(imgs)
            except Exception:
                pass
        # Compute the candidate image SHA-256 before we unlink the temp file.
        # The same hex is what the canonical evidence bundle embeds and
        # what the on-chain commitment protects.
        try:
            image_sha = _ev_sha256_file(path)
        except Exception:
            image_sha = ""
        try:
            if detect_largest_face(path) is None:
                rejected.append((host, url, "no face in fetched image"))
                _print(f"  x {host}  {url[:95]}  ... no face detected  [rejected]")
                continue
            usable_faces += 1
            cand = encode_largest_face(path, normalize=True)
            score = cosine_similarity(reference, cand)
            if score >= threshold:
                image_sha256_lookup[url] = image_sha
                verified.append((host, url, score, c.source, fetch_mode, image_sha))
                _print(f"  + {host}  {url[:95]}  similarity={score:.4f}  [VERIFIED]  (source={c.source}, mode={fetch_mode})")
            else:
                rejected.append((host, url, f"similarity={score:.4f} < {threshold}"))
                _print(f"  - {host}  {url[:95]}  similarity={score:.4f}  [rejected]")
        except Exception as exc:
            rejected.append((host, url, f"verify error: {type(exc).__name__}: {exc}"))
            _print(f"  - {host}  {url[:95]}  ... {exc}  [rejected]")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Optional JS-render fallback for JS_RENDER_REQUIRED pages.
    # ------------------------------------------------------------------ #
    if args.allow_js_render and js_required_urls:
        from src.search.headless_browser import (
            is_browser_available,
            render_url,
            JS_RENDER_SUCCESS as _HB_SUCCESS,
        )
        if not is_browser_available():
            browser_unavailable = True
            _print("[veritrace] js-render   : BROWSER_UNAVAILABLE (no Chrome/Chromium on system) — skipping")
        else:
            budget = max(0, int(args.max_js_renders or 0))
            for host, url, c in js_required_urls:
                if js_render_attempts >= budget:
                    js_renders_capped += 1
                    _print(f"  x {host}  {url[:95]}  ... JS_RENDER_CAP_REACHED  [rejected]")
                    continue
                js_render_attempts += 1
                _print(f"  ? {host}  {url[:95]}  ... JS_RENDER_ATTEMPTED")
                res = render_url(url, timeout_s=15.0, wait_after_load_s=2.0)
                js_render_time_s += float(res.get("elapsed_s", 0) or 0)
                if res.get("status") != _HB_SUCCESS:
                    _print(f"  x {host}  ... {res.get('status','JS_RENDER_FAILED')}: {res.get('reason','')}  [rejected]")
                    continue
                js_render_successes += 1
                rendered_html = res.get("rendered_html", "")
                dom_imgs = res.get("dom_image_urls", [])
                imgs = extract_image_urls_from_html(rendered_html, base_url=res.get("url", url))
                for u in dom_imgs:
                    abs_u = urljoin(res.get("url", url), u)
                    if abs_u not in imgs:
                        imgs.append(abs_u)
                if not imgs:
                    _print(f"  x {host}  ... JS_RENDER_NO_USABLE_IMAGE  [rejected]")
                    continue
                js_render_imgs_recovered += len(imgs)
                for img_url in imgs:
                    path2, _ct2, reason2 = fetch_candidate(img_url)
                    if path2 is None:
                        rejected.append((host, img_url, f"js-render follow-up: {reason2}"))
                        continue
                    try:
                        if detect_largest_face(path2) is None:
                            rejected.append((host, img_url, "no face in js-rendered image"))
                            continue
                        js_render_faces_recovered += 1
                        cand = encode_largest_face(path2, normalize=True)
                        score = cosine_similarity(reference, cand)
                        try:
                            img_sha = _ev_sha256_file(path2)
                        except Exception:
                            img_sha = ""
                        if score >= threshold:
                            js_render_verified_recovered += 1
                            image_sha256_lookup[img_url] = img_sha
                            verified.append((host, img_url, score, c.source, "js_rendered", img_sha))
                            _print(f"  + {host}  {img_url[:95]}  similarity={score:.4f}  [VERIFIED]  (source={c.source}, mode=js_rendered)")
                        else:
                            rejected.append((host, img_url, f"js-render similarity={score:.4f} < {threshold}"))
                    except Exception as exc:
                        rejected.append((host, img_url, f"js-render verify error: {exc}"))
                    finally:
                        try:
                            os.unlink(path2)
                        except OSError:
                            pass
    elif not args.allow_js_render and js_required_urls:
        js_render_skipped_deferred = len(js_required_urls)
        for host, url, c in js_required_urls:
            rejected.append((host, url, "JS_RENDER_REQUIRED_DEFERRED"))
            _print(f"  x {host}  {url[:95]}  ... JS_RENDER_REQUIRED_DEFERRED  [rejected]")

    top = sorted(verified, key=lambda x: -x[2])[:10]
    runtime_s = time.time() - t0

    verified_by_source: dict = {}
    for v in verified:
        verified_by_source.setdefault(v[3], 0)
        verified_by_source[v[3]] += 1
    winning_source = ""
    if verified:
        winning_source = max(verified, key=lambda v: v[2])[3]

    if browser_unavailable:
        js_status = "BROWSER_UNAVAILABLE"
    elif js_render_skipped_deferred:
        js_status = "JS_RENDER_REQUIRED_DEFERRED"
    elif js_renders_capped:
        js_status = "JS_RENDER_CAP_REACHED"
    elif js_render_attempts > 0 and js_render_successes == 0:
        js_status = "JS_RENDER_FAILED"
    elif js_render_successes > 0 and js_render_verified_recovered > 0:
        js_status = "JS_RENDER_VERIFIED_RECOVERED"
    elif js_render_successes > 0:
        js_status = "JS_RENDER_NO_VERIFIED"
    else:
        js_status = "JS_RENDER_DISABLED"

    return SearchResult(
        gate_open=True,
        gate_message="",
        subject_id=result.best_subject,
        best_score=result.best_score,
        threshold=threshold,
        policy=policy,
        providers_used=providers_used,
        by_provider=by_provider,
        unique_per_provider=unique_per_provider,
        overlap_by_pair=overlap_by_pair,
        raw_count=len(raw),
        duplicates_removed=duplicates_removed,
        rejected_by_policy_count=len(rejected_by_policy),
        direct_image_count=direct_image_count,
        html_page_count=html_page_count,
        images_extracted_count=images_extracted_count,
        fetch_failures=fetch_failures,
        js_render_required=js_render_required,
        js_render_attempts=js_render_attempts,
        js_render_successes=js_render_successes,
        js_render_imgs_recovered=js_render_imgs_recovered,
        js_render_faces_recovered=js_render_faces_recovered,
        js_render_verified_recovered=js_render_verified_recovered,
        js_renders_capped=js_renders_capped,
        js_render_time_s=js_render_time_s,
        js_status=js_status,
        browser_unavailable=browser_unavailable,
        js_render_skipped_deferred=js_render_skipped_deferred,
        usable_faces=usable_faces,
        verified=verified,
        rejected=rejected,
        top_scores=[round(s[2], 4) for s in top],
        verified_by_source=verified_by_source,
        winning_source=winning_source,
        image_sha256_lookup=image_sha256_lookup,
        runtime_s=runtime_s,
    )


@dataclass
class SearchResult:
    """Structured result of one search run, used by both cmd_search
    (for the diagnostic print) and cmd_full (for ranking + evidence)."""
    gate_open: bool
    gate_message: str
    subject_id: Optional[str]
    best_score: Optional[float]
    threshold: float
    policy: object
    providers_used: list
    by_provider: dict
    unique_per_provider: dict
    overlap_by_pair: dict
    raw_count: int
    duplicates_removed: int
    rejected_by_policy_count: int
    direct_image_count: int
    html_page_count: int
    images_extracted_count: int
    fetch_failures: int
    js_render_required: int
    js_render_attempts: int
    js_render_successes: int
    js_render_imgs_recovered: int
    js_render_faces_recovered: int
    js_render_verified_recovered: int
    js_renders_capped: int
    js_render_time_s: float
    js_status: str
    browser_unavailable: bool
    js_render_skipped_deferred: int
    usable_faces: int
    verified: list   # (host, url, score, source, fetch_mode, image_sha256)
    rejected: list
    top_scores: list
    verified_by_source: dict
    winning_source: str
    image_sha256_lookup: dict = field(default_factory=dict)
    runtime_s: float = 0.0


def _print_diagnostic(res: SearchResult) -> None:
    """Print the structured DIAGNOSTIC REPORT block from a SearchResult."""
    if not res.gate_open:
        _print(res.gate_message)
        _print("[veritrace] gate        : CLOSED — refusing to search (unenrolled face)")
        return
    _print("")
    _print("=" * 72)
    _print("[veritrace] DIAGNOSTIC REPORT")
    _print("=" * 72)
    if res.policy is not None:
        _print(f"  policy tier            : {res.policy.tier.value}")
    _print(f"  threshold              : {res.threshold}")
    _print(f"  providers used         : {res.providers_used or '(none available)'}")
    _print(f"  raw candidates/provider: {res.by_provider}")
    _print(f"  unique per provider    : {res.unique_per_provider}")
    if res.overlap_by_pair:
        _print(f"  overlap (canonical)    : {res.overlap_by_pair}")
    _print(f"  merged (raw)           : {res.raw_count}")
    _print(f"  duplicates removed     : {res.duplicates_removed}")
    _print(f"  rejected by policy     : {res.rejected_by_policy_count}")
    _print(f"  direct images processed: {res.direct_image_count}")
    _print(f"  HTML pages processed   : {res.html_page_count}")
    _print(f"  images extracted       : {res.images_extracted_count}")
    _print(f"  fetch failures         : {res.fetch_failures}")
    _print(f"  JS-render required     : {res.js_render_required}")
    _print(f"    JS-render status     : {res.js_status}")
    _print(f"    JS-render attempts   : {res.js_render_attempts}")
    _print(f"    JS-render successes  : {res.js_render_successes}")
    _print(f"    JS-render imgs recov : {res.js_render_imgs_recovered}")
    _print(f"    JS-render faces recov: {res.js_render_faces_recovered}")
    _print(f"    JS-render verified   : {res.js_render_verified_recovered}")
    _print(f"    JS-render cap-hit    : {res.js_renders_capped}")
    _print(f"    JS-render time       : {res.js_render_time_s:.2f}s")
    _print(f"  usable faces           : {res.usable_faces}")
    _print(f"  top similarity scores  : {res.top_scores}")
    _print(f"  verified count         : {len(res.verified)}")
    _print(f"  verified by source     : {res.verified_by_source}")
    _print(f"  winning source         : {res.winning_source or '(none)'}")
    _print(f"  rejected count         : {len(res.rejected)}")
    _print(f"  total runtime          : {res.runtime_s:.2f}s")
    _print("=" * 72)
    if res.verified:
        best = max(res.verified, key=lambda v: v[2])
        _print(f"[veritrace] result      : {len(res.verified)} verified match(es) "
               f"(best={best[0]} similarity={best[2]:.4f} source={best[3]} mode={best[4]})")
    else:
        _print(f"[veritrace] result      : 0 verified matches, {len(res.rejected)} rejected")
    _print("[veritrace] status      : ok")


def cmd_full(args: argparse.Namespace) -> int:
    """One-command judge flow: search → rank → best match → canonical
    evidence → on-chain anchor → independent re-verification.

    Intended for the live demo: hand the judges any image of a
    VeriTrace-consented subject, run ``python src/main.py full <image>``,
    and the final VERITRACE RESULT block tells the whole story.

    Returns 0 if the evidence was anchored (or written for signing) and
    local hash matches on-chain, 1 if the gate refused, 2 if no verified
    match, 3 if anchor/verify produced a tamper signal.
    """
    from src.search.ranking import rank_candidates, best_match
    from src.evidence import (
        EVIDENCE_SCHEMA_VERSION,
        MatchEvidence,
        build_match_evidence,
    )
    from src.blockchain.evidence_client import (
        anchor_canonical_evidence,
        verify_canonical_evidence,
    )

    image_path = args.image
    p = Path(image_path)
    if not p.is_file():
        _print(f"error: input image not found: {image_path}")
        return 2

    _print("=" * 72)
    _print("VERITRACE  ::  FULL EVIDENCE PIPELINE")
    _print("=" * 72)
    _print(f"[stage 1/5] consent gate + multi-provider search")
    res = _run_search(args)
    if res.gate_open:
        _print(
            f"[veritrace] consent     : GRANTED — subject={res.subject_id} "
            f"score={float(res.best_score or 0):.4f} >= {res.threshold:.4f}"
        )
    _print_diagnostic(res)
    if not res.gate_open:
        _print("[veritrace] full        : REFUSED — consent gate closed")
        return 1

    _print("")
    _print(f"[stage 2/5] candidate ranking (dedup by image SHA-256)")
    ranked = rank_candidates(
        verified=res.verified,
        threshold=res.threshold,
        image_sha256_lookup=res.image_sha256_lookup or None,
    )
    if not ranked:
        _print(f"[veritrace] ranked      : 0 candidates — nothing to rank")
    else:
        _print(f"[veritrace] ranked      : {len(ranked)} deduplicated candidate(s) "
               f"(from {len(res.verified)} raw verified)")
        for r in ranked[:5]:
            tag = "OK" if r.verified else "BELOW"
            _print(f"   #{r.rank:>2} [{tag}] sim={r.similarity:.4f} "
                   f"margin={r.confidence_margin:+.4f}  page={r.page_url}")

    best = best_match(ranked)
    if best is None:
        _print("")
        _print("=" * 72)
        _print("[veritrace] full        : NO VERIFIED MATCH")
        _print("[veritrace] status      : ok (no evidence to anchor — the search was honest)")
        return 2

    _print("")
    _print(f"[stage 3/5] canonical evidence bundle (schema v{EVIDENCE_SCHEMA_VERSION})")
    evidence = build_match_evidence(
        query_image_path=p,
        query_face_model="insightface-arcface",
        query_face_embedding_dim=512,
        page_url=best.page_url,
        image_url=best.image_url,
        candidate_image_sha256=best.image_sha256,
        similarity=best.similarity,
        threshold=best.threshold,
        fetch_mode=best.fetch_mode,
        discovery_sources=best.discovery_sources,
    )
    bundle_sha = evidence.canonical_sha256()
    _print(f"[veritrace] evidence    : schema={evidence.schema_version}")
    _print(f"[veritrace] canonical   : sha256={bundle_sha}")
    _print(f"[veritrace] bundle      : page_url       = {evidence.match_page_url}")
    _print(f"[veritrace] bundle      : image_sha256   = {evidence.match_image_sha256}")
    _print(f"[veritrace] bundle      : query_sha256   = {evidence.query_image_sha256}")
    _print(f"[veritrace] bundle      : similarity     = {evidence.similarity:.4f}  (>= {evidence.threshold:.4f})")
    _print(f"[veritrace] bundle      : fetch_mode     = {evidence.fetch_mode}")
    _print(f"[veritrace] bundle      : sources        = {','.join(evidence.discovery_sources)}")

    _print("")
    _print(f"[stage 4/5] blockchain anchor (Amoy {getattr(__import__('src.blockchain.evidence_client', fromlist=['CHAIN_DEFAULT']), 'CHAIN_DEFAULT', 80002)})")
    contract = args.contract or os.environ.get("VERIFACE_CONTRACT_ADDRESS")
    rpc = args.rpc or os.environ.get("POLYGON_AMOY_RPC_URL") or os.environ.get("POLYGON_RPC_URL")
    key = args.key or os.environ.get("PRIVATE_KEY")
    out_dir = Path(args.out_dir) if args.out_dir else Path("data/evidence")
    if not contract or not rpc:
        _print("[veritrace] anchor      : SKIPPED — no contract/rpc configured "
               "(set --contract and --rpc, or VERIFACE_CONTRACT_ADDRESS + POLYGON_AMOY_RPC_URL)")
        _print("[veritrace] full        : PARTIAL — evidence built but not anchored")
        return 0
    try:
        anchor_payload = anchor_canonical_evidence(
            evidence=evidence,
            contract=contract,
            rpc_url=rpc,
            private_key=key,
            out_dir=out_dir,
        )
    except Exception as exc:
        _print(f"[veritrace] anchor      : ERROR — {type(exc).__name__}: {exc}")
        _print("[veritrace] full        : PARTIAL — evidence bundle was built; retry with a working RPC endpoint")
        return 3
    if anchor_payload.get("_status") == "pending_signer":
        _print(f"[veritrace] anchor      : UNSIGNED REQUEST written → {anchor_payload.get('_request_file')}")
        _print(f"[veritrace] anchor      : bundle_sha={anchor_payload['bundle_sha256']}")
        _print(f"[veritrace] anchor      : calldata={anchor_payload['calldata'][:42]}…")
    elif anchor_payload.get("_status") == "already_recorded":
        _print("[veritrace] anchor      : ALREADY RECORDED — no duplicate transaction sent")
        _print(f"[veritrace] anchor      : bundle_sha={anchor_payload['bundle_sha256']}")
    else:
        tx_hash = anchor_payload.get("tx_hash")
        _print(f"[veritrace] anchor      : SUBMITTED tx={tx_hash}")
        _print(f"[veritrace] anchor      : bundle_sha={anchor_payload['bundle_sha256']}")
        _print(f"[veritrace] anchor      : subject_id_bytes32={anchor_payload['subject_id_bytes32']}")

    _print("")
    _print(f"[stage 5/5] independent re-verification (re-hash + re-check on-chain)")
    if anchor_payload.get("_status") == "pending_signer":
        _print("[veritrace] verify      : SKIPPED — nothing anchored yet (run again after signing)")
        _print("=" * 72)
        _print("[veritrace] full        : PARTIAL — evidence signed-off, awaiting on-chain submit")
        return 0
    try:
        verify_payload = verify_canonical_evidence(
            evidence=evidence,
            contract=contract,
            rpc_url=rpc,
        )
    except Exception as exc:  # pragma: no cover - RPC failures are surfaced, not raised
        _print(f"[veritrace] verify      : ERROR — {type(exc).__name__}: {exc}")
        return 3
    result_str = verify_payload.get("result", "UNKNOWN")
    is_recorded = verify_payload.get("is_recorded", False)
    onchain_sha = verify_payload.get("onchain_sha256")
    _print(f"[veritrace] verify      : local_sha256   = {verify_payload['local_sha256']}")
    _print(f"[veritrace] verify      : onchain_sha256 = {onchain_sha or '(none)'}")
    _print(f"[veritrace] verify      : recorded       = {is_recorded}")
    _print(f"[veritrace] verify      : submitter      = {verify_payload.get('submitter') or '(none)'}")
    _print(f"[veritrace] verify      : created_at     = {verify_payload.get('created_at') or '(none)'}")

    _print("")
    _print("=" * 72)
    _print("VERITRACE RESULT")
    _print("=" * 72)
    _print(f"  match rank           : #{best.rank} of {len(ranked)}")
    _print(f"  page_url             : {best.page_url}")
    _print(f"  similarity           : {best.similarity:.4f}  (threshold {best.threshold:.4f}, margin {best.confidence_margin:+.4f})")
    _print(f"  evidence schema      : {evidence.schema_version}")
    _print(f"  evidence local SHA   : {bundle_sha}")
    if onchain_sha and onchain_sha.lower() == ("0x" + bundle_sha).lower():
        _print(f"  evidence on-chain    : {onchain_sha}")
    elif onchain_sha:
        _print(f"  evidence on-chain    : {onchain_sha}  (MISMATCH)")
    else:
        _print(f"  evidence on-chain    : (unrecorded)")
    _print(f"  integrity            : {result_str}")
    _print("=" * 72)
    if result_str == "MATCH":
        _print("[veritrace] status      : INTEGRITY CONFIRMED — local hash == on-chain hash")
        return 0
    if result_str == "TAMPER DETECTED":
        _print("[veritrace] status      : TAMPER DETECTED — on-chain hash does not match local")
        return 3
    _print(f"[veritrace] status      : {result_str}")
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
    p_enroll.add_argument("--allowed-domains", default=None,
                          help="Owner-specific comma-separated search domain scope "
                               "(e.g. 'linkedin.com,wikipedia.org'). Omit for the default list.")
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
        "search", help="Consent-gated multi-provider reverse-image search (Phase 2b).",
    )
    p_search.add_argument("image", help="Face photo to search (consent-gated).")
    p_search.add_argument("--registry", type=Path, default=None,
                          help="Override consent registry path (default: data/consent_registry.json).")
    p_search.add_argument("--threshold", type=float, default=None,
                          help="Override the consent cosine threshold (default 0.6).")
    p_search.add_argument("--max-results", type=int, default=15,
                          help="Max candidates to verify (default 15).")
    p_search.add_argument("--allow-domains", default=None,
                          help="Override the public-surface allow-list for this run "
                               "(comma-separated). Highest precedence.")
    p_search.add_argument("--providers", default=None,
                          help="Comma-separated provider names to enable "
                               "(default: vision_web_detection,additional_discovery).")
    p_search.add_argument("--allow-js-render", action="store_true",
                          help="Opt-in: render JS-required pages with a headless "
                               "browser (Chrome/Chromium). Off by default.")
    p_search.add_argument("--max-js-renders", type=int, default=3,
                          help="Per-run budget for JS renders (default 3).")
    p_search.set_defaults(func=cmd_search)

    p_full = sub.add_parser(
        "full", help="One-command judge flow: search → rank → canonical "
                     "evidence → blockchain anchor → re-verify.",
    )
    p_full.add_argument("image", help="Face photo to verify end-to-end (consent-gated).")
    p_full.add_argument("--registry", type=Path, default=None,
                        help="Override consent registry path (default: data/consent_registry.json).")
    p_full.add_argument("--allow-domains", default=None,
                        help="Override the public-surface allow-list for this run "
                             "(comma-separated). Highest precedence.")
    p_full.add_argument("--providers", default=None,
                        help="Comma-separated provider names to enable "
                             "(default: vision_web_detection,additional_discovery).")
    p_full.add_argument("--max-results", type=int, default=15,
                        help="Max candidates to verify (default 15).")
    p_full.add_argument("--threshold", type=float, default=None,
                        help="Override the consent cosine threshold (default 0.6).")
    p_full.add_argument("--allow-js-render", action="store_true",
                        help="Opt-in: render JS-required pages with a headless "
                             "browser (Chrome/Chromium). Off by default.")
    p_full.add_argument("--max-js-renders", type=int, default=3,
                        help="Per-run budget for JS renders (default 3).")
    p_full.add_argument("--contract", default=os.environ.get("VERIFACE_CONTRACT_ADDRESS"),
                        help="EvidenceRegistry contract address (or VERIFACE_CONTRACT_ADDRESS).")
    p_full.add_argument("--rpc",
                        default=os.environ.get("POLYGON_AMOY_RPC_URL") or os.environ.get("POLYGON_RPC_URL"),
                        help="JSON-RPC endpoint (or POLYGON_AMOY_RPC_URL).")
    p_full.add_argument("--key", default=os.environ.get("PRIVATE_KEY"),
                        help="Owner private key (hex) (or PRIVATE_KEY). Omitting writes "
                             "an unsigned anchor request and SKIPS the verify step.")
    p_full.add_argument("--out-dir", type=Path, default=Path("data/evidence"),
                        help="Directory for the canonical bundle + anchor request "
                             "(default: data/evidence).")
    p_full.set_defaults(func=cmd_full)

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
