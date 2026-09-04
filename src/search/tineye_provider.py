"""TinEye reverse-image search provider — third independent visual-search path.

Genuinely independent of both Google Vision and Yandex:

* Different vendor (TinEye's own CBIR index).
* Accepts the **input image** as bytes; the search request is derived
  from the input image's content, not from any preconfigured list.
* No local identity database, no prebuilt index, no planted pages.

Pipeline
--------
1. Upload the input image to a temporary anonymous public host
   (catbox.moe / uguu.se fallback, shared with the Yandex provider).
2. Call TinEye's public ``/api/v1/result_json/`` endpoint with the
   public URL.
3. Parse the JSON response for matches; emit each match's
   ``back_links`` (the page URLs that contain the image) and
   ``domain`` as :class:`Candidate` objects.

Failure modes
-------------
* Upload fails (no host works) -> emit a ``__provider_error__`` stub.
* TinEye returns no matches -> emit a stub.
* TinEye returns captcha / block -> emit a stub.

TinEye is well-known for being **stricter about duplicate-only
matches** — the same image uploaded to many sites returns many
back_links. This provider therefore tends to surface exact or
near-duplicate matches (page URLs that host the same photo), which
is complementary to Yandex's "visually similar" emphasis.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional
from urllib.parse import urlparse

from src.search.base import Candidate, CANDIDATE_TYPE_IMAGE, CANDIDATE_TYPE_PAGE
from src.search.reverse_image_provider import _upload_to_public_host

log = logging.getLogger(__name__)

PROVIDER_NAME = "tineye_provider"
TINEYE_ENDPOINT = "https://tineye.com/api/v1/result_json/"

# TinEye's index is smaller than Vision's — cap results accordingly.
DEFAULT_MAX_RESULTS = 25
TINEYE_TIMEOUT = 25.0
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def is_available() -> bool:
    """True unless explicitly disabled via env."""
    flag = os.environ.get("VERIFACE_TINEYE_ENABLED", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _call_tineye(public_url: str) -> Optional[dict]:
    """Call TinEye's public result_json endpoint; return the parsed body
    or None on any failure."""
    target = f"{TINEYE_ENDPOINT}?url={urllib.parse.quote(public_url, safe='')}"
    try:
        req = urllib.request.Request(
            target,
            headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TINEYE_TIMEOUT) as resp:
            raw = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("tineye: request failed: %s: %s", type(exc).__name__, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("tineye: unexpected error: %s: %s", type(exc).__name__, exc)
        return None
    try:
        return json.loads(raw.decode("utf-8", "ignore"))
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("tineye: bad JSON: %s", exc)
        return None


def _emit_candidates(payload: dict, public_url: str, max_results: int) -> List[Candidate]:
    """Convert a TinEye result_json body to a list of :class:`Candidate`."""
    out: List[Candidate] = []
    matches = payload.get("matches") or []
    if not isinstance(matches, list):
        return out

    seen_keys: set = set()
    for m in matches:
        if not isinstance(m, dict):
            continue
        # TinEye "matches" each contain a list of back_links — pages where
        # this image was found. We emit one candidate per (domain, url).
        back_links = m.get("back_links") or []
        if not isinstance(back_links, list):
            continue
        image_url = m.get("image_url") or ""
        for link in back_links:
            if not isinstance(link, dict):
                continue
            url = link.get("url") or ""
            domain = link.get("domain") or ""
            if not url or not (url.startswith("http://") or url.startswith("https://")):
                continue
            key = f"page::{url}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(
                Candidate(
                    url=url,
                    source=PROVIDER_NAME,
                    candidate_type=CANDIDATE_TYPE_PAGE,
                    discovery_method="reverse_image_match",
                    metadata={
                        "public_url_input": public_url,
                        "host": domain or (urlparse(url).hostname or "").lower(),
                        "tineye_score": m.get("score"),
                        "tineye_image_url": image_url,
                    },
                )
            )
            if len(out) >= max_results:
                return out
    return out


def discover(
    image_path: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    index_path: Optional[str] = None,  # unused — interface compat
) -> List[Candidate]:
    """Run TinEye reverse-image search. Never raises."""
    out: List[Candidate] = []
    if not is_available():
        return out

    public_url = _upload_to_public_host(image_path)
    if not public_url:
        out.append(
            Candidate(
                url="__provider_error__::" + PROVIDER_NAME + "::upload_failed",
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_PAGE,
                discovery_method="reverse_image_match",
                metadata={"error": "no public host accepted the upload"},
            )
        )
        return out

    payload = _call_tineye(public_url)
    if not payload:
        out.append(
            Candidate(
                url="__provider_error__::" + PROVIDER_NAME + "::tineye_unreachable",
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_PAGE,
                discovery_method="reverse_image_match",
                metadata={"public_url": public_url},
            )
        )
        return out

    n_matches = int(payload.get("num_matches", 0) or 0)
    out.extend(_emit_candidates(payload, public_url, max_results))

    if n_matches == 0 or not out:
        out.append(
            Candidate(
                url="__provider_error__::" + PROVIDER_NAME + "::no_items",
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_PAGE,
                discovery_method="reverse_image_match",
                metadata={
                    "info": "tineye returned 0 matches",
                    "public_url": public_url,
                    "num_matches": n_matches,
                },
            )
        )
    return out[:max_results]
