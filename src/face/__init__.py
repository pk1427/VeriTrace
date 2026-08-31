"""VeriTrace face-processing package (Phase 1).

Public API::

    from src.face import detect_largest_face, encode_largest_face, match

Submodules are lazily imported (PEP 562) so that importing the package does
**not** eagerly pull in the heavy backend (cv2 / InsightFace). The lightweight
``matcher`` module (NumPy only) can therefore be used even before the optional
face-detection stack is installed.
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from ._engine import BBox, Face
    import numpy as np  # noqa: F401

__all__ = [
    "BBox",
    "Face",
    "analyze",
    "backend_name",
    "cosine_similarity",
    "DEFAULT_THRESHOLD",
    "detect_faces",
    "detect_largest_face",
    "face_count",
    "encode_face",
    "encode_largest_face",
    "match",
]

# name -> (submodule within this package, attribute name)
_LAZY: dict = {
    "BBox": ("._engine", "BBox"),
    "Face": ("._engine", "Face"),
    "analyze": ("._engine", "analyze"),
    "backend_name": ("._engine", "backend_name"),
    "detect_faces": (".detector", "detect_faces"),
    "detect_largest_face": (".detector", "detect_largest_face"),
    "face_count": (".detector", "face_count"),
    "encode_face": (".encoder", "encode_face"),
    "encode_largest_face": (".encoder", "encode_largest_face"),
    "cosine_similarity": (".matcher", "cosine_similarity"),
    "DEFAULT_THRESHOLD": (".matcher", "DEFAULT_THRESHOLD"),
    "match": (".matcher", "match"),
}


def __getattr__(name: str):
    spec = _LAZY.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = spec
    module = import_module(mod_name, __name__)
    return getattr(module, attr)


def __dir__() -> list:
    return sorted(list(globals().keys()) + list(_LAZY.keys()))
