"""Phase 3 on-chain evidence anchoring + independent re-verification.

Builds contract calldata, signs raw transactions (offline), and issues
read-only ``eth_call`` queries against ``EvidenceRegistry.sol``. Signing is
offline and unit-tested without a node; sending / verifying requires a
JSON-RPC endpoint and a configured owner key (see ``.env.example``).

Security: anchoring and verification are always performed **after** the Phase 2
consent gate (``check_consent``) has granted the input face to an enrolled
subject — see :mod:`src.evidence` for the gate contract and ``src/main.py`` for
the CLI enforcement.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_hash.auto import keccak

from src.evidence import EvidenceRecord, build_evidence_record, subject_id_to_bytes32_hex

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
