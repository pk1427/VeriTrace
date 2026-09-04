"""Google Cloud Vision ``WEB_DETECTION`` provider.

Returns the same candidates the Phase 2b ``web.py`` used to return, but
re-emitted as :class:`Candidate` objects in the common schema. The
Vision response includes:

* ``pages_with_matching_images`` — public pages where this image (or a
  visually-similar one) appears.  These are *pages* (HTML).
* ``visually_similar_images``     — direct image URLs that look like this
  face.  These are *images* (Content-Type will normally be ``image/*``).
* ``full_matching_images``        — exact (cropped/scaled) duplicates.
  These are *images* (highest confidence).

Authentication is via ``GOOGLE_APPLICATION_CREDENTIALS`` (set in ``.env``).
If the dependency or credentials are missing, this provider is *not* fatal
— the search pipeline will simply log it as unavailable and continue with
the other providers.
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import urlparse

from src.search.base import Candidate, CANDIDATE_TYPE_IMAGE, CANDIDATE_TYPE_PAGE

log = logging.getLogger(__name__)

PROVIDER_NAME = "vision_web_detection"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_available() -> bool:
    """Return True if this provider is usable in the current environment."""
    try:
        import google.cloud.vision  # noqa: F401
        return True
    except ImportError:
        return False


def discover(image_path: str, max_results: int = 20) -> List[Candidate]:
    """Run Vision ``WEB_DETECTION`` and return :class:`Candidate` objects.

    Raises :class:`RuntimeError` if the dependency is missing, the
    credentials are not set, or the API call itself fails. The caller is
    expected to treat that as "provider unavailable" for the run.
    """
    from pathlib import Path
    try:
        from google.cloud import vision
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-vision is not installed (pip install google-cloud-vision)."
        ) from exc

    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=Path(image_path).read_bytes())
    res = client.web_detection(image=image, max_results=max_results)
    wd = res.web_detection

    out: List[Candidate] = []
    for page in wd.pages_with_matching_images or []:
        url = getattr(page, "url", None)
        if not url:
            continue
        out.append(
            Candidate(
                url=url,
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_PAGE,
                discovery_method="pages_with_matching_images",
                metadata={"host": _host(url)},
            )
        )
    for sim in getattr(wd, "visually_similar_images", None) or []:
        url = getattr(sim, "url", None)
        if not url:
            continue
        out.append(
            Candidate(
                url=url,
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_IMAGE,
                discovery_method="visually_similar_images",
                metadata={"host": _host(url)},
            )
        )
    for full in getattr(wd, "full_matching_images", None) or []:
        url = getattr(full, "url", None)
        if not url:
            continue
        out.append(
            Candidate(
                url=url,
                source=PROVIDER_NAME,
                candidate_type=CANDIDATE_TYPE_IMAGE,
                discovery_method="full_matching_images",
                metadata={"host": _host(url)},
            )
        )
    return out
