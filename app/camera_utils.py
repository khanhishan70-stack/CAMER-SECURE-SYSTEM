from __future__ import annotations

import subprocess
import time

import cv2


_CAMERA_LIST_CACHE: list[dict] = []
# Camera names discovered by the (slow) wmic probe. Captured once at startup
# and reused so opening the camera never has to block on wmic again.
_KNOWN_NAMES: list[str] = []


def _get_camera_names() -> list[str]:
    try:
        ps = subprocess.run([
            "wmic", "path", "Win32_PnPEntity", "get", "Name,PNPClass"
        ], capture_output=True, text=True, timeout=5)
        if ps.returncode != 0:
            return []
        names: list[str] = []
        for line in ps.stdout.split('\n'):
            line = line.strip()
            if not line or "Camera" not in line or "DFU" in line:
                continue
            parts = line.rsplit(None, 1)
            if parts:
                name = parts[0].strip()
                if name:
                    names.append(name)
        return names
    except:
        return []


def _make_name(index: int, names: list[str], backend: str) -> str:
    if index < len(names):
        return f"{names[index]}"
    return f"Camera {index} ({backend})"


def _seed_cache() -> None:
    global _CAMERA_LIST_CACHE, _KNOWN_NAMES
    if _CAMERA_LIST_CACHE:
        return
    names = _get_camera_names()
    _KNOWN_NAMES = names
    if names:
        _CAMERA_LIST_CACHE = [{"index": i, "backend": "DSHOW", "name": n} for i, n in enumerate(names)]
    else:
        _CAMERA_LIST_CACHE = [{"index": 0, "backend": "DSHOW", "name": "Camera 0"}]


def open_camera(index: int = 0, retries: int = 5) -> cv2.VideoCapture | None:
    from app.config import CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS

    for i in range(retries):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap and cap.isOpened():
            # Ask for the smooth-preview profile (720p/30fps) from config.
            # Cheap USB cameras may refuse some settings - whatever sticks is
            # what we use; the engine adapts to the real frame size.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            for _ in range(5):
                if cap.grab():
                    _update_cache(index, cap)
                    return cap
                time.sleep(0.2)
            cap.release()
        else:
            if cap:
                cap.release()
        if i < retries - 1:
            time.sleep(2.0)
    return None


def _update_cache(index: int, cap: cv2.VideoCapture) -> None:
    # HOT PATH - called on every camera open, so never probe wmic here. Use the
    # names captured at startup (or a plain label) so opening stays fast.
    global _CAMERA_LIST_CACHE
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    names = _KNOWN_NAMES
    _CAMERA_LIST_CACHE = [{
        "index": index,
        "backend": cap.getBackendName(),
        "name": _make_name(index, names, cap.getBackendName()),
        "width": w,
        "height": h,
    }]


def list_cameras() -> list[dict]:
    global _CAMERA_LIST_CACHE
    if not _CAMERA_LIST_CACHE:
        _seed_cache()
    return _CAMERA_LIST_CACHE


_seed_cache()
