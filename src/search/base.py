"""Common candidate schema + provider interface for multi-strategy discovery.

Every discovery provider returns :class:`Candidate` objects. The search
pipeline then merges, deduplicates, applies the public-surface policy, and
finally fetches + verifies each one.

A ``Candidate`` is intentionally minimal — the only required field is
``url``. ``source`` is the provider name (e.g. ``vision_web_detection``,
``bing_visual_search``). ``candidate_type`` distinguishes a *page* (HTML)
from a *direct image*. ``discovery_method`` is a free-form string for
diagnostics (e.g. ``pages_with_matching_images``,
``visually_similar_images``). ``metadata`` is opaque per-provider extra.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional


CANDIDATE_TYPE_PAGE = "page"
CANDIDATE_TYPE_IMAGE = "image"


@dataclass
class Candidate:
    """One URL surfaced by a discovery provider.

    Attributes
    ----------
    url : str
        Public URL of either a page (HTML) or a direct image. Always set.
    source : str
        Provider name. Stable identifier (e.g. ``vision_web_detection``).
    candidate_type : str
        Either ``"page"`` or ``"image"``. Pages need HTML extraction;
        images go straight to face verification.
    discovery_method : str
        Free-form provider method (e.g. ``pages_with_matching_images``).
    metadata : dict
        Opaque per-provider extras (titles, scores, etc.). Never required.
    """

    url: str
    source: str
    candidate_type: str = CANDIDATE_TYPE_PAGE
    discovery_method: str = ""
    metadata: dict = field(default_factory=dict)

    def canonical_key(self) -> str:
        """Stable hash for deduplication.

        Uses the normalized URL so the same image referenced from
        different tracking-parameter URLs is recognised as a single
        candidate.
        """
        from src.search.candidate_normalizer import normalize_url
        return hashlib.sha1(normalize_url(self.url).encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "source": self.source,
            "candidate_type": self.candidate_type,
            "discovery_method": self.discovery_method,
            "metadata": dict(self.metadata or {}),
        }
