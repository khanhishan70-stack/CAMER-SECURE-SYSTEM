"""
Face embeddings via InsightFace (ONNX). Avoids TensorFlow; works on newer Python
when onnxruntime + insightface wheels install cleanly.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from insightface.app import FaceAnalysis

_app: FaceAnalysis | None = None
_init_lock = threading.Lock()
_last_error: str | None = None


@dataclass
class FaceDetection:
    embedding: np.ndarray
    bbox: tuple[int, int, int, int]


def dependencies_installed() -> bool:
    try:
        import insightface  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def last_init_error() -> str | None:
    return _last_error


def _area(bbox) -> float:
    return float(max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1]))


def _detect_providers() -> list[str]:
    providers = ["CPUExecutionProvider"]
    try:
        import onnxruntime as ort

        available = ort.get_available_providers()
        for preferred in ["CUDAExecutionProvider", "TensorrtExecutionProvider", "DmlExecutionProvider"]:
            if preferred in available:
                providers.insert(0, preferred)
                break
    except Exception:
        pass
    return providers


def _insightface_root() -> str | None:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = str(Path(sys._MEIPASS) / ".insightface")
        if Path(candidate).is_dir():
            return candidate
    return None


def get_app() -> FaceAnalysis:
    global _app, _last_error
    with _init_lock:
        if _app is not None:
            return _app
        try:
            from insightface.app import FaceAnalysis as FA

            providers = _detect_providers()
            kwargs = dict(name="buffalo_l", providers=providers)
            root = _insightface_root()
            if root is not None:
                kwargs["root"] = root
            model = FA(**kwargs)
            model.prepare(ctx_id=-1, det_size=(640, 640))
            _app = model
            _last_error = None
            return _app
        except Exception as e:
            _last_error = str(e)
            raise


def warmup() -> tuple[bool, str | None]:
    if not dependencies_installed():
        return False, "Install onnxruntime and insightface (see requirements.txt)."
    try:
        get_app()
        return True, None
    except Exception as e:
        return False, str(e)


def embedding_from_bgr(image_bgr: np.ndarray) -> np.ndarray | None:
    faces = get_app().get(image_bgr)
    if not faces:
        return None
    best = max(faces, key=lambda f: _area(f.bbox))
    return np.asarray(best.embedding, dtype=np.float64)


def embeddings_all_faces(image_bgr: np.ndarray) -> list[np.ndarray]:
    faces = get_app().get(image_bgr)
    return [np.asarray(f.embedding, dtype=np.float64) for f in faces]


def detect_faces(image_bgr: np.ndarray) -> list[FaceDetection]:
    faces = get_app().get(image_bgr)
    out: list[FaceDetection] = []
    for f in faces:
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        out.append(
            FaceDetection(
                embedding=np.asarray(f.embedding, dtype=np.float64),
                bbox=(x1, y1, x2, y2),
            )
        )
    return out
