"""One-off diagnostic: run the input image through the pipeline and log every stage.

Does not modify the codebase. Writes a JSON report to /tmp/veritrace_diag.json
and a human-readable report to stdout.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

# Ensure repo root on path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from dotenv import load_dotenv

load_dotenv()

INPUT = "test/samples/Teammate5.png"
ALLOW = [
    "media.licdn.com",
    "avatars.githubusercontent.com",
    "pbs.twimg.com",
    "lookaside.fbsbx.com",
    "lookaside.instagram.com",
    "tiktok.com",
    "yt3.googleusercontent.com",
    "pk1427.github.io",
    "akave.com",
    "cdn.prod.website-files.com",
    "facebook.com",
    "reddit.com",
    "espncricinfo.com",
    "cricbuzz.com",
    "crictracker.com",
    "sportskeeda.com",
    "hindustantimes.com",
    "wikipedia.org",
    "commons.wikimedia.org",
    "unsplash.com",
    "pexels.com",
]

REPORT: Dict[str, Any] = {
    "input": INPUT,
    "stages": {},
}


# -------------------------- helpers --------------------------
def _hr(s: str) -> None:
    print("\n" + "=" * 80 + "\n" + s + "\n" + "=" * 80)


# -------------------------- STAGE 1: input processing --------------------------
def stage1() -> Dict[str, Any]:
    _hr("STAGE 1 — Input processing")
    from src.face import detector, encoder, _engine
    backend = _engine.backend_name()
    out: Dict[str, Any] = {"backend": backend, "model_pack": os.environ.get("VERIFACE_MODEL_PACK", "buffalo_sc")}
    faces, img = _engine.analyze(INPUT)
    h, w = img.shape[:2]
    out["image_size"] = {"width": int(w), "height": int(h)}
    out["n_faces"] = len(faces)
    out["faces"] = []
    for i, f in enumerate(faces):
        out["faces"].append({
            "i": i,
            "bbox": list(f.bbox),
            "area": f.area,
            "det_score": round(f.det_score, 4),
            "embedding_dim": int(f.embedding.size) if f.embedding is not None else None,
            "embedding_norm": round(float((f.embedding ** 2).sum() ** 0.5), 4) if f.embedding is not None else None,
            "selected": i == 0,  # default: largest
        })
    if not faces:
        out["status"] = "NO_FACE_DETECTED"
    else:
        out["status"] = "OK"
        # Produce a fingerprinting-grade hash for comparison later
        import hashlib
        with open(INPUT, "rb") as fh:
            out["image_sha256"] = hashlib.sha256(fh.read()).hexdigest()
    return out


# -------------------------- STAGE 2: search/discovery --------------------------
def stage2() -> Dict[str, Any]:
    _hr("STAGE 2 — Search / candidate discovery")
    from src.search import web as search_web
    try:
        from google.cloud import vision_v1
    except ImportError as e:
        return {"status": "VISION_NOT_INSTALLED", "error": str(e)}

    # Use the low-level client to also surface pages_with_matching_images + web_entities
    client = vision_v1.ImageAnnotatorClient()
    with open(INPUT, "rb") as f:
        content = f.read()
    img = vision_v1.Image(content=content)
    ft = vision_v1.Feature(type=vision_v1.Feature.Type.WEB_DETECTION, max_results=30)
    req = vision_v1.AnnotateImageRequest(image=img, features=[ft])
    resp = client.annotate_image(request=req)
    wd = resp.web_detection

    raw: List[Dict[str, Any]] = []
    for p in wd.pages_with_matching_images or []:
        u = p.url
        raw.append({
            "url": u, "host": (urlparse(u).hostname or "").lower(),
            "source_field": "pages_with_matching_images",
            "score": round(p.score or 0.0, 4),
        })
    for s in getattr(wd, "visually_similar_images", None) or []:
        u = getattr(s, "url", "") or ""
        if u:
            raw.append({
                "url": u, "host": (urlparse(u).hostname or "").lower(),
                "source_field": "visually_similar_images",
                "score": round(s.score or 0.0, 4),
            })

    # Tag in-scope (matches any of the allowlist)
    def _host_allowed(h: str) -> bool:
        h = (h or "").lower().lstrip("www.")
        for d in ALLOW:
            if h == d or h.endswith("." + d):
                return True
        return False

    for r in raw:
        r["in_scope"] = _host_allowed(r["host"])

    out: Dict[str, Any] = {
        "status": "OK",
        "provider": "google-cloud-vision WEB_DETECTION",
        "max_results_requested": 30,
        "pages_with_matching_images_count": len(wd.pages_with_matching_images or []),
        "visually_similar_images_count": len(getattr(wd, "visually_similar_images", None) or []),
        "total_candidates": len(raw),
        "candidates": raw,
        "domain_breakdown": {},
    }
    from collections import Counter
    c = Counter(r["host"] for r in raw)
    out["domain_breakdown"] = dict(c.most_common())
    out["n_in_scope"] = sum(1 for r in raw if r["in_scope"])
    out["allowed_domains_used"] = ALLOW
    return out


# -------------------------- STAGE 3: candidate extraction --------------------------
def stage3(stage2_out: Dict[str, Any]) -> Dict[str, Any]:
    _hr("STAGE 3 — Candidate extraction (fetch + HTML image extraction)")
    from src.search.fetcher import fetch_candidate, IMAGE_CONTENT_TYPES, _FirstImg
    import urllib.request

    candidates = stage2_out["candidates"]
    in_scope = [c for c in candidates if c["in_scope"]]

    detail: List[Dict[str, Any]] = []
    for c in in_scope:
        url = c["url"]
        # Head request to capture the initial HTTP status + content type
        head_status: Optional[int] = None
        head_ct: Optional[str] = None
        try:
            req = urllib.request.Request(url, method="HEAD", headers={
                "User-Agent": "veritrace/1.0 (+https://github.com/pk1427/VeriTrace)",
                "Accept": "image/*,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                head_status = resp.status
                head_ct = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
        except Exception as e:  # noqa: BLE001
            head_status = f"HEAD failed: {type(e).__name__}: {e}"

        # Now actually fetch + parse (this is what the pipeline does)
        path, content_type, reason = fetch_candidate(url)
        kind: str
        if path is None:
            kind = "FETCH_FAILED"
        elif content_type in IMAGE_CONTENT_TYPES:
            kind = "DIRECT_IMAGE"
        elif content_type.startswith("text/html"):
            # Try to also enumerate meta/img manually for the report
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "veritrace/1.0 (+https://github.com/pk1427/VeriTrace)"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = resp.read(2_000_000).decode("utf-8", "ignore")
                p = _FirstImg(); p.feed(body)
                meta_og = bool(p._og_image)
                meta_tw = bool(p._twitter_image)
                meta_link = bool(p._link_image_src)
                meta_img = bool(p._img_src)
                n_imgs_total = body.count("<img")
            except Exception as e:  # noqa: BLE001
                meta_og = meta_tw = meta_link = meta_img = False
                n_imgs_total = -1
                detail.append({"error": f"manual parse failed: {e}"})
            else:
                # If the fetch_candidate already produced an image (via og:image etc.), this
                # is a successful indirect extraction. Otherwise it's a parse failure.
                if reason == "ok":
                    kind = "HTML_PAGE_OK_VIA_META_OR_IMG"
                else:
                    kind = "HTML_PAGE_PARSE_FAILED"
            detail.append({
                "og_image": meta_og, "twitter_image": meta_tw,
                "link_image_src": meta_link, "img_src_present": meta_img,
                "img_tag_count": n_imgs_total,
            })
        else:
            kind = "NON_IMAGE_CONTENT"

        final_image_url: Optional[str] = None
        if path:
            final_image_url = url  # direct image
            if kind.startswith("HTML_PAGE_OK"):
                final_image_url = urljoin(url, (
                    (lambda p: p._og_image or p._twitter_image or p._link_image_src or p._img_src)(_FirstImg())
                )) if False else final_image_url

        rec: Dict[str, Any] = {
            "url": url, "host": c["host"], "in_scope": True,
            "head_status": head_status, "head_content_type": head_ct,
            "kind": kind, "reason": reason, "content_type": content_type,
        }
        if detail and isinstance(detail[-1], dict) and "og_image" in detail[-1]:
            rec.update(detail.pop())
        detail.append(rec)

    return {
        "in_scope_count": len(in_scope),
        "details": detail,
    }


# -------------------------- STAGE 4: face processing on candidates --------------------------
def stage4(stage3_out: Dict[str, Any]) -> Dict[str, Any]:
    _hr("STAGE 4 — Face processing on candidate images")
    from src.search.fetcher import fetch_candidate
    from src.face import _engine

    faces_input, _ = _engine.analyze(INPUT)
    if not faces_input:
        return {"status": "NO_INPUT_FACE"}
    query_emb = faces_input[0].embedding

    # Re-fetch each in-scope candidate directly to get a fresh temp file
    in_scope = [d for d in stage3_out["details"] if d["in_scope"]]
    out: List[Dict[str, Any]] = []
    for d in in_scope:
        path, content_type, reason = fetch_candidate(d["url"])
        if path is None:
            out.append({"url": d["url"], "download_ok": False, "reason": reason})
            continue
        try:
            faces, _ = _engine.analyze(path)
        except Exception as e:  # noqa: BLE001
            out.append({"url": d["url"], "download_ok": True, "face_analysis_error": str(e)})
            continue
        import numpy as np
        rec: Dict[str, Any] = {
            "url": d["url"], "download_ok": True, "path": path,
            "content_type": content_type, "n_faces_detected": len(faces),
        }
        if not faces:
            rec["status"] = "REJECTED_NO_FACE"
        else:
            sims = []
            for f in faces:
                if f.embedding is None:
                    continue
                cos = float(np.dot(query_emb, f.embedding) / (
                    (np.linalg.norm(query_emb) * np.linalg.norm(f.embedding)) + 1e-9
                ))
                sims.append({"bbox": list(f.bbox), "similarity": round(cos, 4)})
            sims.sort(key=lambda x: -x["similarity"])
            rec["similarities"] = sims
            best = sims[0]["similarity"] if sims else None
            rec["best_similarity"] = best
            rec["status"] = (
                "CANDIDATE" if best is not None and best >= 0.6 else "BELOW_THRESHOLD"
            ) if best is not None else "NO_EMBEDDING"
        out.append(rec)
    return {"items": out}


# -------------------------- STAGE 5: similarity + classification --------------------------
def stage5(stage4_out: Dict[str, Any]) -> Dict[str, Any]:
    _hr("STAGE 5 — Similarity analysis + bottleneck classification")
    items = stage4_out["items"]
    candidates = [it for it in items if it.get("status") == "CANDIDATE"]
    others = [it for it in items if it.get("status") != "CANDIDATE"]
    candidates.sort(key=lambda x: -x.get("best_similarity", -1))
    top = candidates[:20]

    # bottleneck classification
    classification: List[str] = []
    if stage2_out := REPORT["stages"].get("stage2", {}):
        if stage2_out.get("n_in_scope", 0) == 0:
            classification.append("A.DISCOVERY: 0 in-scope candidates returned by Vision (and no allowlist match)")
        else:
            classification.append(f"A.DISCOVERY: {stage2_out['n_in_scope']} in-scope candidates (sufficient)")
    if stage3_out := REPORT["stages"].get("stage3", {}):
        n_downloaded = sum(1 for d in stage3_out["details"] if d["kind"] != "FETCH_FAILED")
        n_failed = sum(1 for d in stage3_out["details"] if d["kind"] == "FETCH_FAILED")
        n_html_parse = sum(1 for d in stage3_out["details"] if d["kind"] == "HTML_PAGE_PARSE_FAILED")
        if n_failed:
            classification.append(f"B.FETCHING: {n_failed} in-scope URLs failed to fetch")
        if n_html_parse:
            classification.append(f"C.HTML_EXTRACTION: {n_html_parse} HTML pages had no extractable image (likely JS-rendered shims)")
        classification.append(f"B+C: {n_downloaded} in-scope URLs yielded a usable image")
    stage4_items = stage4_out.get("items", [])
    n_no_face = sum(1 for it in stage4_items if it.get("status") == "REJECTED_NO_FACE")
    if n_no_face:
        classification.append(f"D.FACE_DETECTION: {n_no_face} downloaded images had no detectable face")
    n_below = sum(1 for it in stage4_items if it.get("status") == "BELOW_THRESHOLD")
    if n_below:
        classification.append(f"F.THRESHOLD: {n_below} candidates had face(s) but similarity < 0.6 (best=%.4f)" % max(
            (it.get("best_similarity") or 0) for it in stage4_items
        ))
    if not candidates:
        classification.append("G.NO_VERIFIED: pipeline ran end-to-end with no candidate passing threshold; this is the OBSERVED outcome")

    return {
        "n_candidates_passing_threshold": len(candidates),
        "n_below_threshold": n_below,
        "n_no_face_in_image": n_no_face,
        "top_20_ranked": top,
        "bottleneck_classification": classification,
        "threshold_used": 0.6,
        "embedding_dim": int(REPORT["stages"]["stage1"]["faces"][0]["embedding_dim"]) if REPORT["stages"].get("stage1", {}).get("faces") else None,
    }


# -------------------------- run all stages --------------------------
def main() -> None:
    try:
        REPORT["stages"]["stage1"] = stage1()
        print(json.dumps(REPORT["stages"]["stage1"], indent=2))
    except Exception as e:  # noqa: BLE001
        REPORT["stages"]["stage1"] = {"status": "ERROR", "error": str(e), "tb": traceback.format_exc()}
        print(REPORT["stages"]["stage1"]["tb"])

    try:
        REPORT["stages"]["stage2"] = stage2()
        # Print a compact summary
        s = REPORT["stages"]["stage2"]
        print(f"\ntotal candidates: {s['total_candidates']}, in_scope: {s['n_in_scope']}")
        for c in s["candidates"][:20]:
            print(f"  [{c['source_field']}] score={c['score']:.4f} in_scope={c['in_scope']}  {c['host']}  {c['url'][:120]}")
    except Exception as e:  # noqa: BLE001
        REPORT["stages"]["stage2"] = {"status": "ERROR", "error": str(e), "tb": traceback.format_exc()}
        print(REPORT["stages"]["stage2"]["tb"])

    try:
        REPORT["stages"]["stage3"] = stage3(REPORT["stages"]["stage2"])
        for d in REPORT["stages"]["stage3"]["details"]:
            print(f"  {d['url'][:80]}  head={d.get('head_status')}  head_ct={d.get('head_content_type')}  kind={d['kind']}  reason={d.get('reason')}")
    except Exception as e:  # noqa: BLE001
        REPORT["stages"]["stage3"] = {"status": "ERROR", "error": str(e), "tb": traceback.format_exc()}
        print(REPORT["stages"]["stage3"]["tb"])

    try:
        REPORT["stages"]["stage4"] = stage4(REPORT["stages"]["stage3"])
        for it in REPORT["stages"]["stage4"]["items"]:
            print(f"  {it.get('url','')[:80]}  status={it.get('status')}  best_sim={it.get('best_similarity')}  n_faces={it.get('n_faces_detected')}")
    except Exception as e:  # noqa: BLE001
        REPORT["stages"]["stage4"] = {"status": "ERROR", "error": str(e), "tb": traceback.format_exc()}
        print(REPORT["stages"]["stage4"]["tb"])

    try:
        REPORT["stages"]["stage5"] = stage5(REPORT["stages"]["stage4"])
        s5 = REPORT["stages"]["stage5"]
        print(f"\nthreshold used: {s5['threshold_used']}")
        print(f"candidates ≥ threshold: {s5['n_candidates_passing_threshold']}")
        print("bottleneck classification:")
        for c in s5["bottleneck_classification"]:
            print(f"  - {c}")
    except Exception as e:  # noqa: BLE001
        REPORT["stages"]["stage5"] = {"status": "ERROR", "error": str(e), "tb": traceback.format_exc()}
        print(REPORT["stages"]["stage5"]["tb"])

    with open("/tmp/veritrace_diag.json", "w") as f:
        json.dump(REPORT, f, indent=2, default=str)
    print(f"\nfull report: /tmp/veritrace_diag.json")


if __name__ == "__main__":
    main()
