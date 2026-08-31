"""Face detection: locate faces and return bounding boxes.

Pure public surface on top of the shared backend (see ``_engine.analyze``).
No web search, no matching — just local detection from the input image.
"""
from __future__ import annotations

from typing import Optional

from ._engine import BBox, Face, analyze


def detect_faces(image_path: str) -> list[BBox]:
    """Return the bounding boxes of every detected face, largest last."""
    faces, _ = analyze(image_path)
    return [f.bbox for f in faces]


def detect_largest_face(image_path: str) -> Optional[BBox]:
    """Return the largest face's bounding box, or ``None`` if no face is found."""
    faces, _ = analyze(image_path)
    if not faces:
        return None
    best: Face = max(faces, key=lambda f: f.area)
    return best.bbox


def face_count(image_path: str) -> int:
    """Count the faces detected in an image."""
    return len(detect_faces(image_path))


def _largest_face_obj(image_path: str) -> Optional[Face]:
    """Internal: return the full :class:`Face` (with embedding) for the largest face."""
    faces, _ = analyze(image_path)
    if not faces:
        return None
    return max(faces, key=lambda f: f.area)
