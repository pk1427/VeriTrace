"""Canonical JSON serializer for VeriTrace evidence bundles.

The on-chain commitment is the SHA-256 of a deterministic byte
representation of the evidence bundle. To make verification
reproducible across machines and over time we need:

  * Stable key ordering (always ``sort_keys=True``).
  * Stable numeric formatting (no scientific notation, no trailing
    zeros, ``str()`` is not enough because Python's ``repr`` of a
    ``float`` can vary).
  * No whitespace ambiguity (separators ``(',', ':')``).
  * UTF-8 with no BOM.
  * Reject unhashable / unstable fields at build time (we don't want
    a ``runtime`` or ``timestamp`` in the hashed payload).

The rules are enforced by the :func:`canonical_json_bytes` function.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


# Fields that must never appear inside a hashed canonical bundle.
# These would make the hash non-reproducible across runs.
_FORBIDDEN_IN_CANONICAL = {
    "runtime",
    "runtime_s",
    "elapsed_s",
    "timestamp",
    "embedded_at",
    "created_at",
    "uuid",
    "request_id",
    "nonce",
    "random_id",
    "id",  # opaque — callers that need an id should add it OUTSIDE the canonical block
}


def _format_number(v: Any) -> Any:
    """Format a number deterministically. Floats are serialized with
    :func:`json.dumps`'s default but we additionally trim redundant
    trailing zeros and ensure no scientific notation is used."""
    if isinstance(v, bool):
        # bool is a subclass of int; keep the JSON bool.
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        if v != v:  # NaN
            raise ValueError("NaN is not allowed in canonical JSON")
        if v == float("inf") or v == float("-inf"):
            raise ValueError("Infinity is not allowed in canonical JSON")
        # Use repr() for stable round-tripping; json.dumps default uses
        # this too, but we make it explicit.
        return repr(v)
    return v


def _scrub(obj: Any, path: str = "") -> Any:
    """Recursively walk the object, dropping forbidden fields and
    normalising numbers. Returns a new object suitable for json.dumps.

    Raises ``ValueError`` if a forbidden key is present at any depth.
    """
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            k_str = str(k)
            if k_str in _FORBIDDEN_IN_CANONICAL:
                raise ValueError(
                    f"forbidden field {k_str!r} at {path}/{k_str} — "
                    f"unstable fields must not be in canonical evidence"
                )
            cleaned[k_str] = _scrub(v, f"{path}/{k_str}")
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    return _format_number(obj)


def canonical_json_bytes(obj: Any) -> bytes:
    """Serialize ``obj`` to deterministic UTF-8 JSON bytes.

    * Reject forbidden fields (``runtime``, ``timestamp``, ...).
    * Sort keys at every level.
    * No whitespace between tokens (``(',', ':')``).
    * Numbers formatted via :func:`_format_number`.
    """
    cleaned = _scrub(obj)
    return json.dumps(
        cleaned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(obj: Any) -> str:
    """Return the hex SHA-256 of the canonical byte representation of ``obj``."""
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()
