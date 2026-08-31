"""Local consent registry + hard consent gate (Phase 2).

This module is the single enforcement point for the non-negotiable constraint:

> NEVER search for or match against a face that has not been explicitly
> enrolled by its owner in this local consent registry. The gate runs
> **before** any search API call; if no enrolled subject matches, the
> pipeline refuses to proceed.

The gate is :func:`check_consent`. Phase 3's search step MUST call it first
and must not issue any network request until it returns ``granted=True``.

Design notes
------------
* The live registry lives at ``data/consent_registry.json`` and is **git-ignored**
  (it stores biometric templates = 512-dim ArcFace embeddings), so owner face
  data never enters source control. A committed template
  ``data/consent_registry.example.json`` documents the schema.
* Embeddings are stored L2-normalized (ArcFace default), so matching is a pure
  cosine (dot) comparison in :mod:`src.face.matcher`.
* ``threshold`` defaults to 0.6 — confirmed against real faces in Phase 1
  (same-person ~0.72, different-person ~0.24). It is read from
  ``VERIFACE_SIMILARITY_THRESHOLD`` / stored per-registry so it can be tuned.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

# Lazy import to keep this module importable without the heavy backend at
# module load (the backend is only needed inside enroll()/check_consent()).
from src.face import encode_largest_face, cosine_similarity  # noqa: E402

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "consent_registry.json"
EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "data" / "consent_registry.example.json"
DEFAULT_THRESHOLD = 0.6
EMBED_DIM = 512

REGISTRY_VERSION = "0.2.0"


@dataclass
class ConsentResult:
    """Outcome of running the consent gate on one input face."""

    granted: bool
    subject_id: Optional[str]
    best_score: Optional[float]
    threshold: float
    best_subject: Optional[str]
    message: str
    allowed_domains: Optional[list] = None

    def as_dict(self) -> dict:
        return {
            "granted": self.granted,
            "matched_subject": self.best_subject if self.granted else None,
            "score": self.best_score,
            "threshold": self.threshold,
            "allowed_domains": self.allowed_domains,
            "message": self.message,
        }


def _env_path() -> Path:
    override = os.environ.get("VERIFACE_REGISTRY_PATH")
    return Path(override) if override else REGISTRY_PATH


def _empty_registry(threshold: float = DEFAULT_THRESHOLD) -> dict:
    return {
        "version": REGISTRY_VERSION,
        "description": "VeriTrace local consent registry (owner-enrolled subjects only).",
        "match_threshold": threshold,
        "subjects": [],
    }


def load_registry(path: Optional[Path] = None) -> dict:
    """Load the registry JSON. If missing/corrupt, return an empty registry."""
    p = path or _env_path()
    if not p.is_file():
        return _empty_registry()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return _empty_registry()
    data.setdefault("version", REGISTRY_VERSION)
    data.setdefault("match_threshold", DEFAULT_THRESHOLD)
    data.setdefault("subjects", [])
    return data


def save_registry(registry: dict, path: Optional[Path] = None) -> None:
    """Atomically write the registry to disk (git-ignored path)."""
    p = path or _env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, p)


def image_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _embedding_from_file(path: str) -> np.ndarray:
    """Detect + embed the largest face. Raises ValueError if none detected."""
    return encode_largest_face(path, normalize=True)


def is_enrolled(subject_id: str, path: Optional[Path] = None) -> bool:
    """Return True if ``subject_id`` already has an enrollment record."""
    reg = load_registry(path)
    return any(s.get("subject_id") == subject_id for s in reg.get("subjects", []))


def list_subjects(path: Optional[Path] = None) -> list:
    """Return enrolled subject IDs (no embeddings exposed)."""
    reg = load_registry(path)
    return [s.get("subject_id") for s in reg.get("subjects", [])]


def get_subject(subject_id: str, path: Optional[Path] = None) -> Optional[dict]:
    """Return the raw enrollment record for ``subject_id`` (or None).

    Used by Phase 2b to read the owner's per-owner ``allowed_domains`` search
    scope without re-running the face comparison.
    """
    reg = load_registry(path)
    for subj in reg.get("subjects", []):
        if subj.get("subject_id") == subject_id:
            return subj
    return None


def enroll(subject_id: str, image_path: str, threshold: float = DEFAULT_THRESHOLD,
           allowed_domains: Optional[list] = None,
           path: Optional[Path] = None) -> dict:
    """Enroll an owner-owned face.

    Steps:
      1. Detect + embed the face in ``image_path`` (512-dim ArcFace, normalized).
      2. Refuse if no face is detected.
      3. Refuse if ``subject_id`` is already enrolled (no silent overwrite).
      4. Persist the record to the git-ignored registry.

    ``allowed_domains`` (if provided) is the **per-owner** search scope: Phase 2b's
    reverse-image search will only return results from these hosts for this owner.
    When omitted it is stored as ``None`` and the caller falls back to its default.

    Returns the enrollment record (without echoing the raw embedding).
    """
    p = Path(image_path)
    if not p.is_file():
        raise FileNotFoundError(f"Enrollment image not found: {image_path}")

    if is_enrolled(subject_id, path):
        raise ValueError(f"subject '{subject_id}' is already enrolled — refusing to overwrite.")

    emb = _embedding_from_file(str(p))
    if emb.shape[0] != EMBED_DIM:
        raise RuntimeError(f"Expected {EMBED_DIM}-dim embedding, got {emb.shape[0]}.")

    sha = image_sha256(str(p))
    domains = [str(d).strip().lower() for d in (allowed_domains or []) if str(d).strip()] or None
    record = {
        "subject_id": subject_id,
        "enrollment_photo": p.name,
        "enrollment_sha256": sha,
        "embedding_b64": base64.b64encode(emb.tobytes()).decode("ascii"),
        "embedding_dim": EMBED_DIM,
        "enrolled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "allowed_domains": domains,
    }

    reg = load_registry(path)
    reg["version"] = REGISTRY_VERSION
    reg["match_threshold"] = reg.get("match_threshold", threshold)
    reg["subjects"].append(record)
    save_registry(reg, path)

    # Return a sanitized view (never echo the biometric vector in logs).
    return {
        "subject_id": subject_id,
        "enrollment_photo": p.name,
        "enrollment_sha256": sha,
        "embedding_dim": EMBED_DIM,
        "enrolled_at": record["enrolled_at"],
    }


def check_consent(image_path: str, threshold: Optional[float] = None,
                  path: Optional[Path] = None) -> ConsentResult:
    """Run the hard consent gate on ``image_path``.

    This MUST be called BEFORE any search API call. It embeds the input face
    and compares it (cosine similarity) against every enrolled subject.

    * If a face is not detected the gate is denied (nothing to verify).
    * If the best match is below ``threshold`` the gate is denied — the
      pipeline refuses to proceed (no search of an unenrolled face).
    * Only if the best match is >= ``threshold`` is consent granted, together
      with the matched ``subject_id`` and score.
    """
    p = Path(image_path)
    if not p.is_file():
        return ConsentResult(
            granted=False, subject_id=None, best_score=None,
            threshold=threshold or DEFAULT_THRESHOLD,
            best_subject=None,
            message=f"refusal: input image not found: {image_path}",
        )

    reg = load_registry(path)
    gate_threshold = float(threshold if threshold is not None else reg.get("match_threshold", DEFAULT_THRESHOLD))

    subjects = reg.get("subjects", [])
    if not subjects:
        # Privacy-preserving: refuse WITHOUT embedding the input at all — an
        # unenrolled face is never processed by the match step.
        return ConsentResult(
            granted=False, subject_id=None, best_score=None,
            threshold=gate_threshold, best_subject=None,
            message="refusal: consent registry is empty — no enrolled subjects; "
                    "not proceeding (unenrolled face would not be searched).",
        )

    try:
        input_emb = _embedding_from_file(str(p))
    except ValueError:
        return ConsentResult(
            granted=False, subject_id=None, best_score=None,
            threshold=gate_threshold, best_subject=None,
            message="refusal: no face detected in the input image — cannot verify consent.",
        )

    best_score: Optional[float] = None
    best_subject: Optional[str] = None
    best_record: Optional[dict] = None
    for subj in subjects:
        emb = _decode_embedding(subj)
        if emb is None:
            continue
        score = cosine_similarity(input_emb, emb)
        if best_score is None or score > best_score:
            best_score, best_subject, best_record = score, subj.get("subject_id"), subj

    assert best_score is not None and best_subject is not None
    granted = bool(best_score >= gate_threshold)
    # Per-owner search scope: the matched subject's own approved domains (may be
    # None for legacy enrollments → Phase 2b falls back to its default list).
    owner_domains = (best_record or {}).get("allowed_domains")
    if granted:
        message = (f"consent: GRANTED — input matches enrolled subject "
                   f"'{best_subject}' (score={best_score:.4f} >= {gate_threshold}).")
    else:
        message = (f"refusal: no enrolled subject matches (best={best_subject} "
                   f"score={best_score:.4f} < {gate_threshold}) — refusing to proceed.")
    return ConsentResult(
        granted=granted, subject_id=best_subject if granted else None,
        best_score=best_score, threshold=gate_threshold,
        best_subject=best_subject, message=message, allowed_domains=owner_domains,
    )


def _decode_embedding(record: dict) -> Optional[np.ndarray]:
    """Decode a stored base64 embedding back to a float32 vector."""
    b64 = record.get("embedding_b64")
    if not b64:
        return None
    try:
        return np.frombuffer(base64.b64decode(b64), dtype=np.float32)
    except Exception:
        return None


def reset_registry(path: Optional[Path] = None) -> None:
    """Empty the registry (used by tests / manual reset)."""
    save_registry(_empty_registry(), path)
