"""Phase 3 evidence fingerprinting + tamper detection.

Pure stdlib (hashlib + json + time): produces a SHA-256 fingerprint of an
evidence image and a structured anchor payload ready for the on-chain
``EvidenceRegistry`` contract. Fully testable offline — no model, no network.

Two layers
----------
1. ``EvidenceRecord`` / ``build_evidence_record`` — the **legacy** image-only
   fingerprinting path. Anchors ``image_sha256`` of the evidence image
   itself. Kept for backwards compatibility with the existing
   blockchain flow and the existing tests.

2. ``build_match_evidence`` / ``MatchEvidence`` — the **new** canonical
   evidence bundle for the best verified match. Anchors the
   canonical-JSON SHA-256 of the bundle (which embeds the candidate
   image's SHA-256, the page URL, the discovery sources, the
   fetch_mode, the similarity, and the threshold). The on-chain
   commitment is now the canonical-bundle hash, so any change to any
   field makes verification fail with TAMPER DETECTED.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from src.evidence.canonical import canonical_json_bytes, canonical_sha256

_HASH_CHUNK = 1 << 16
EVIDENCE_SCHEMA_VERSION = "1.0"
def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    p = Path(path)
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def subject_id_to_bytes32_hex(subject_id: str) -> str:
    """Deterministic subject-id -> bytes32 mapping without keccak deps.

    sha256(subject_id) is 32 bytes, which fits Solidity ``bytes32`` and is
    collision-resistant enough to index an enrolled subject on-chain.
    """
    return hashlib.sha256(subject_id.encode("utf-8")).hexdigest()


@dataclass
class EvidenceRecord:
    """Legacy image-only evidence record.

    Anchors the SHA-256 of the *evidence image* itself. New code
    should prefer :class:`MatchEvidence` which anchors a canonical
    bundle hash.
    """
    subject_id: str
    image_sha256: str
    subject_id_bytes32: str
    embedded_at: float

    def to_anchor_payload(self) -> dict:
        return asdict(self) | {"evidence_sha256": self.image_sha256}


def build_evidence_record(subject_id: str, image_path: str | Path) -> EvidenceRecord:
    """Fingerprint an image and bind it to the enrolled subject that owns it.

    The caller MUST have already passed the consent gate for this face
    (Phase 3 enforces this before evidence is produced).
    """
    p = Path(image_path)
    if not p.is_file():
        raise FileNotFoundError(f"evidence image not found: {image_path}")
    return EvidenceRecord(
        subject_id=subject_id,
        image_sha256=sha256_file(p),
        subject_id_bytes32=subject_id_to_bytes32_hex(subject_id),
        embedded_at=time.time(),
    )


def payload_json(record: EvidenceRecord) -> bytes:
    """Canonical JSON byte payload (deterministic key order) to anchor verbatim."""
    payload = json.dumps(record.to_anchor_payload(), sort_keys=True, separators=(",", ":"))
    return payload.encode("utf-8")


def verify_evidence(image_path: str | Path, expected_sha256_hex: str) -> bool:
    """Return True iff the image at ``image_path`` still hashes to ``expected``."""
    p = Path(image_path)
    if not p.is_file() or not expected_sha256_hex:
        return False
    return sha256_file(p) == expected_sha256_hex


def tamper_demo(image_path: str | Path) -> dict:
    """Flip the trailing byte; demonstrate the fingerprint changes.

    Returns {"original": .., "tampered": .., "changed": bool}. Restores the
    temp file immediately. Used by the tamper demo and its tests.
    """
    p = Path(image_path)
    original = sha256_file(p)
    data = bytearray(p.read_bytes())
    if not data:
        raise ValueError("empty evidence image — cannot run tamper demo")
    data[-1] ^= 0xFF
    tmp = p.with_name(p.name + ".tamper")
    tmp.write_bytes(bytes(data))
    tampered = sha256_file(tmp)
    tmp.unlink(missing_ok=True)
    return {"original": original, "tampered": tampered, "changed": original != tampered}


# --------------------------------------------------------------------------- #
# Canonical best-match evidence bundle
# --------------------------------------------------------------------------- #


@dataclass
class MatchEvidence:
    """Canonical evidence bundle for the best verified match.

    The on-chain commitment is ``canonical_sha256`` of the
    :func:`to_bundle` payload (sorted keys, deterministic numbers,
    no unstable fields). Any change to any field — page URL, image
    hash, similarity, threshold, fetch_mode, discovery_sources —
    makes the canonical hash change, so independent re-verification
    fails with TAMPER DETECTED.
    """

    schema_version: str
    query_image_sha256: str          # SHA-256 of the user's input image
    query_face_model: str            # e.g. "insightface-arcface-512"
    query_face_embedding_dim: int    # 512
    match_page_url: str
    match_image_url: str
    match_image_sha256: str
    similarity: float
    threshold: float
    fetch_mode: str                  # "static" or "js_rendered"
    discovery_sources: List[str]

    def to_bundle(self) -> dict:
        """Return the canonical bundle (the dict that gets hashed).

        All field names are stable and human-readable. Stable number
        formatting is applied by :func:`canonical_sha256` /
        :func:`canonical_json_bytes`.
        """
        return {
            "schema_version": self.schema_version,
            "query": {
                "image_sha256": self.query_image_sha256,
                "face": {
                    "model": self.query_face_model,
                    "embedding_dimension": self.query_face_embedding_dim,
                },
            },
            "match": {
                "page_url": self.match_page_url,
                "image_url": self.match_image_url,
                "candidate_image_sha256": self.match_image_sha256,
                "similarity": round(float(self.similarity), 6),
                "threshold": round(float(self.threshold), 6),
                "verified": bool(float(self.similarity) >= float(self.threshold)),
                "fetch_mode": self.fetch_mode,
                "discovery_sources": sorted(set(self.discovery_sources)),
            },
        }

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_bundle())

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.to_bundle())


def build_match_evidence(
    *,
    query_image_path: str | Path,
    query_face_model: str,
    query_face_embedding_dim: int,
    page_url: str,
    image_url: str,
    candidate_image_sha256: str,
    similarity: float,
    threshold: float,
    fetch_mode: str,
    discovery_sources: List[str],
) -> MatchEvidence:
    """Build a :class:`MatchEvidence` from a ranked best-match.

    ``query_image_path`` is hashed here so the bundle is fully
    self-contained. ``candidate_image_sha256`` MUST already be the
    hex SHA-256 of the candidate image (the search pipeline computes
    it after fetching the image).
    """
    qp = Path(query_image_path)
    if not qp.is_file():
        raise FileNotFoundError(f"query image not found: {query_image_path}")
    return MatchEvidence(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        query_image_sha256=sha256_file(qp),
        query_face_model=str(query_face_model),
        query_face_embedding_dim=int(query_face_embedding_dim),
        match_page_url=str(page_url),
        match_image_url=str(image_url),
        match_image_sha256=str(candidate_image_sha256),
        similarity=float(similarity),
        threshold=float(threshold),
        fetch_mode=str(fetch_mode),
        discovery_sources=list(discovery_sources),
    )
