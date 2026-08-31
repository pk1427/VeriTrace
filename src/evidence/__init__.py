"""Phase 3 evidence fingerprinting + tamper detection (public API)."""
from src.evidence.evidence import (  # noqa: F401
    EvidenceRecord,
    build_evidence_record,
    payload_json,
    sha256_bytes,
    sha256_file,
    subject_id_to_bytes32_hex,
    tamper_demo,
    verify_evidence,
)
