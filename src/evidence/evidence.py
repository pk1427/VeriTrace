"""Phase 3 evidence fingerprinting + tamper detection.

Pure stdlib (hashlib + json + time): produces a SHA-256 fingerprint of an
evidence image and a structured anchor payload ready for the on-chain
``EvidenceRegistry`` contract. Fully testable offline — no model, no network.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_HASH_CHUNK = 1 << 16


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
    subject_id: str
    image_sha256: str          # hex sha256 of the evidence image bytes
    subject_id_bytes32: str    # hex bytes32 used as the on-chain subjectId
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
