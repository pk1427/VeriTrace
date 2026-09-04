"""VeriTrace search package — multi-provider candidate discovery.

Public API::

    from src.search import (
        Candidate,
        ActivePolicy,
        resolve_policy,
        host_allowed,
        filter_candidates,
        discover_candidates,   # multi-provider merge
        fetch_candidate,
        extract_image_urls_from_html,
    )

The pipeline is::

    providers (vision_web_detection, additional_discovery, …)
        -> list[Candidate]                # raw, per-provider
        -> merge + dedup (by canonical URL)
        -> apply public-surface policy    # never silent
        -> fetch (direct image or HTML-extracted)
        -> InsightFace verification + rank
        -> structured diagnostic report
"""
from __future__ import annotations
from typing import List, Optional

from src.search.base import Candidate, CANDIDATE_TYPE_IMAGE, CANDIDATE_TYPE_PAGE  # noqa: F401
from src.search.candidate_normalizer import normalize_url  # noqa: F401
from src.search.policy import (  # noqa: F401
    ActivePolicy,
    PolicyTier,
    default_broad_domains,
    filter_candidates,
    host_allowed,
    resolve_policy,
)
from src.search.fetcher import (  # noqa: F401
    extract_image_urls_from_html,
    fetch_candidate,
    JS_RENDER_REQUIRED,
)
from src.search import vision_web_detection, additional_discovery_provider  # noqa: F401
from src.search.reverse_image_provider import (  # noqa: F401
    discover as _reverse_image_discover,
    is_available as _reverse_image_available,
    PROVIDER_NAME as REVERSE_IMAGE_PROVIDER_NAME,
)
from src.search.tineye_provider import (  # noqa: F401
    discover as _tineye_discover,
    is_available as _tineye_available,
    PROVIDER_NAME as TINEYE_PROVIDER_NAME,
)
from src.search.web import default_allowed_domains, filter_allowed_urls  # noqa: F401


__all__ = [
    "ActivePolicy",
    "CANDIDATE_TYPE_IMAGE",
    "CANDIDATE_TYPE_PAGE",
    "Candidate",
    "JS_RENDER_REQUIRED",
    "PolicyTier",
    "REVERSE_IMAGE_PROVIDER_NAME",
    "TINEYE_PROVIDER_NAME",
    "VISION_WEB_DETECTION_PROVIDER_NAME",
    "additional_discovery_provider",
    "default_allowed_domains",
    "default_broad_domains",
    "discover_candidates",
    "extract_image_urls_from_html",
    "fetch_candidate",
    "filter_allowed_urls",
    "host_allowed",
    "normalize_url",
    "resolve_policy",
    "reverse_image_provider",
    "tineye_provider",
    "vision_web_detection",
]


# Stable, public provider names (re-exported under the schema names used
# by the diagnostic / tests).
VISION_WEB_DETECTION_PROVIDER_NAME = "vision_web_detection"


# --------------------------------------------------------------------------- #
# Multi-provider merge
# --------------------------------------------------------------------------- #


def discover_candidates(
    image_path: str,
    enabled: Optional[List[str]] = None,
    max_results: int = 30,
) -> List[Candidate]:
    """Run all enabled providers and return the merged raw candidate list.

    Default provider set:
        1. ``vision_web_detection``   — Google Cloud Vision.
        2. ``reverse_image_provider`` — Yandex public reverse-image
           search via anonymous public-host upload (independent of Vision).
        3. ``tineye_provider``        — TinEye public reverse-image API
           (independent of both Vision and Yandex).
        4. ``additional_discovery``   — local perceptual-hash index
           and sidecar URL hints (input-driven, requires user to place
           files; never hardcodes any person or URL).

    Provider failures are *not* fatal — they are caught per-provider and
    a stub ``__provider_error__`` candidate is emitted so the diagnostic
    can still report the failure with attribution.
    """
    from src.search import (
        vision_web_detection,
        reverse_image_provider,
        tineye_provider,
        additional_discovery_provider,
    )

    all_providers = [
        ("vision_web_detection", vision_web_detection),
        ("reverse_image_provider", reverse_image_provider),
        ("tineye_provider", tineye_provider),
        ("additional_discovery", additional_discovery_provider),
    ]
    if enabled:
        wanted = {n for n in enabled}
        all_providers = [(n, m) for (n, m) in all_providers if n in wanted]

    out: List[Candidate] = []
    for name, mod in all_providers:
        if not mod.is_available():
            continue
        try:
            got = mod.discover(image_path, max_results=max_results) or []
        except Exception as exc:  # noqa: BLE001
            out.append(
                Candidate(
                    url=f"__provider_error__::{name}::{type(exc).__name__}",
                    source=name,
                    candidate_type=CANDIDATE_TYPE_PAGE,
                    discovery_method="provider_error",
                    metadata={"error": str(exc)},
                )
            )
            continue
        out.extend(got)
    return out
