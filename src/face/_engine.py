"""Shared, lazily-loaded face-analysis backend.

Public modules ``detector`` and ``encoder`` both go through :func:`analyze`
so the underlying model is loaded exactly once and a single forward pass
yields both the face bounding boxes and the ArcFace embeddings.

Backend priority:

1. **InsightFace** (``insightface.app.FaceAnalysis``) on CPU via
   ``onnxruntime`` — produces **512-dim** ArcFace embeddings.
2. **face_recognition** (dlib ResNet) — fallback that produces **128-dim**
   embeddings when InsightFace cannot be installed (e.g. on some macOS
   toolchains). The matcher is dimension-agnostic, so the fallback composes
   transparently with the rest of the pipeline.

Neither backend is ever allowed to search for or match an *unenrolled* face
here — that consent gate lives in ``src/consent/`` and is enforced *before*
any search API is invoked (see ``src/main.py`` Phases 2+).
"""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from typing import List, Optional, Tuple

# The system Python on macOS links LibreSSL, which trips urllib3 v2's
# NotOpenSSLWarning on every InsightFace import. It is cosmetic and harmless
# (HTTPS to GitHub/PyPI works fine); silence it for clean CLI output.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="urllib3")

import cv2
import numpy as np

# Bounding box convention used throughout the package:
#   (x1, y1) = top-left corner, (x2, y2) = bottom-right corner (inclusive-exclusive).
BBox = Tuple[int, int, int, int]


@dataclass
class Face:
    """A detected face with optional ArcFace embedding."""

    bbox: BBox
    embedding: Optional[np.ndarray] = None
    det_score: float = 0.0
    landmark: Optional[np.ndarray] = None

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)


def read_image(path: str) -> np.ndarray:
    """Read an image from disk as a BGR array (InsightFace convention)."""
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"Could not read image (unsupported format or missing file): {path}")
    return img


def _load_insightface_app():
    from insightface.app import FaceAnalysis

    # Default to the compact ``buffalo_sc`` pack: a MobileFaceNet ArcFace
    # recognizer (w600k_mbf, 512-dim output) + RetinaFace detector in a ~15 MB
    # download — usable on metered / slow links. For maximum accuracy set
    # VERIFACE_MODEL_PACK=buffalo_l (~275 MB, ResNet-100 ArcFace).
    pack = os.environ.get("VERIFACE_MODEL_PACK", "buffalo_sc")
    app = FaceAnalysis(name=pack, providers=["CPUExecutionProvider"])
    # ctx_id=0 selects the first CPU execution provider; det_size is tuned for
    # small-to-medium portraits. det_thresh gates weak detections.
    app.prepare(ctx_id=0, det_thresh=float(os.environ.get("VERIFACE_DET_THRESH", "0.5")), det_size=(640, 640))
    return app


_APP: Optional[object] = None
_BACKEND: Optional[str] = None


def backend_name() -> str:
    """Return the name of the active backend, initialising it if needed."""
    global _APP, _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        _APP = _load_insightface_app()
        _BACKEND = "insightface"
    except Exception:
        _APP = None
        try:
            import face_recognition  # noqa: F401

            _BACKEND = "face_recognition"
        except Exception:
            _BACKEND = None
    return _BACKEND


def analyze(path: str) -> Tuple[List[Face], np.ndarray]:
    """Detect faces in ``path`` and return ``(faces, image_bgr)``.

    Each :class:`Face` carries a normalized 512-dim (InsightFace) or
    128-dim (face_recognition fallback) embedding, plus its bounding box.
    """
    img = read_image(path)
    backend = backend_name()
    if backend is None:
        raise RuntimeError(
            "No face backend available. Install the InsightFace stack "
            "(pip install -r requirements.txt) or face_recognition + dlib."
        )

    if backend == "insightface":
        # Single forward pass: detection + alignment + 512-dim ArcFace embedding.
        results = _APP.get(img)
        faces: List[Face] = []
        for r in results:
            emb = np.asarray(r.embedding, dtype=np.float32) if r.embedding is not None else None
            faces.append(
                Face(
                    bbox=tuple(int(v) for v in r.bbox),
                    embedding=emb,
                    det_score=float(r.det_score),
                    landmark=r.kps,
                )
            )
        return faces, img

    # face_recognition fallback (dlib HOG/CNN face locations + ResNet encodings).
    import face_recognition

    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    boxes = face_recognition.face_locations(rgb)
    encodings = face_recognition.face_encodings(rgb, boxes)
    faces = []
    for box, enc in zip(boxes, encodings):
        top, right, bottom, left = box
        faces.append(
            Face(
                bbox=(int(left), int(top), int(right), int(bottom)),
                embedding=np.asarray(enc, dtype=np.float32),
                det_score=1.0,
            )
        )
    return faces, img
