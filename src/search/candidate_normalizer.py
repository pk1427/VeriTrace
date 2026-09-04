"""URL canonicalization for cross-provider candidate deduplication.

Different providers surface the same image with different URL parameters
(utm tags, fbclid, gclid, m.facebook vs www.facebook, etc.). The
:func:`normalize_url` function strips tracking, lowercases the host, and
removes the default ports so the dedup step treats them as one.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# Tracking parameters that should never influence the canonical key.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "ref_src", "ref_url", "source",
    "_hsenc", "_hsmi",  # hubspot
    "igshid",  # instagram share id
    "feature",  # youtube
}


def _strip_default_port(netloc: str) -> str:
    if netloc.endswith(":80") or netloc.endswith(":443"):
        return netloc.rsplit(":", 1)[0]
    return netloc


def normalize_url(url: str) -> str:
    """Return a canonical form of ``url`` for stable deduplication.

    * Lowercases scheme + host.
    * Strips the default port (80/443).
    * Drops the fragment.
    * Drops a small set of well-known tracking params.
    * Sorts remaining query params.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    scheme = (parts.scheme or "http").lower()
    netloc = _strip_default_port((parts.netloc or "").lower())
    # Keep a path that includes a trailing slash for empty-path URLs.
    path = parts.path or ""
    # Drop empty ``?`` when no params survive.
    if parts.query:
        q = parse_qsl(parts.query, keep_blank_values=False)
        q = [(k, v) for (k, v) in q if k.lower() not in _TRACKING_PARAMS]
        q.sort()
        query = urlencode(q)
    else:
        query = ""
    return urlunsplit((scheme, netloc, path, query, ""))
