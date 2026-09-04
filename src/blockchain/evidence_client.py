"""Phase 3 on-chain evidence anchoring + independent re-verification.

Two anchoring flows are supported:

1. **Legacy image-fingerprint anchor** — ``anchor_evidence`` /
   ``verify_onchain``. Anchors the SHA-256 of the evidence image
   itself (``image_sha256``). Kept for backwards compatibility.

2. **Canonical-bundle anchor** — ``anchor_canonical_evidence`` /
   ``verify_canonical_evidence``. Anchors the SHA-256 of the
   canonical JSON evidence bundle produced by
   :class:`src.evidence.MatchEvidence`. The on-chain commitment is
   the canonical-bundle hash so any change to any field of the
   bundle makes re-verification fail.

Signing is offline and unit-tested without a node; sending / verifying
requires a JSON-RPC endpoint and a configured owner key (see
``.env.example``).

Security: anchoring and verification are always performed **after** the Phase 2
consent gate (``check_consent``) has granted the input face to an enrolled
subject — see :mod:`src.evidence` for the gate contract and ``src/main.py`` for
the CLI enforcement.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_hash.auto import keccak

from src.evidence import (
    EvidenceRecord,
    MatchEvidence,
    build_evidence_record,
    build_match_evidence,
    subject_id_to_bytes32_hex,
)

try:
    from eth_account import Account

    _HAS_ACCOUNT = True
except Exception:  # pragma: no cover - eth-account is an optional Phase 3 dep
    Account = None
    _HAS_ACCOUNT = False

CHAIN_DEFAULT = 80002  # Polygon Amoy
GAS_LIMIT_ANCHOR = 200_000
ANCHOR_REQUEST_FILENAME = "anchor_request.json"


def _selector(signature: str) -> bytes:
    return keccak(signature.encode("utf-8"))[:4]


ADD_RECORD_SELECTOR = _selector("addRecord(bytes32,bytes32)")
IS_RECORDED_SELECTOR = _selector("isRecorded(bytes32)")
VERIFY_SELECTOR = _selector("verify(bytes32)")
GET_RECORD_SELECTOR = _selector("getRecord(bytes32)")


def contract_abi() -> list:
    return [
        {
            "inputs": [{"internalType": "bytes32", "name": "subjectId", "type": "bytes32"},
                        {"internalType": "bytes32", "name": "sha256", "type": "bytes32"}],
            "name": "addRecord", "outputs": [], "type": "function",
            "stateMutability": "nonpayable",
        },
        {"inputs": [{"internalType": "bytes32", "name": "sha256", "type": "bytes32"}],
         "name": "isRecorded", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
         "type": "function", "stateMutability": "view"},
        {"inputs": [{"internalType": "bytes32", "name": "sha256", "type": "bytes32"}],
         "name": "verify",
         "outputs": [{"internalType": "bytes32", "name": "subjectId", "type": "bytes32"},
                     {"internalType": "bytes32", "name": "sha256", "type": "bytes32"},
                     {"internalType": "uint256", "name": "createdAt", "type": "uint256"},
                     {"internalType": "address", "name": "submitter", "type": "address"}],
         "type": "function", "stateMutability": "view"},
        {"inputs": [{"internalType": "bytes32", "name": "sha256", "type": "bytes32"}],
         "name": "getRecord",
         "outputs": [{"internalType": "bytes32", "name": "subjectId", "type": "bytes32"},
                     {"internalType": "bytes32", "name": "sha256", "type": "bytes32"},
                     {"internalType": "uint256", "name": "createdAt", "type": "uint256"},
                     {"internalType": "address", "name": "submitter", "type": "address"}],
         "type": "function", "stateMutability": "view"},
    ]


def _require_account() -> "type":
    if not _HAS_ACCOUNT:
        raise RuntimeError(
            "eth-account is required for signing (pip install eth-account). "
            "Alternatively run anchoring via the Hardhat script: "
            "npx hardhat run scripts/anchor.js --network amoy"
        )
    return Account


def encode_add_record(subject_id: str, image_path: str) -> tuple[bytes, dict]:
    """Build the ``addRecord`` calldata for the consent-gated evidence payload."""
    record = build_evidence_record(subject_id, image_path)
    data = ADD_RECORD_SELECTOR + abi_encode(
        ["bytes32", "bytes32"],
        [bytes.fromhex(record.subject_id_bytes32), bytes.fromhex(record.image_sha256)],
    )
    return data, record


def encode_is_recorded(sha256_hex: str) -> bytes:
    return IS_RECORDED_SELECTOR + abi_encode(["bytes32"], [bytes.fromhex(sha256_hex)])


def encode_verify(sha256_hex: str) -> bytes:
    return VERIFY_SELECTOR + abi_encode(["bytes32"], [bytes.fromhex(sha256_hex)])


def _rpc(payload: dict, rpc_url: str, timeout: float = 30.0) -> dict:
    import urllib.error
    import urllib.request

    if not rpc_url:
        raise RuntimeError(
            "no JSON-RPC endpoint configured (set --rpc or POLYGON_AMOY_RPC_URL)."
        )
    req = urllib.request.Request(
        rpc_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "veritrace/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"RPC {rpc_url!r} returned HTTP {exc.code} {exc.reason} "
            f"— check the endpoint / API key."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"RPC unreachable ({rpc_url!r}): {exc.reason}") from exc
    try:
        res = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"RPC {rpc_url!r} returned non-JSON: {body[:200]!r}") from exc
    if res.get("error"):
        raise RuntimeError(f"RPC error ({payload.get('method')}): {res['error']}")
    return res


def eth_call(to: str, data: bytes, rpc_url: str) -> bytes:
    """Read-only eth_call; returns raw 0x-prefixed result bytes."""
    res = _rpc(
        {"jsonrpc": "2.0", "method": "eth_call",
         "params": [{"to": to, "data": "0x" + data.hex()}, "latest"], "id": 1},
        rpc_url,
    )
    if "error" in res:
        raise RuntimeError(f"eth_call error: {res['error']}")
    return bytes.fromhex(res["result"][2:])


def _nonce(addr: str, rpc_url: str) -> int:
    res = _rpc(
        {"jsonrpc": "2.0", "method": "eth_getTransactionCount",
         "params": [addr, "latest"], "id": 1},
        rpc_url,
    )
    return int(res["result"], 16)


def _chain_id(rpc_url: str) -> int:
    res = _rpc(
        {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1},
        rpc_url,
    )
    return int(res["result"], 16) or CHAIN_DEFAULT


def _gas_price(rpc_url: str) -> int:
    res = _rpc(
        {"jsonrpc": "2.0", "method": "eth_maxPriorityFeePerGas", "params": [], "id": 1},
        rpc_url,
    )
    return int(res["result"], 16) + 1_000_000_000


def _wait_for_receipt(tx_hash: str, rpc_url: str, timeout: float = 90.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        res = _rpc(
            {"jsonrpc": "2.0", "method": "eth_getTransactionReceipt",
             "params": [tx_hash], "id": 1},
            rpc_url,
        )
        if res.get("result"):
            return res["result"]
        time.sleep(2)
    raise RuntimeError(f"tx not mined within {int(timeout)}s: {tx_hash}")


def anchor_evidence(
    subject_id: str,
    image_path: str,
    contract: str,
    rpc_url: str,
    private_key: Optional[str] = None,
    out_dir: Optional[Path] = None,
) -> dict:
    """Anchor an evidence fingerprint on-chain for an already-consented subject.

    If no ``private_key`` is supplied, nothing is signed/sent; instead an
    ``anchor_request.json`` is written for an external signer (e.g. the Hardhat
    anchor script) to consume. This keeps secrets out of logs and lets the
    operator decide when to broadcast.
    """
    data, record = encode_add_record(subject_id, image_path)
    payload = asdict(record) | {
        "contract": contract,
        "chain_id": CHAIN_DEFAULT,
        "calldata": "0x" + data.hex(),
        "selector": "0x" + ADD_RECORD_SELECTOR.hex(),
    }
    if private_key is None:
        out_dir = Path(out_dir) if out_dir else Path("data") / "evidence"
        out_dir.mkdir(parents=True, exist_ok=True)
        req_path = out_dir / ANCHOR_REQUEST_FILENAME
        req_path.write_text(json.dumps(payload, indent=2))
        payload["_request_file"] = str(req_path)
        payload["_status"] = "pending_signer"
        return payload

    Account = _require_account()
    acct = Account.from_key(private_key)
    nonce = _nonce(acct.address, rpc_url)
    tx = {
        "nonce": nonce,
        "gas": GAS_LIMIT_ANCHOR,
        "gasPrice": _gas_price(rpc_url),
        "to": contract,
        "data": "0x" + data.hex(),
        "value": 0,
        "chainId": _chain_id(rpc_url),
    }
    signed = Account.sign_transaction(tx, private_key)
    send = _rpc(
        {"jsonrpc": "2.0", "method": "eth_sendRawTransaction",
         "params": ["0x" + signed.raw_transaction.hex()], "id": 1},
        rpc_url,
    )
    tx_hash = send["result"]
    receipt = _wait_for_receipt(tx_hash, rpc_url)
    ok = receipt.get("status") == "0x1"
    payload["tx_hash"] = tx_hash
    payload["block_number"] = int(receipt["blockNumber"], 16)
    payload["gas_used"] = int(receipt["gasUsed"], 16)
    payload["_status"] = "mined" if ok else "reverted"
    if not ok:
        raise RuntimeError(f"anchor tx reverted: {tx_hash}")
    return payload


def verify_onchain(sha256_hex: str, contract: str, rpc_url: str) -> dict:
    """Read-only on-chain check that a fingerprint was anchored (independent re-verification)."""
    recorded = abi_decode(["bool"], eth_call(contract, encode_is_recorded(sha256_hex), rpc_url))[0]
    record = None
    if recorded:
        try:
            raw = eth_call(contract, encode_verify(sha256_hex), rpc_url)
            subj, fp, ts, who = abi_decode(
                ["bytes32", "bytes32", "uint256", "address"], raw
            )
            record = {"subject_id_bytes32": "0x" + subj.hex(),
                      "sha256": "0x" + fp.hex(), "createdAt": int(ts),
                      "submitter": who}
        except Exception:
            record = None
    return {"is_recorded": recorded, "record": record}


# --------------------------------------------------------------------------- #
# Canonical evidence (best-verified-match) anchor + verify
# --------------------------------------------------------------------------- #


def anchor_canonical_evidence(
    evidence: MatchEvidence,
    contract: str,
    rpc_url: str,
    private_key: Optional[str] = None,
    out_dir: Optional[Path] = None,
) -> dict:
    """Anchor the canonical-bundle SHA-256 of a :class:`MatchEvidence`.

    The on-chain commitment is ``evidence.canonical_sha256()``, which
    is the SHA-256 of the deterministic canonical JSON bundle (page
    URL, image hash, similarity, threshold, fetch_mode,
    discovery_sources, query image hash, etc.). Any change to any
    field makes the canonical hash change, so re-verification fails.

    Like the legacy flow, if no ``private_key`` is supplied the
    function writes an ``anchor_request.json`` (including the full
    canonical bundle + calldata) for an external signer.
    """
    bundle = evidence.to_bundle()
    bundle_bytes = evidence.to_canonical_bytes()
    bundle_sha = evidence.canonical_sha256()
    subject_id_bytes32 = subject_id_to_bytes32_hex(evidence.match_page_url)
    data = ADD_RECORD_SELECTOR + abi_encode(
        ["bytes32", "bytes32"],
        [bytes.fromhex(subject_id_bytes32), bytes.fromhex(bundle_sha)],
    )

    # Persist the canonical bundle alongside the request / receipt so
    # independent re-verification has a single source of truth.
    out_dir = Path(out_dir) if out_dir else Path("data") / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = out_dir / f"match_evidence_{bundle_sha[:16]}.json"
    bundle_path.write_bytes(bundle_bytes)

    payload = {
        "schema_version": evidence.schema_version,
        "bundle_sha256": bundle_sha,
        "subject_id_bytes32": "0x" + subject_id_bytes32,
        "contract": contract,
        "chain_id": CHAIN_DEFAULT,
        "calldata": "0x" + data.hex(),
        "selector": "0x" + ADD_RECORD_SELECTOR.hex(),
        "bundle": bundle,
        "bundle_file": str(bundle_path),
    }
    if private_key is None:
        req_path = out_dir / ANCHOR_REQUEST_FILENAME
        req_path.write_text(json.dumps(payload, indent=2))
        payload["_request_file"] = str(req_path)
        payload["_status"] = "pending_signer"
        return payload

    # Re-running the demo must be safe.  The registry intentionally rejects
    # duplicate writes, but a duplicate is still a successful integrity
    # outcome when this exact canonical bundle is already recorded.  Check
    # before signing so a second run neither spends gas nor reports a false
    # failure to the operator.
    already_recorded = abi_decode(
        ["bool"], eth_call(contract, encode_is_recorded(bundle_sha), rpc_url)
    )[0]
    if already_recorded:
        payload["_status"] = "already_recorded"
        return payload

    Account = _require_account()
    acct = Account.from_key(private_key)
    nonce = _nonce(acct.address, rpc_url)
    tx = {
        "nonce": nonce,
        "gas": GAS_LIMIT_ANCHOR,
        "gasPrice": _gas_price(rpc_url),
        "to": contract,
        "data": "0x" + data.hex(),
        "value": 0,
        "chainId": _chain_id(rpc_url),
    }
    signed = Account.sign_transaction(tx, private_key)
    send = _rpc(
        {"jsonrpc": "2.0", "method": "eth_sendRawTransaction",
         "params": ["0x" + signed.raw_transaction.hex()], "id": 1},
        rpc_url,
    )
    tx_hash = send["result"]
    receipt = _wait_for_receipt(tx_hash, rpc_url)
    ok = receipt.get("status") == "0x1"
    payload["tx_hash"] = tx_hash
    payload["block_number"] = int(receipt["blockNumber"], 16)
    payload["gas_used"] = int(receipt["gasUsed"], 16)
    payload["_status"] = "mined" if ok else "reverted"
    if not ok:
        raise RuntimeError(f"canonical-anchor tx reverted: {tx_hash}")
    return payload


def verify_canonical_evidence(
    evidence: MatchEvidence,
    contract: str,
    rpc_url: str,
) -> dict:
    """Independent re-verification of a :class:`MatchEvidence`.

    Recomputes the canonical SHA-256 locally, then checks whether
    that hash is recorded on-chain in the EvidenceRegistry.

    Returns a dict::

        {
          "local_sha256":         <hex>,
          "is_recorded":          bool,
          "onchain_sha256":       <hex> or None,
          "subject_id_bytes32":   <hex> or None,
          "submitter":            <addr> or None,
          "created_at":           <int> or None,
          "result":               "MATCH" or "TAMPER DETECTED" or "UNRECORDED",
          "tampered":             bool,
        }
    """
    local_sha = evidence.canonical_sha256()
    # Use the page_url as the subject-id surrogate on-chain so the
    # registry's per-subject uniqueness doesn't collide.
    subject_id_bytes32 = subject_id_to_bytes32_hex(evidence.match_page_url)
    is_recorded = abi_decode(
        ["bool"], eth_call(contract, encode_is_recorded(local_sha), rpc_url)
    )[0]
    onchain_sha = None
    submitter = None
    created_at = None
    if is_recorded:
        try:
            raw = eth_call(contract, encode_verify(local_sha), rpc_url)
            subj, fp, ts, who = abi_decode(
                ["bytes32", "bytes32", "uint256", "address"], raw
            )
            onchain_sha = "0x" + fp.hex()
            submitter = who
            created_at = int(ts)
            subject_id_bytes32 = "0x" + subj.hex()
        except Exception:
            pass
    if not is_recorded:
        result = "UNRECORDED"
        tampered = False
    elif onchain_sha and onchain_sha.lower() == ("0x" + local_sha).lower():
        result = "MATCH"
        tampered = False
    else:
        result = "TAMPER DETECTED"
        tampered = True
    return {
        "local_sha256": local_sha,
        "is_recorded": bool(is_recorded),
        "onchain_sha256": onchain_sha,
        "subject_id_bytes32": subject_id_bytes32,
        "submitter": submitter,
        "created_at": created_at,
        "result": result,
        "tampered": bool(tampered),
    }
