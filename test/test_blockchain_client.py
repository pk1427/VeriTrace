"""Phase 3 — on-chain evidence anchoring (offline-only tests).

No network, no node: validates calldata encoding, offline signing, and the
JSON-RPC request shape (via a monkey-patched RPC). The actual anchor/verify
calls require a live JSON-RPC endpoint + owner key and are integration-tested
under Hardhat.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.blockchain.evidence_client as ec  # noqa: E402
from src.blockchain import (  # noqa: E402
    ADD_RECORD_SELECTOR,
    encode_add_record,
    encode_is_recorded,
    encode_verify,
)
from eth_abi import decode as abi_decode
from eth_account import Account


def _tmp_image() -> Path:
    p = Path(tempfile.mkdtemp()) / "evidence.bin"
    p.write_bytes(b"evidence-bytes-for-phase3")
    return p


def test_add_record_calldata_shape_and_roundtrip() -> None:
    p = _tmp_image()
    data, record = encode_add_record("prasad", str(p))
    # 4-byte selector + 2 x 32-byte args
    assert len(data) == 4 + 64
    assert data[:4] == ADD_RECORD_SELECTOR
    decoded = abi_decode(["bytes32", "bytes32"], data[4:])
    assert "0x" + decoded[0].hex() == "0x" + record.subject_id_bytes32
    assert "0x" + decoded[1].hex() == "0x" + record.image_sha256


def test_view_calldata_is_selector_plus_one_bytes32() -> None:
    fp = "ab" * 32
    assert len(encode_is_recorded(fp)) == 4 + 32
    assert encode_is_recorded(fp)[:4] == ec.IS_RECORDED_SELECTOR
    assert len(encode_verify(fp)) == 4 + 32
    assert encode_verify(fp)[:4] == ec.VERIFY_SELECTOR


def test_sign_transaction_offline_roundtrip() -> None:
    acct = Account.create()
    tx = {
        "nonce": 0,
        "gas": ec.GAS_LIMIT_ANCHOR,
        "gasPrice": 1_000_000_000,
        "to": "0x" + "11" * 20,
        "data": "0x" + encode_is_recorded("cd" * 32).hex(),
        "value": 0,
        "chainId": ec.CHAIN_DEFAULT,
    }
    signed = Account.sign_transaction(tx, acct.key)
    assert len(signed.raw_transaction) > 0
    assert Account.recover_transaction(signed.raw_transaction) == acct.address


def test_eth_call_builds_correct_request_and_decodes_bool(monkeypatch) -> None:
    captured: dict = {}

    def fake_rpc(payload, rpc_url, timeout=30.0):
        captured["payload"] = payload
        # keccak256 of empty 32-byte word with trailing 01 = bool true
        return {"result": "0x" + ("00" * 31) + "01"}

    monkeypatch.setattr(ec, "_rpc", fake_rpc)
    contract = "0x" + "11" * 20
    encoded = encode_is_recorded("ab" * 32)
    res = ec.eth_call(contract, encoded, "http://127.0.0.1:8545")
    assert res[-1] == 1  # abi_decode(['bool'], res)[0] is True
    payload = captured["payload"]
    assert payload["method"] == "eth_call"
    assert payload["params"][0]["to"] == contract
    assert payload["params"][0]["data"] == "0x" + encoded.hex()
    assert payload["params"][1] == "latest"


def test_anchor_without_key_writes_request_no_rpc(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(ec, "_rpc", lambda *a, **k: calls.append(1))
    p = _tmp_image()
    out_dir = Path(tempfile.mkdtemp())
    result = ec.anchor_evidence(
        "prasad", str(p), "0x" + "22" * 20, "http://x",
        private_key=None, out_dir=out_dir,
    )
    assert result["_status"] == "pending_signer"
    assert result["calldata"].startswith("0x")
    assert calls == []  # no RPC when unsigned
    req = out_dir / ec.ANCHOR_REQUEST_FILENAME
    assert req.is_file()
    on_disk = json.loads(req.read_text())
    assert on_disk["contract"] == "0x" + "22" * 20
    assert on_disk["calldata"] == result["calldata"]


def test_verify_onchain_not_recorded(monkeypatch) -> None:
    def fake_rpc(payload, rpc_url, timeout=30.0):
        return {"result": "0x" + "00" * 32}  # bool false

    monkeypatch.setattr(ec, "_rpc", fake_rpc)
    out = ec.verify_onchain("ef" * 32, "0x" + "33" * 20, "http://x")
    assert out["is_recorded"] is False
    assert out["record"] is None


class _Monkey:
    """Minimal monkeypatch stand-in so the suite runs standalone (no pytest)."""

    def __init__(self) -> None:
        self._orig: list = []

    def setattr(self, obj, name, value, raising=True) -> None:
        self._orig.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self) -> None:
        for obj, name, original in reversed(self._orig):
            setattr(obj, name, original)
        self._orig.clear()


def main() -> int:
    import inspect

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        m = _Monkey()
        try:
            params = inspect.signature(t).parameters
            if "monkeypatch" in params:
                t(monkeypatch=m)
            else:
                t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        finally:
            m.undo()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
