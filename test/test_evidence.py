"""Phase 3 — evidence fingerprinting + tamper detection.

Pure-stdlib (no model, no network). Validates the SHA-256 evidence payload that
Phase 3 anchors on-chain.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evidence import (  # noqa: E402
    EvidenceRecord,
    build_evidence_record,
    payload_json,
    sha256_bytes,
    sha256_file,
    subject_id_to_bytes32_hex,
    tamper_demo,
    verify_evidence,
)


def _write_tmp(data: bytes) -> Path:
    p = Path(tempfile.mkdtemp()) / "evidence.bin"
    p.write_bytes(data)
    return p


def test_fingerprint_is_stable_sha256() -> None:
    p = _write_tmp(b"VeriTrace evidence payload v3")
    fp = sha256_file(p)
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)
    # recomputing matches the stdlib reference
    import hashlib
    assert fp == hashlib.sha256(b"VeriTrace evidence payload v3").hexdigest()


def test_build_evidence_record_binds_subject() -> None:
    p = _write_tmp(b"bytes of a face image")
    rec = build_evidence_record("prasad", p)
    assert rec.subject_id == "prasad"
    assert rec.image_sha256 == sha256_file(p)
    assert len(rec.subject_id_bytes32) == 64  # 32-byte hex
    assert rec.image_sha256 != rec.subject_id_bytes32


def test_tamper_demo_detects_changed_byte() -> None:
    p = _write_tmp(b"evidence-bytes-that-must-not-change-1234567890")
    out = tamper_demo(p)
    assert out["original"] != out["tampered"]
    assert out["changed"] is True


def test_verify_evidence_roundtrip_and_rejects_tamper() -> None:
    p = _write_tmp(b"untampered evidence content")
    fp = sha256_file(p)
    assert verify_evidence(p, fp) is True
    assert verify_evidence(p, fp + "00") is False            # forged hash
    assert verify_evidence(p, "") is False                    # empty hash
    missing = p.parent / "nope.bin"
    assert verify_evidence(missing, fp) is False             # missing file


def test_payload_json_is_deterministic() -> None:
    p = _write_tmp(b"image bytes")
    rec = build_evidence_record("owner", p)
    a = payload_json(rec)
    b = payload_json(rec)
    assert a == b and isinstance(a, bytes)
    # payload carries the fields the on-chain anchor needs
    import json
    decoded = json.loads(a)
    assert decoded["subject_id"] == "owner"
    assert decoded["evidence_sha256"] == rec.image_sha256


def test_subject_id_to_bytes32_hex_is_valid_hex32() -> None:
    h = subject_id_to_bytes32_hex("prasad")
    assert len(h) == 64
    bytes.fromhex(h)  # raises if not valid hex


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
