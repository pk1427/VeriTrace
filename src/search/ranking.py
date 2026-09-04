"""Global candidate ranking + dedup for the search pipeline.

The pipeline produces many ``verified`` candidates during a search run:

* Static-fetched images (one ``verified`` tuple per face photo that
  passed the threshold)
* JS-rendered images (one ``verified`` tuple per face photo extracted
  from a rendered DOM)

The raw order of those tuples reflects the order in which candidates
were processed (which is dominated by provider result order, not by
how likely they are to be the right person). For the judge-facing
output we need a single, explainable, deterministic ranking.

Algorithm
---------
1. For every verified candidate we have:

   - similarity         (float in [-1, 1])
   - page_url           (the source page)
   - image_url          (the actual face image that was verified)
   - discovery_sources  (the set of providers that surfaced this URL)
   - fetch_mode         ("static" or "js_rendered")
   - image_sha256       (the SHA-256 of the candidate image bytes)

2. **Exact-image dedup** — two candidates are the same image iff their
   ``image_sha256`` matches. We keep the higher-similarity entry and
   *merge* the provenance (discovery_sources, page_url) into one
   :class:`RankedCandidate`.

3. **Per-image ranking** — sort by ``similarity`` desc, then
   ``image_sha256`` (deterministic tie-break), and assign 1-based
   ``rank``.

4. **Best-match selection** — the first entry whose
   ``similarity >= threshold`` is the primary result. The confidence
   margin is ``similarity - threshold``.

The :class:`RankedCandidate` dataclass is intentionally minimal — it
holds exactly the fields the canonical evidence bundle needs and no
unstable state (no random ids, no runtime, no timestamps).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    """Read a file and return its hex SHA-256. Used to fingerprint the
    candidate image after we have it on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class RankedCandidate:
    """One ranked, deduplicated verified candidate image.

    Fields are exactly those the canonical evidence bundle needs.
    """

    rank: int
    similarity: float
    threshold: float
    verified: bool
    page_url: str
    image_url: str
    discovery_sources: List[str] = field(default_factory=list)
    fetch_mode: str = "static"
    image_sha256: str = ""
    face_index: int = 0
    faces_detected: int = 1

    @property
    def confidence_margin(self) -> float:
        """How far above the threshold this match is.

        A positive margin means verified; a negative one means
        ``similarity < threshold``. Reported verbatim — not a
        probability or confidence percentage.
        """
        return float(self.similarity) - float(self.threshold)

    def as_dict(self) -> dict:
        return {
            "rank": self.rank,
            "similarity": round(float(self.similarity), 6),
            "threshold": float(self.threshold),
            "verified": bool(self.verified),
            "page_url": self.page_url,
            "image_url": self.image_url,
            "discovery_sources": sorted(set(self.discovery_sources)),
            "fetch_mode": self.fetch_mode,
            "image_sha256": self.image_sha256,
            "face_index": int(self.face_index),
            "faces_detected": int(self.faces_detected),
        }


def _verified_tuple_to_seed(v: Tuple) -> dict:
    """Convert one (host, url, score, source, fetch_mode) tuple from
    ``cmd_search`` into a seed dict for ranking.

    The ``image_url`` is the *image* that was downloaded; the
    ``page_url`` is the *page* it came from. For direct-image
    candidates (provider returned an image URL directly) they are the
    same string. For HTML-page candidates the ``url`` is the page and
    we don't yet know which extracted image matched — but for ranking
    purposes we treat the page URL as the canonical key (the
    image_sha256 path replaces this with a per-image key once we
    have it).
    """
    host, url, score, source, fetch_mode = v[:5]
    return {
        "page_url": url,
        "image_url": url,
        "similarity": float(score),
        "discovery_sources": [str(source)],
        "fetch_mode": str(fetch_mode),
        # image_sha256 is filled in by the caller if available;
        # otherwise we fall back to URL-based dedup (which still works).
        "image_sha256": "",
    }


def rank_candidates(
    verified: Sequence[Tuple],
    rejected: Optional[Sequence[Tuple]] = None,
    threshold: float = 0.6,
    image_sha256_lookup: Optional[dict] = None,
) -> List[RankedCandidate]:
    """Globally rank verified candidates and return a deduplicated list.

    Parameters
    ----------
    verified : sequence of (host, url, score, source, fetch_mode)
        The verified-tuple list produced by ``cmd_search``.
    rejected : sequence of (host, url, reason) or None
        Currently unused by ranking (kept for future re-inclusion) but
        accepted so callers don't need to filter before passing.
    threshold : float
        The same threshold the verifier used.
    image_sha256_lookup : dict[image_url -> sha256] or None
        Optional map from candidate image URL to its on-disk SHA-256.
        When provided, exact-image dedup uses the hash; when absent,
        dedup falls back to the URL itself.
    """
    sha_lookup = image_sha256_lookup or {}

    # 1. Seed from verified tuples.
    seeds: List[dict] = [_verified_tuple_to_seed(v) for v in verified]
    for s in seeds:
        s["image_sha256"] = sha_lookup.get(s["image_url"], "")

    # 2. Exact-image dedup.
    by_sha: dict = {}
    by_url: dict = {}
    for s in seeds:
        # Prefer SHA-256 dedup; fall back to URL.
        key = s["image_sha256"] or s["image_url"]
        bucket = by_sha if s["image_sha256"] else by_url
        existing = bucket.get(key)
        if existing is None:
            bucket[key] = s
            continue
        # Merge provenance; keep the higher-similarity entry.
        existing["discovery_sources"] = list(
            dict.fromkeys(existing["discovery_sources"] + s["discovery_sources"])
        )
        if s["similarity"] > existing["similarity"]:
            existing["similarity"] = s["similarity"]
            existing["fetch_mode"] = s["fetch_mode"]
            existing["image_url"] = s["image_url"]
            existing["page_url"] = s["page_url"]

    deduped = list(by_sha.values()) + list(by_url.values())

    # 3. Sort: similarity desc, then image_sha256 (deterministic).
    deduped.sort(key=lambda s: (-s["similarity"], s.get("image_sha256", "")))

    # 4. Build ranked output.
    ranked: List[RankedCandidate] = []
    for i, s in enumerate(deduped, start=1):
        ranked.append(
            RankedCandidate(
                rank=i,
                similarity=s["similarity"],
                threshold=float(threshold),
                verified=bool(s["similarity"] >= threshold),
                page_url=s["page_url"],
                image_url=s["image_url"],
                discovery_sources=sorted(set(s["discovery_sources"])),
                fetch_mode=s["fetch_mode"],
                image_sha256=s["image_sha256"],
                face_index=0,
                faces_detected=1,
            )
        )
    return ranked


def best_match(ranked: List[RankedCandidate]) -> Optional[RankedCandidate]:
    """Return the strongest verified candidate, or None if no candidate
    passes the threshold."""
    for r in ranked:
        if r.verified:
            return r
    return None
