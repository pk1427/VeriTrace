"""Face encoding: produce a normalized embedding vector for a detected face.

Primary backend (InsightFace) yields a **512-dim** ArcFace (L2-normalized)
embedding. The ``face_recognition`` fallback yields a **128-dim** embedding.
Either way the values are L2-normalized so cosine similarity is simply the
dot product.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ._engine import BBox, analyze


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm > 0.0:
        vec = vec / norm
    return vec.astype(np.float32, copy=False)


def encode_largest_face(image_path: str, normalize: bool = True) -> np.ndarray:
    """Embed the largest face in ``image_path``.

    Raises ``ValueError`` if no face is detected.
    """
    from .detector import _largest_face_obj

    face = _largest_face_obj(image_path)
    if face is None:
        raise ValueError(f"No face detected in {image_path}")
    if face.embedding is None:
        raise RuntimeError("Backend did not return an embedding for the detected face.")
    emb = np.asarray(face.embedding, dtype=np.float32)
    return _normalize(emb) if normalize else emb


def encode_face(
    image_path: str, bbox: Optional[BBox] = None, iou_threshold: float = 0.5, normalize: bool = True
) -> np.ndarray:
    """Embed a specific face identified by ``bbox``.

    If ``bbox`` is ``None`` the largest face in the image is encoded. When a
    bbox is supplied the face whose box best overlaps it (IoU >= threshold) is
    chosen; otherwise the largest face is used as a fallback.
    """
    faces, _ = analyze(image_path)
    if not faces:
        raise ValueError(f"No face detected in {image_path}")

    if bbox is not None:
        target = _select_face_by_overlap(faces, bbox, iou_threshold)
    else:
        target = max(faces, key=lambda f: f.area)

    if target is None:
        raise ValueError(f"No face found near bbox {bbox} in {image_path}")
    if target.embedding is None:
        raise RuntimeError("Backend did not return an embedding for the detected face.")

    emb = np.asarray(target.embedding, dtype=np.float32)
    return _normalize(emb) if normalize else emb


def _select_face_by_overlap(faces, bbox, iou_threshold: float):
    x1, y1, x2, y2 = bbox
    best, best_iou = None, -1.0
    for f in faces:
        i = _iou(bbox, f.bbox)
        if i > best_iou:
            best, best_iou = f, i
    if best_iou >= iou_threshold:
        return best
    # Fall back to the largest face if overlap is too weak.
    return max(faces, key=lambda f: f.area)


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    a_area = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    b_area = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = a_area + b_area - inter
    if union <= 0:
        return 0.0
    return inter / union
