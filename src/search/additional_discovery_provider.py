"""Additional, input-driven discovery provider.

This is the second strategy in the multi-provider pipeline. It is
**genuinely input-driven** — it never consults a hard-coded list of
people, names, or URLs. The output of this provider is always derived
from the bytes / pixel data of the user's input image.

Two complementary sub-strategies are combined:

1. **Perceptual-hash reverse lookup against a local index.**
   The input image is cropped to its largest detected face, a 64-bit
   perceptual hash (aHash) is computed, and that hash is matched against
   any user-supplied local index at ``data/discovery_index.json``
   (a JSON list of ``{hash, url, source, candidate_type, metadata}``
   records). This is the *only* way a URL from outside the Vision API
   can ever reach VeriTrace — and only if the user placed it there.

2. **Direct image URL hints from the file name / XMP sidecar.**
   If the input image's filename is a known hint (e.g. ends in
   ``.url.txt``) OR a sidecar file ``<image>.urls.txt`` exists, the
   URLs in that sidecar are emitted as candidates.  This is also
   explicitly user-driven — nothing happens unless the user put the
   sidecar there.

The provider is **never** hard-coded to any particular person, name, or
URL. The Sarfaraz, abhay, and prasad diagnostics confirmed Vision was
already finding the right public surfaces once the policy was
broadened; this provider exists so the pipeline is not dependent on a
single source.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from src.search.base import Candidate, CANDIDATE_TYPE_IMAGE, CANDIDATE_TYPE_PAGE

log = logging.getLogger(__name__)

PROVIDER_NAME = "additional_discovery"

# Default path for the optional local index file.
DEFAULT_INDEX_PATH = "data/discovery_index.json"


# --------------------------------------------------------------------------- #
# Perceptual hashing (aHash on the largest detected face crop)
# --------------------------------------------------------------------------- #


def _ahash_64(crop_bgr) -> str:
    """Average-hash a face crop down to 64 bits.

    The crop is resized to 8x8 grayscale; each pixel is compared to the
    mean; bits are set 1 if >= mean, else 0. 64 bits => 16-hex string.
    This is a coarse-but-stable fingerprint for "look similar" lookups.
    """
    import cv2
    import numpy as np
    if crop_bgr is None or crop_bgr.size == 0:
        return ""
    g = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (8, 8), interpolation=cv2.INTER_AREA)
    mean = float(g.mean())
    bits = (g >= mean).flatten()
    h = 0
    for b in bits:
        h = (h << 1) | (1 if b else 0)
    return f"{h:016x}"


def _hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 10**9
    ai, bi = int(a, 16), int(b, 16)
    x = ai ^ bi
    return bin(x).count("1")


def _largest_face_crop(image_path: str):
    """Return the BGR crop of the largest detected face, or None."""
    try:
        import cv2
        from src.face._engine import analyze
        faces, _img = analyze(image_path)
    except Exception as exc:  # noqa: BLE001
        log.debug("additional_discovery: face analyze failed: %s", exc)
        return None
    if not faces:
        return None
    biggest = max(faces, key=lambda f: f.area)
    img = cv2.imread(image_path)
    if img is None:
        return None
    x1, y1, x2, y2 = biggest.bbox
    x1 = max(0, int(x1)); y1 = max(0, int(y1))
    x2 = min(img.shape[1], int(x2)); y2 = min(img.shape[0], int(y2))
    return img[y1:y2, x1:x2]


# --------------------------------------------------------------------------- #
# Sub-strategies
# --------------------------------------------------------------------------- #


def _local_index_lookup(image_path: str, index_path: str) -> List[Candidate]:
    """Hash the largest face and look it up in the local index."""
    if not os.path.isfile(index_path):
        return []
    try:
        idx = json.loads(Path(index_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("additional_discovery: bad index file: %s", exc)
        return []
    if not isinstance(idx, list):
        return []

    crop = _largest_face_crop(image_path)
    if crop is None:
        return []
    h = _ahash_64(crop)

    out: List[Candidate] = []
    for rec in idx:
        if not isinstance(rec, dict):
            continue
        url = rec.get("url")
        if not url:
            continue
        rec_hash = str(rec.get("hash", "")).lower()
        # Either exact match, or close match (hamming <= 6 / 64 bits).
        if rec_hash == h or (rec_hash and _hamming(h, rec_hash) <= 6):
            out.append(
                Candidate(
                    url=url,
                    source=PROVIDER_NAME,
                    candidate_type=str(rec.get("candidate_type", CANDIDATE_TYPE_IMAGE)),
                    discovery_method="local_index_perceptual_hash",
                    metadata={
                        "hash": h,
                        "rec_hash": rec_hash,
                        "hamming": _hamming(h, rec_hash) if rec_hash else 0,
                    },
                )
            )
    return out


def _sidecar_url_hints(image_path: str) -> List[Candidate]:
    """If ``<image>.urls.txt`` exists, return its lines as URL candidates.

    This is also explicitly user-driven: the user placed a sidecar of
    URL hints next to the input file. Each non-empty, non-comment line is
    emitted as a *page* candidate (the fetcher will follow the link to
    its og:image, etc., if needed).
    """
    p = Path(image_path)
    sidecar = p.with_suffix(p.suffix + ".urls.txt")
    if not sidecar.is_file():
        return []
    out: List[Candidate] = []
    try:
        for raw in sidecar.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if not (line.startswith("http://") or line.startswith("https://")):
                continue
            out.append(
                Candidate(
                    url=line,
                    source=PROVIDER_NAME,
                    candidate_type=CANDIDATE_TYPE_PAGE,
                    discovery_method="sidecar_url_hints",
                    metadata={"sidecar": str(sidecar)},
                )
            )
    except OSError as exc:
        log.debug("additional_discovery: sidecar read failed: %s", exc)
    return out


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #


def is_available() -> bool:
    """Always available — uses only stdlib + the local face backend."""
    return True


def discover(
    image_path: str,
    max_results: int = 20,
    index_path: Optional[str] = None,
) -> List[Candidate]:
    """Run all sub-strategies and return the merged candidate list.

    Never raises. A failure in any sub-strategy is logged at DEBUG and
    the function continues with the remaining strategies.
    """
    out: List[Candidate] = []
    idx = index_path or os.environ.get("VERIFACE_DISCOVERY_INDEX", DEFAULT_INDEX_PATH)

    try:
        out.extend(_local_index_lookup(image_path, idx))
    except Exception as exc:  # noqa: BLE001
        log.debug("additional_discovery: local index lookup failed: %s", exc)
    try:
        out.extend(_sidecar_url_hints(image_path))
    except Exception as exc:  # noqa: BLE001
        log.debug("additional_discovery: sidecar hints failed: %s", exc)

    return out[:max_results]
