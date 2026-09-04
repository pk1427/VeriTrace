"""Backward-compat shim — kept so existing tests/imports keep working.

The multi-provider pipeline lives in :mod:`src.search`. The original
:func:`web_detection` / :func:`search` helpers in this module now route
to the new provider; the :func:`filter_allowed_urls` / :func:`default_allowed_domains`
helpers route to the new policy.

Behavior change (intentional): the default allow-list is no longer the
narrow wikipedia/unsplash/pexels/commons set. It is now the broad
public-surface allow-list in :mod:`src.search.policy`. Tests and
callers that want the *old* narrow set can pass it explicitly via
``allowed_domains=[...]``.
"""
from __future__ import annotations

import os
import warnings
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from src.search import vision_web_detection, policy as _policy


# --------------------------------------------------------------------------- #
# Allow-list helpers (compat)
# --------------------------------------------------------------------------- #


def default_allowed_domains() -> list:
    """Default allow-list.

    The previous narrow list (wikipedia, unsplash, …) is replaced by the
    broad public-surface allow-list in :mod:`src.search.policy` so the
    pipeline does not silently exclude legitimate public results.

    An env override is still honored (legacy compat): setting
    ``VERIFACE_ALLOWED_DOMAINS=a.com,b.com`` will *replace* the broad
    default with that explicit list (forces EXPLICIT-style filtering).
    """
    env = os.environ.get("VERIFACE_ALLOWED_DOMAINS")
    if env:
        return [d.strip().lower() for d in env.split(",") if d.strip()]
    return _policy.default_broad_domains()


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _host_allowed(host: str, allowed: list) -> bool:
    host = host.lower().lstrip("www.")
    return any(host == d or host.endswith("." + d) for d in allowed)


def filter_allowed_urls(matches, allowed_domains) -> list:
    """Compat shim — used by tests."""
    allowed = [d.lower() for d in (allowed_domains or []) if d]
    if not allowed:
        # Empty allow-list => old behavior was "drop everything", but
        # we want the new behavior: nothing rejected by the shim. The
        # new pipeline calls :func:`src.search.filter_candidates` instead
        # and that path uses the broad default.
        return list(matches)
    return [m for m in matches if m.get("url") and _host_allowed(_host(m["url"]), allowed)]


# --------------------------------------------------------------------------- #
# Vision provider (compat)
# --------------------------------------------------------------------------- #


def web_detection(image_path, max_results: int = 10) -> list:
    """Call Google Cloud Vision ``WEB_DETECTION`` and return candidate dicts.

    Returns dicts with the legacy ``url/host/domain`` keys so existing
    call sites keep working. Internally it uses the new
    :func:`src.search.vision_web_detection.discover`.
    """
    out: list = []
    for c in vision_web_detection.discover(image_path, max_results=max_results):
        h = _host(c.url)
        out.append({"url": c.url, "host": h, "domain": h,
                    "candidate_type": c.candidate_type,
                    "source": c.source, "discovery_method": c.discovery_method})
    return out


def search(image_path, allowed_domains=None, max_results: int = 10) -> list:
    """Consent-gated reverse-image search (compat shim)."""
    warnings.warn(
        "src.search.web.search is the legacy single-provider shim. "
        "Use src.search.discover_candidates + src.search.filter_candidates "
        "for the multi-provider pipeline.",
        DeprecationWarning,
        stacklevel=2,
    )
    matches = web_detection(image_path, max_results=max_results)
    return filter_allowed_urls(matches, allowed_domains or default_allowed_domains())
