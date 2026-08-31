"""Phase 3 on-chain evidence anchoring (public API)."""
from src.blockchain.evidence_client import (  # noqa: F401
    ADD_RECORD_SELECTOR,
    IS_RECORDED_SELECTOR,
    VERIFY_SELECTOR,
    anchor_evidence,
    encode_add_record,
    encode_is_recorded,
    encode_verify,
    eth_call,
    verify_onchain,
)

__all__ = [
    "ADD_RECORD_SELECTOR",
    "IS_RECORDED_SELECTOR",
    "VERIFY_SELECTOR",
    "anchor_evidence",
    "encode_add_record",
    "encode_is_recorded",
    "encode_verify",
    "eth_call",
    "verify_onchain",
]
