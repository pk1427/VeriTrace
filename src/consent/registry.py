"""Local consent registry (Phase 2 — not yet active).

Phase 1 deliberately does **not** implement any consent gate because the
pipeline performs no remote search and never matches an arbitrary face
against a database. The scan flow is strictly local:

    image -> detect -> embed -> print

Until Phase 2 (consent gate) is confirmed by the operator, this module is a
placeholder. It must NOT be wired into any search path before the gate is
implemented, so there is no code path that could search an unenrolled face.
"""
from __future__ import annotations

from pathlib import Path


REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "consent_registry.json"


def is_enrolled(*_args, **_kwargs):  # pragma: no cover - phase 2 placeholder
    raise NotImplementedError("Consent gate not implemented until Phase 2 (operator confirmation required).")


def load_registry():  # pragma: no cover - phase 2 placeholder
    raise NotImplementedError("Consent gate not implemented until Phase 2 (operator confirmation required).")
