"""Phase 3 evidence fingerprinting + tamper detection (public API)."""
from src.evidence.canonical import (  # noqa: F401
    canonical_json_bytes,
    canonical_sha256,
)
from src.evidence.evidence import (  # noqa: F401
    EVIDENCE_SCHEMA_VERSION,
    EvidenceRecord,
    MatchEvidence,
    build_evidence_record,
    build_match_evidence,
    payload_json,
    sha256_bytes,
    sha256_file,
    subject_id_to_bytes32_hex,
    tamper_demo,
    verify_evidence,
)

__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "EvidenceRecord",
    "MatchEvidence",
    "build_evidence_record",
    "build_match_evidence",
    "canonical_json_bytes",
    "canonical_sha256",
    "payload_json",
    "sha256_bytes",
    "sha256_file",
    "subject_id_to_bytes32_hex",
    "tamper_demo",
    "verify_evidence",
]
