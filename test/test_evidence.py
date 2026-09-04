"""Phase 3 — evidence fingerprinting + tamper detection.

Pure-stdlib (no model, no network). Validates the SHA-256 evidence payload that
Phase 3 anchors on-chain, plus the canonical best-match evidence bundle
(used for the judge-facing full-flow demo).
"""
from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evidence import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    EvidenceRecord,
    MatchEvidence,
    build_evidence_record,
    build_match_evidence,
    canonical_json_bytes,
    canonical_sha256,
    payload_json,
    sha256_bytes,
    sha256_file,
    subject_id_to_bytes32_hex,
    tamper_demo,
    verify_evidence,
)
from src.search.ranking import rank_candidates, best_match  # noqa: E402


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


# --------------------------------------------------------------------------- #
# Canonical best-match evidence bundle
# --------------------------------------------------------------------------- #


def test_canonical_json_is_deterministic() -> None:
    obj = {
        "match": {"page_url": "https://a.com/p", "similarity": 0.9778},
        "query": {"image_sha256": "a" * 64, "face": {"model": "x", "embedding_dimension": 512}},
        "schema_version": "1.0",
    }
    a = canonical_json_bytes(obj)
    b = canonical_json_bytes(obj)
    assert a == b
    # Keys are sorted at every level
    assert a.index(b'"match"') < a.index(b'"page_url"') < a.index(b'"similarity"')
    assert a.index(b'"query"') < a.index(b'"face"') < a.index(b'"model"')


def test_canonical_json_rejects_unstable_fields() -> None:
    for bad in ("runtime", "timestamp", "embedded_at", "created_at", "uuid",
                "request_id", "nonce", "id"):
        try:
            canonical_json_bytes({bad: "x"})
        except ValueError:
            continue
        raise AssertionError(f"forbidden field {bad!r} was not rejected")


def test_canonical_json_no_nan_no_inf() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            canonical_json_bytes({"x": bad})
        except ValueError:
            continue
        raise AssertionError(f"non-finite {bad} was not rejected")


def _make_match_evidence(page_url: str = "https://a.com/p",
                          image_url: str = "https://a.com/i.jpg",
                          similarity: float = 0.9778) -> MatchEvidence:
    p = _write_tmp(b"query image bytes for evidence bundle test")
    return build_match_evidence(
        query_image_path=p,
        query_face_model="insightface-arcface-512",
        query_face_embedding_dim=512,
        page_url=page_url,
        image_url=image_url,
        candidate_image_sha256="a" * 64,
        similarity=similarity,
        threshold=0.6,
        fetch_mode="static",
        discovery_sources=["vision_web_detection"],
    )


def test_match_evidence_canonical_hash_is_deterministic() -> None:
    m1 = _make_match_evidence()
    m2 = _make_match_evidence()
    assert m1.canonical_sha256() == m2.canonical_sha256()


def test_match_evidence_tamper_detected_on_page_url_change() -> None:
    m = _make_match_evidence()
    good = m.canonical_sha256()
    m_tampered = _make_match_evidence(page_url="https://EVIL.com/p")
    assert m_tampered.canonical_sha256() != good, \
        "tampering the page_url must change the canonical hash"


def test_match_evidence_tamper_detected_on_image_hash_change() -> None:
    m = _make_match_evidence()
    good = m.canonical_sha256()
    p = _write_tmp(b"query image bytes for evidence bundle test")
    m_tampered = build_match_evidence(
        query_image_path=p,
        query_face_model="insightface-arcface-512",
        query_face_embedding_dim=512,
        page_url="https://a.com/p",
        image_url="https://a.com/i.jpg",
        candidate_image_sha256="b" * 64,  # <-- changed
        similarity=0.9778,
        threshold=0.6,
        fetch_mode="static",
        discovery_sources=["vision_web_detection"],
    )
    assert m_tampered.canonical_sha256() != good, \
        "tampering the image hash must change the canonical hash"


def test_match_evidence_tamper_detected_on_similarity_change() -> None:
    m = _make_match_evidence()
    good = m.canonical_sha256()
    m_tampered = _make_match_evidence(similarity=0.9999)
    assert m_tampered.canonical_sha256() != good, \
        "tampering the similarity must change the canonical hash"


def test_match_evidence_tamper_detected_on_threshold_change() -> None:
    """Lowering the threshold past the similarity would silently turn a
    non-verified match into a verified one — the canonical hash must
    catch it too."""
    m = _make_match_evidence(similarity=0.55)
    good = m.canonical_sha256()
    p = _write_tmp(b"query image bytes for evidence bundle test")
    m_tampered = build_match_evidence(
        query_image_path=p,
        query_face_model="insightface-arcface-512",
        query_face_embedding_dim=512,
        page_url="https://a.com/p",
        image_url="https://a.com/i.jpg",
        candidate_image_sha256="a" * 64,
        similarity=0.55,
        threshold=0.5,  # <-- lowered
        fetch_mode="static",
        discovery_sources=["vision_web_detection"],
    )
    assert m_tampered.canonical_sha256() != good, \
        "tampering the threshold must change the canonical hash"


def test_ranking_merges_duplicate_pages_and_orders_by_similarity() -> None:
    verified = [
        ("a.com", "https://a.com/p1", 0.9778, "vision", "static"),
        ("b.com", "https://b.com/p2", 0.7153, "vision", "static"),
        ("a.com", "https://a.com/p1", 0.7153, "yandex", "static"),  # same page, different provider
        ("c.com", "https://c.com/p3", 0.9985, "yandex", "static"),
    ]
    ranked = rank_candidates(verified, threshold=0.6)
    # 4 verified -> 3 unique pages (a.com/p1 deduped).
    assert len(ranked) == 3
    # Order: c.com (0.9985), a.com/p1 (0.9778, merged sources), b.com (0.7153)
    assert ranked[0].image_url == "https://c.com/p3"
    assert ranked[1].image_url == "https://a.com/p1"
    assert sorted(ranked[1].discovery_sources) == ["vision", "yandex"]
    assert ranked[2].image_url == "https://b.com/p2"
    best = best_match(ranked)
    assert best is not None and best.rank == 1 and best.similarity == 0.9985


def test_ranking_no_match_returns_none() -> None:
    verified = [
        ("a.com", "https://a.com/p1", 0.123, "vision", "static"),
    ]
    ranked = rank_candidates(verified, threshold=0.6)
    assert best_match(ranked) is None


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
