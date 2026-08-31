"""Phase 2b: consent-gated reverse-image search via Google Cloud Vision.

Authentication uses Application Default Credentials: set
``GOOGLE_APPLICATION_CREDENTIALS`` in ``.env`` to the service-account JSON key
file (e.g. ``veritrace-507220-*.json``). The search scope is constrained to
``VERIFACE_ALLOWED_DOMAINS`` — only results from approved hostnames are
returned.

The caller MUST have passed the consent gate (Phase 2) for the input face
before calling :func:`search` — see ``src/main.py`` ``cmd_search`` for the CLI
enforcement.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

ALLOWED_DOMAINS_DEFAULT = "wikipedia.org,commons.wikimedia.org,unsplash.com,pexels.com"


def default_allowed_domains() -> list:
    env = os.environ.get("VERIFACE_ALLOWED_DOMAINS", ALLOWED_DOMAINS_DEFAULT)
    return [d.strip().lower() for d in env.split(",") if d.strip()]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _host_allowed(host: str, allowed: list) -> bool:
    host = host.lower().lstrip("www.")
    return any(host == d or host.endswith("." + d) for d in allowed)


def filter_allowed_urls(matches, allowed_domains) -> list:
    """Keep only matches whose URL host is covered by the allow-list."""
    allowed = [d.lower() for d in (allowed_domains or [])]
    return [m for m in matches if m.get("url") and _host_allowed(_host(m["url"]), allowed)]


def web_detection(image_path, max_results: int = 10) -> list:
    """Call Google Cloud Vision ``WEB_DETECTION`` and return candidate URLs."""
    try:
        from google.cloud import vision
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-vision is not installed (pip install google-cloud-vision), "
            "or GOOGLE_APPLICATION_CREDENTIALS is not set in .env."
        ) from exc

    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=Path(image_path).read_bytes())
    res = client.web_detection(image=image, max_results=max_results)
    wd = res.web_detection

    matches: list = []
    for page in wd.pages_with_matching_images or []:
        matches.append({"url": page.url, "host": _host(page.url), "domain": _host(page.url)})
    for sim in getattr(wd, "visually_similar_images", None) or []:
        url = getattr(sim, "url", "") or ""
        if url:
            matches.append({"url": url, "host": _host(url), "domain": _host(url)})
    return matches


def search(image_path, allowed_domains=None, max_results: int = 10) -> list:
    """Consent-gated reverse-image search: detect → Vision web detection → allow-list filter."""
    matches = web_detection(image_path, max_results=max_results)
    return filter_allowed_urls(matches, allowed_domains or default_allowed_domains())
