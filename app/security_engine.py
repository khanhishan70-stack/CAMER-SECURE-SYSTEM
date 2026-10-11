from __future__ import annotations

import math
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app import face_ai
from app.camera_utils import list_cameras, open_camera
from app.config import (
    ANALYSIS_INTERVAL_MS,
    ANALYSIS_SCALE,
    APP_VERSION,
    CAMERA_INDEX,
    DATA_DIR,
    ENGINE_LOOP_INTERVAL_SECONDS,
    ENGINE_TICK_WAIT_SECONDS,
    MATCH_THRESHOLD,
    PREVIEW_EVERY_SECONDS,
    PREVIEW_INTERVAL_SECONDS,
    PREVIEW_QUALITY,
    PREVIEW_MAX_WIDTH,
    UNKNOWN_VERIFY_SECONDS,
    UPDATE_CHECK_HOURS,
    UPDATE_REPO,
    GUARD_ANNOUNCE_COOLDOWN_SECONDS,
    VOICE_DONE_PATH,
    VOICE_GUARD_PATH,
)
from app import updater as app_updater
from app.face_store import load_embedding, save_embedding


# How long after the owner was last seen the desk is still treated as
# "owner around" (avoids locking while the owner simply turned away briefly).
OWNER_LEFT_GRACE_SECONDS = 2.0
# A fresh owner sighting only counts as a real "return" (and triggers the
# "Welcome back" notification) after the owner has been out of sight at least
# this long - shorter gaps are just detection flicker.
GREETING_MIN_ABSENCE_SECONDS = 3.0
# Minimum gap between "Welcome back" notifications. Stops them repeating
# while the camera keeps re-detecting the owner.
WELCOME_COOLDOWN_SECONDS = 20.0
# The owner counts as "still present" for this long after their last sighting,
# even across a few face-free frames.
OWNER_PRESENT_WINDOW_SECONDS = 5.0
# An unrecognized face must be seen for this many consecutive analysis frames
# (each ~ANALYSIS_INTERVAL_MS) before the verify countdown can start. A single
# phantom detection on an empty desk never arms a lock. And the moment the
# scene goes empty, any in-progress countdown is cancelled outright.
PERSON_CONFIRM_FRAMES = 3


@dataclass
class EngineStatus:
    running: bool = False
    enrolled: bool = False
    ai_ready: bool = False
    last_error: str | None = None
    scene: str = "no_camera"
    faces_seen: int = 0
    guard_active: bool = False
    face_match: bool = False


class SecurityEngine:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._preview_jpeg: bytes | None = None
        # Animated face boxes drawn over the camera preview on the UI: each is
        # [x1, y1, x2, y2] as fractions (0..1) of the preview image, refreshed
        # on every analysis frame so the box follows the face live.
        self._preview_boxes: list[list[float]] = []
        # Smooth-preview capture pump: a dedicated thread keeps pulling the
        # newest camera frame at full camera speed, so the heavy face-AI pass
        # (and any hiccup) can never stall the live preview. The engine loop
        # just grabs the latest snapshot and never touches cap.read() itself.
        self._pump_lock = threading.Lock()
        self._pump_frame: Any = None
        self._pump_frame_ts = 0.0
        self._pump_cap: Any = None
        self._pump_thread: threading.Thread | None = None
        self._pump_stop = threading.Event()
        self._pump_failed = threading.Event()
        # Face-AI worker: the InsightFace pass (hundreds of ms on CPU) runs on
        # its own thread so it can NEVER stall the live preview. The fast
        # engine loop just hands it a frame copy every ANALYSIS_INTERVAL_MS
        # and consumes the latest result - preview, boxes and guard logic all
        # stay fluid at ~10 Hz while analysis lands at its own ~2 Hz pace.
        self._analysis_lock = threading.Lock()
        self._analysis_wanted = threading.Event()
        self._analysis_busy = False
        self._analysis_frame: Any = None
        self._analysis_zoom = 1.0
        self._analysis_result: tuple | None = None
        self._analysis_error: str | None = None
        self._analysis_stop = threading.Event()
        self._analysis_thread = threading.Thread(
            target=self._analysis_loop, name="SecureCamFaceAI", daemon=True
        )
        self._analysis_thread.start()
        # Self-update system: polls the latest GitHub release in the
        # background. When a newer tag appears the UI pops an "Update
        # available" banner; one click downloads, applies, re-runs setup
        # (fresh dependencies), restarts, and pops "Update complete".
        self._install_root = app_updater.find_install_root(Path(__file__).parent)
        self._update_info: dict = {"available": False}
        self._update_prog = {"stage": "idle", "percent": 0, "detail": ""}
        self._update_prog_lock = threading.Lock()
        self._update_stop = threading.Event()
        self._update_thread = threading.Thread(
            target=self._update_check_loop, name="SecureCamUpdater", daemon=True
        )
        self._update_thread.start()
        self._status = EngineStatus()
        self._post_lock_cooldown_until = 0.0
        self._pending_enroll = threading.Event()
        self._enroll_cv = threading.Condition()
        self._enroll_done = False
        self._enroll_ok = False
        self._enroll_detail = ""
        self._lock_proc: threading.Thread | None = None
        self._enabled = True
        self._camera_source: int | str = CAMERA_INDEX
        self._switch_camera = threading.Event()
        self._face_match_pending = False
        self._owner_seen_at = 0.0
        self._last_greeting_at = 0.0
        self._warning_active = False
        self._intruder_warning_enabled = True
        self._camera_name: str = ""
        self._zoom = 1.0
        self._owner_left_at = 0.0
        self._unlock_event = threading.Event()
        self._verify_started_at: float | None = None
        # Voice command control (offline)
        self._voice_enabled = False
        self._voice_detector = None
        self._voice_event: str | None = None
        self._mic_retry_at = 0.0
        # FULL mic mute (privacy): when True the microphone is physically
        # released - not even the wake word is listened for - and nothing ever
        # restarts it while muted. Persisted so it survives app restarts.
        self._voice_muted = self._load_voice_muted()
        self._camera_active = False
        # After a voice "lock screen" command, further lock commands are
        # ignored for this long - stops a repeated mis-heard "lock" from
        # re-locking over and over (the user typically walks up after locking).
        self._voice_lock_rearm_at = 0.0
        # While set, the UI flashes the microphone indicator: a voice command
        # just landed (even if it was unrecognized).
        self._mic_flash_until = 0.0
        # "Guard active." announcement: cooldown between repeats plus a flag so
        # the guard only announces once per arm (reset when it disarms or the
        # owner returns to the desk).
        self._guard_voice_cd_until = 0.0
        self._guard_announced = False
        # While the SecureCam lock screen overlay is up, the camera is released
        # (privacy: no recording of the locked desktop / the person at it) and
        # the "not the owner" warning is spoken once per lock - it does NOT
        # repeat while the screen stays locked. Re-armed when the owner unlocks.
        self._screen_locked = False
        self._stranger_notice_armed = True

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            s = self._status
            face_match = self._face_match_pending
            self._face_match_pending = False
            voice_event = self._voice_event
            self._voice_event = None
            started = self._verify_started_at
            verify_left: int | None = None
            if s.scene == "verifying" and started is not None:
                remaining = UNKNOWN_VERIFY_SECONDS - (time.monotonic() - started)
                verify_left = max(0, int(math.ceil(remaining)))
            mic_active, mic_error = self._mic_state()
            if not mic_active and not self._voice_muted and time.monotonic() >= self._mic_retry_at:
                # Even with voice control OFF the mic stays up in wake-only
                # mode so "voice on" can always be heard - but never while it
                # is MUTED: muted means nobody can be heard until the user
                # clicks unmute.
                self._ensure_mic()
                mic_active, mic_error = self._mic_state()
            mic_level = 0.0
            det = self._voice_detector
            if det is not None and det.is_active():
                mic_level = det.get_level()
            with self._preview_lock:
                preview_boxes = [list(b) for b in self._preview_boxes]
            return {
                "running": s.running,
                "enrolled": s.enrolled,
                "ai_ready": s.ai_ready,
                "deepface_ok": s.ai_ready,
                "last_error": s.last_error,
                "scene": s.scene,
                "faces_seen": s.faces_seen,
                "face_count": len(preview_boxes),
                "face_boxes": preview_boxes,
                "guard_active": s.guard_active,
                "face_match": face_match,
                "match_threshold": MATCH_THRESHOLD,
                "verify_left": verify_left,
                "verify_seconds": UNKNOWN_VERIFY_SECONDS,
                "enabled": self._enabled,
                "intruder_warning": self._intruder_warning_enabled,
                "camera_source": self._camera_source,
                "camera_name": self._camera_name if self._camera_name else (str(self._camera_source) if not isinstance(self._camera_source, str) else self._camera_source),
                "camera_is_url": isinstance(self._camera_source, str),
                "camera_active": self._camera_active,
                "zoom": self._zoom,
                "voice_enabled": self._voice_enabled,
                "mic_active": mic_active,
                "mic_error": mic_error,
                "mic_level": mic_level,
                "mic_flash": time.monotonic() < self._mic_flash_until,
                "voice_event": voice_event,
                "app_version": APP_VERSION,
                "update_available": bool(self._update_info.get("available")),
                "update_latest": self._update_info.get("latest", ""),
                "update_notes": str(self._update_info.get("notes", ""))[:800],
                "update_packaged": self._install_root is not None,
                "update_prog": self.update_progress(),
                "just_updated": app_updater.peek_just_updated(DATA_DIR),
            }

    def get_preview_jpeg(self) -> bytes | None:
        with self._preview_lock:
            return self._preview_jpeg

    def _set_preview_boxes(self, boxes: list[list[float]]) -> None:
        with self._preview_lock:
            self._preview_boxes = boxes

    def _start_frame_pump(self, cap: Any) -> None:
        """Hand an open camera to the background capture pump.

        The pump thread owns cap.read() from here on; the engine loop only
        ever calls _pump_snapshot(). Because frames keep flowing at camera
        speed on their own thread, the preview stays smooth (~8 fps) even
        while the face-AI pass is chewing on a heavy frame.
        """
        self._stop_frame_pump()
        self._pump_cap = cap
        self._pump_stop.clear()
        self._pump_failed.clear()
        with self._pump_lock:
            self._pump_frame = None
            self._pump_frame_ts = 0.0
        t = threading.Thread(target=self._pump_loop, name="SecureCamCapture", daemon=True)
        self._pump_thread = t
        t.start()

    def _pump_loop(self) -> None:
        cap = self._pump_cap
        while not self._pump_stop.is_set() and cap is not None:
            try:
                ok, frame = cap.read()
            except Exception:  # noqa: BLE001
                ok, frame = False, None
            if not ok or frame is None:
                self._pump_failed.set()
                break
            with self._pump_lock:
                self._pump_frame = frame
                self._pump_frame_ts = time.monotonic()

    def _stop_frame_pump(self) -> None:
        self._pump_stop.set()
        t = self._pump_thread
        self._pump_thread = None
        if t is not None and t is not threading.current_thread() and t.is_alive():
            t.join(timeout=2.0)
        cap, self._pump_cap = self._pump_cap, None
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass

    def _pump_snapshot(self, max_age: float = 1.0) -> Any:
        """Newest camera frame (a private copy), or None if the feed stalled."""
        if self._pump_failed.is_set():
            return None
        with self._pump_lock:
            f = self._pump_frame
            ts = self._pump_frame_ts
        if f is None or (time.monotonic() - ts) > max_age:
            return None
        return f.copy()

    def _wait_first_frame(self, timeout: float = 4.0) -> bool:
        """Block until the freshly started pump delivers its first frame.

        Without this, the first snapshot right after _start_frame_pump() is
        legitimately empty (thread startup + one frame time) and would be
        misread as a stalled feed, causing an endless reopen loop.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop.is_set():
                return False
            if self._pump_snapshot(max_age=timeout) is not None:
                return True
            time.sleep(0.05)
        return False

    def _submit_analysis(self, frame: Any, zoom: float) -> bool:
        """Hand a frame copy to the face-AI worker. False = worker still busy
        (caller retries next tick - frames are cheap, results stay in order)."""
        with self._analysis_lock:
            if self._analysis_busy:
                return False
            self._analysis_busy = True
            self._analysis_frame = frame
            self._analysis_zoom = zoom
            self._analysis_wanted.set()
            return True

    def _take_analysis_result(self) -> tuple | None:
        with self._analysis_lock:
            res = self._analysis_result
            self._analysis_result = None
            return res

    def _take_analysis_error(self) -> str | None:
        with self._analysis_lock:
            err = self._analysis_error
            self._analysis_error = None
            return err

    def _discard_analysis(self) -> None:
        """Drop any in-flight/stale result (camera reopen, lock, unlock)."""
        with self._analysis_lock:
            self._analysis_frame = None
            self._analysis_result = None
            self._analysis_error = None

    def _analysis_loop(self) -> None:
        while not self._analysis_stop.is_set():
            if not self._analysis_wanted.wait(timeout=0.2):
                continue
            with self._analysis_lock:
                frame = self._analysis_frame
                zoom = self._analysis_zoom
                self._analysis_frame = None
                self._analysis_wanted.clear()
            if frame is None:
                with self._analysis_lock:
                    self._analysis_busy = False
                continue
            try:
                small = cv2.resize(
                    frame,
                    (0, 0),
                    fx=ANALYSIS_SCALE,
                    fy=ANALYSIS_SCALE,
                    interpolation=cv2.INTER_AREA,
                )
                detections = face_ai.detect_faces(small)
                fh, fw = frame.shape[:2]
                with self._analysis_lock:
                    self._analysis_result = (detections, fw, fh, zoom)
            except Exception as e:  # noqa: BLE001
                with self._analysis_lock:
                    self._analysis_error = str(e)
            finally:
                with self._analysis_lock:
                    self._analysis_busy = False

    # ---------------- self-update ----------------

    def _set_update_prog(self, stage: str, percent: int = 0, detail: str = "") -> None:
        with self._update_prog_lock:
            self._update_prog = {"stage": stage, "percent": percent, "detail": detail}

    def update_progress(self) -> dict[str, Any]:
        with self._update_prog_lock:
            return dict(self._update_prog)

    def update_info(self) -> dict[str, Any]:
        with self._lock:
            info = dict(self._update_info)
        return {
            "packaged": self._install_root is not None,
            "current": APP_VERSION,
            "available": bool(info.get("available")),
            "latest": info.get("latest", ""),
            "notes": str(info.get("notes", ""))[:800],
            "size": info.get("size", 0),
        }

    def refresh_update_check(self) -> dict[str, Any]:
        """Force an immediate check against GitHub (manual refresh button)."""
        try:
            res = app_updater.check_for_update(APP_VERSION, UPDATE_REPO)
            with self._lock:
                self._update_info = res
        except Exception:  # noqa: BLE001
            pass
        return self.update_info()

    def _update_check_loop(self) -> None:
        time.sleep(20.0)
        while not self._update_stop.is_set():
            try:
                res = app_updater.check_for_update(APP_VERSION, UPDATE_REPO)
                with self._lock:
                    self._update_info = res
            except Exception:  # noqa: BLE001
                pass
            self._update_stop.wait(UPDATE_CHECK_HOURS * 3600.0)

    def start_update(self) -> tuple[bool, str | None]:
        """Begin a one-click update. Returns (ok, error)."""
        with self._lock:
            info = dict(self._update_info)
        if not info.get("available"):
            return False, "No update available right now."
        if self._install_root is None:
            return False, "Self-update only works in the installed app."
        with self._update_prog_lock:
            if self._update_prog.get("stage") in ("downloading", "extracting", "applying"):
                return False, "An update is already in progress."
            self._update_prog = {"stage": "downloading", "percent": 0, "detail": "Starting..."}
        threading.Thread(target=self._update_worker, args=(info,), daemon=True).start()
        return True, None

    def _update_worker(self, info: dict[str, Any]) -> None:
        import tempfile
        import zipfile

        tag = str(info.get("latest") or "latest")
        safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in tag)
        try:
            stage = Path(tempfile.gettempdir()) / "css_update" / safe
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True, exist_ok=True)
            zippath = stage.parent / f"{safe}.zip"
            url = str(info.get("url") or "")
            total_hint = int(info.get("size") or 0)

            def _cb(got: int, total: int) -> None:
                t = total or total_hint or 1
                self._set_update_prog("downloading", min(99, int(got * 100 / t)),
                                      f"{got // 1048576} MB downloaded")

            self._set_update_prog("downloading", 0, "Downloading update...")
            app_updater.download_with_progress(url, zippath, _cb)
            self._set_update_prog("extracting", 0, "Unpacking update...")
            with zipfile.ZipFile(zippath, "r") as zf:
                zf.extractall(stage)
            try:
                zippath.unlink(missing_ok=True)
            except OSError:
                pass
            exe = stage / "SecureCam-win32-x64" / "SecureCam.exe"
            if not exe.is_file():
                raise RuntimeError("Downloaded package is incomplete (app missing).")
            bat = app_updater.write_apply_script(stage, self._install_root, tag)
            self._set_update_prog("applying", 100, "Restarting to apply update...")
            subprocess.Popen(
                [str(bat)],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=str(tempfile.gettempdir()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            # Give the apply script a head start, then exit so files unlock.
            time.sleep(4.0)
            try:
                self.stop()
            finally:
                os._exit(0)
        except Exception as e:  # noqa: BLE001
            self._set_update_prog("error", 0, str(e)[:300])

    def set_zoom(self, z: float) -> None:
        self._zoom = max(1.0, min(5.0, z))

    def get_zoom(self) -> float:
        return self._zoom

    def lock_now(self) -> None:
        self._spawn_lock_screen()

    def _spawn_lock_screen(self) -> None:
        if self._lock_proc and self._lock_proc.is_alive():
            return
        from app.password_store import has_password

        if not has_password():
            return
        from app.lock_screen import run_lock_screen

        self._screen_locked = True
        self._unlock_event.clear()
        t = threading.Thread(target=run_lock_screen, daemon=True)
        t.start()
        self._lock_proc = t
        def _on_unlock():
            t.join(timeout=60)
            self._unlock_event.set()
            self._post_lock_cooldown_until = time.monotonic() + 12.0
            self._owner_left_at = time.monotonic()
        threading.Thread(target=_on_unlock, daemon=True).start()

    def _lock_for_stranger(self) -> None:
        """Lock out a detected stranger, guaranteed to show SOMETHING.

        With an unlock password configured this is the SecureCam password
        overlay. Without one (fresh install, password never set) it falls
        back to the REAL Windows session lock (Win+L) - the stranger is
        still locked out with zero setup and the owner signs back in with
        Windows. Click-to-unlock overlay only as a last resort for VMs /
        remote sessions where the OS refuses Win+L.
        """
        try:
            from app.password_store import has_password

            if has_password():
                self._spawn_lock_screen()
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.lock_screen import lock_windows_session

            if lock_windows_session():
                # Real OS lock - nothing else to do. The engine keeps
                # watching; it sees the login screen (no faces) until the
                # owner signs back in.
                self._post_lock_cooldown_until = time.monotonic() + 12.0
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            self._spawn_unlock_click_screen()
        except Exception:  # noqa: BLE001
            pass

    def _spawn_warning_popup(self, side: str) -> bool:
        if self._warning_active:
            return False
        self._warning_active = True
        from app.lock_screen import show_warning_popup

        self._popup_result = [False]
        self._popup_done = threading.Event()

        def target():
            try:
                self._popup_result[0] = show_warning_popup(side)
            finally:
                self._popup_done.set()
                self._warning_active = False

        t = threading.Thread(target=target, daemon=True)
        t.start()
        return False

    def _popup_check(self) -> bool | None:
        if not hasattr(self, "_popup_done") or not self._popup_done:
            return None
        if self._popup_done.is_set():
            val = self._popup_result[0]
            del self._popup_done
            del self._popup_result
            return val
        return None

    def _finish_enroll_attempt(self, ok: bool, detail: str) -> None:
        with self._enroll_cv:
            self._enroll_done = True
            self._enroll_ok = ok
            self._enroll_detail = detail
            self._enroll_cv.notify_all()

    def request_capture_enroll(self, timeout: float = 12.0) -> tuple[bool, str]:
        if not face_ai.dependencies_installed():
            return False, "Face AI libraries missing - pip install -r requirements.txt"
        if not self._thread or not self._thread.is_alive():
            self.start()

        with self._enroll_cv:
            self._enroll_done = False
            self._enroll_ok = False
            self._enroll_detail = ""
            self._pending_enroll.set()
            notified = self._enroll_cv.wait_for(
                lambda: self._enroll_done or not (self._thread and self._thread.is_alive()),
                timeout=timeout,
            )
        if not notified:
            self._pending_enroll.clear()
            return False, "Timed out - check that the camera is available."
        if not self._enroll_done:
            self._pending_enroll.clear()
            detail = self.status_snapshot().get("last_error")
            return False, detail or "Camera engine is not running."
        return self._enroll_ok, self._enroll_detail

    def _set_status(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if k == "verify_started_at":
                    self._verify_started_at = v
                else:
                    setattr(self._status, k, v)

    # -- Security mode controller -----------------------------------------
    # Both the UI ON/OFF button and the voice commands call these same
    # central functions so camera, AI, and UI state always stay in sync.

    def activateSecurityMode(self) -> None:
        """Turn Security Mode ON: start the engine so the camera and AI run."""
        self._enabled = True
        self.start()

    def deactivateSecurityMode(self) -> None:
        """Turn Security Mode OFF: the engine loop releases the camera."""
        self._enabled = False

    def toggleSecurityMode(self, by_voice: bool = False) -> None:
        """Toggle Security Mode. With ``by_voice`` set, notify the UI."""
        activating = not self._enabled
        if activating:
            self._enabled = True
            self.start()
        else:
            self._enabled = False
        if by_voice:
            self._voice_event = "activated" if activating else "deactivated"

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.activateSecurityMode()
        else:
            self.deactivateSecurityMode()

    def is_enabled(self) -> bool:
        return self._enabled

    # -- Voice command control -------------------------------------------

    def is_voice_enabled(self) -> bool:
        return self._voice_enabled

    def is_voice_muted(self) -> bool:
        return self._voice_muted

    def _load_voice_muted(self) -> bool:
        try:
            from app.config import VOICE_STATE_FILE

            with open(VOICE_STATE_FILE, "r", encoding="utf-8") as f:
                import json

                return bool(json.load(f).get("muted", False))
        except Exception:  # noqa: BLE001
            return False

    def _save_voice_muted(self) -> None:
        try:
            from app.config import VOICE_STATE_FILE
            import json

            with open(VOICE_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"muted": bool(self._voice_muted)}, f)
        except Exception:  # noqa: BLE001
            pass

    def set_voice_muted(self, muted: bool) -> tuple[bool, str | None]:
        """Fully mute/unmute the microphone.

        MUTED = the mic is released and NOTHING re-opens it (no wake word, no
        auto-restart) until the user clicks unmute - so the AI hears no one,
        permanently, even across app restarts.
        """
        self._voice_muted = bool(muted)
        self._save_voice_muted()
        if self._voice_muted:
            self._stop_mic()
            return True, None
        # Unmute: bring the mic back in the right mode (full voice if control
        # is ON, wake-only otherwise).
        return self._ensure_mic()

    def voice_status(self) -> dict[str, Any]:
        active, err = self._mic_state()
        if self._voice_muted:
            active = False
        return {
            "voice_enabled": self._voice_enabled,
            "mic_active": active,
            "mic_muted": self._voice_muted,
            "mic_error": err,
        }

    def set_voice_enabled(self, enabled: bool) -> tuple[bool, str | None]:
        """Enable/disable full voice command control.

        With control ON the mic listens for voice commands. With control OFF it
        STAYS listening in a wake-only mode so a spoken "voice on" can bring it
        back - UNLESS the mic is MUTED, in which case it stays fully silent
        until the user unmutes (privacy: muted means nobody can ever be heard).
        Returns (ok, error).
        """
        self._voice_enabled = bool(enabled)
        if enabled and self._voice_muted:
            # Turning voice ON always un-mutes first, otherwise the commands
            # could never be heard.
            self._voice_muted = False
            self._save_voice_muted()
        ok, err = self._ensure_mic()
        if not ok:
            return ok, err
        det = self._voice_detector
        if det is not None:
            det.set_wake_only(not enabled)
        return True, None

    def _mic_state(self) -> tuple[bool, str | None]:
        det = self._voice_detector
        if det is None:
            return False, None
        return det.is_active(), det.error

    def _ensure_mic(self) -> tuple[bool, str | None]:
        if self._voice_muted:
            # Privacy is absolute: while muted, nothing may restart the mic.
            return True, None
        now = time.monotonic()
        if now < self._mic_retry_at:
            return True, None
        if self._voice_detector is None:
            from app.voice_commands import VoiceCommandDetector

            self._voice_detector = VoiceCommandDetector(
                on_camera_on=self._on_camera_on,
                on_camera_off=self._on_camera_off,
                on_lock=self._on_lock_command,
                on_voice_on=self._on_voice_on_command,
                on_voice_off=self._on_voice_off_command,
                on_unknown=self._on_voice_unknown,
            )
        det = self._voice_detector
        if det.is_active():
            return True, None
        ok, err = det.start()
        if not ok:
            self._mic_retry_at = now + 3.0
            return False, err
        det.set_wake_only(not self._voice_enabled)
        return True, None

    def _stop_mic(self) -> None:
        if self._voice_detector is not None:
            self._voice_detector.stop()

    def _on_camera_on(self) -> None:
        # "camera on" -> turn Security Mode ON instantly (camera + AI keep
        # running / start immediately). The camera is already warm while voice
        # control is on, so the toggle is near-instant.
        self._mic_flash_until = time.monotonic() + 1.5
        self.set_enabled(True)
        self._voice_event = "activated"
        self._speak_done()

    def _on_camera_off(self) -> None:
        # "camera off" -> turn Security Mode OFF immediately.
        self._mic_flash_until = time.monotonic() + 1.5
        self.set_enabled(False)
        self._voice_event = "deactivated"
        self._speak_done()

    def _speak_done(self) -> None:
        # Audible "done" confirmation in the girl voice so the user knows the
        # command landed even when the app window is in the background.
        try:
            from app.spoken_audio import play_wav

            play_wav(VOICE_DONE_PATH)
        except Exception:  # noqa: BLE001
            pass

    def _speak_guard_active(self) -> None:
        # Ava announces "Guard active." once the owner has genuinely left and
        # the guard has been armed over the empty desk for a sustained moment.
        try:
            from app.spoken_audio import play_wav

            play_wav(VOICE_GUARD_PATH)
        except Exception:  # noqa: BLE001
            pass

    def _on_voice_unknown(self) -> None:
        # Speech was heard that did not match a command - tell the UI so it can
        # hint at the valid phrases. Throttled by the detector.
        self._mic_flash_until = time.monotonic() + 1.5
        self._voice_event = "unknown"

    def _on_voice_on_command(self) -> None:
        # "voice on" -> keep / ensure Voice Control listening.
        # Runs off the detector thread: turning the mic on/off from inside the
        # detector's own thread would deadlock its stop() join.
        self._mic_flash_until = time.monotonic() + 1.5
        threading.Thread(target=self._voice_on_worker, daemon=True).start()

    def _on_voice_off_command(self) -> None:
        # "voice off" -> release the microphone and stop listening. After this
        # the mic is off, so "voice on" won't be audible - re-enable via the
        # toggle button (the mic must be listening to hear anything).
        self._mic_flash_until = time.monotonic() + 1.5
        threading.Thread(target=self._voice_off_worker, daemon=True).start()

    def _voice_on_worker(self) -> None:
        try:
            self.set_voice_enabled(True)
            self._voice_event = "voice_on"
            self._speak_done()
        except Exception:  # noqa: BLE001
            self._voice_event = "voice_error"

    def _voice_off_worker(self) -> None:
        try:
            self.set_voice_enabled(False)
            self._voice_event = "voice_off"
            self._speak_done()
        except Exception:  # noqa: BLE001
            self._voice_event = "voice_error"

    def _on_lock_command(self) -> None:
        # "lock screen" -> lock the PC immediately.
        self._mic_flash_until = time.monotonic() + 1.5
        now = time.monotonic()
        if now < self._voice_lock_rearm_at:
            # Already locked very recently - ignore the repeat (mis-heard or
            # double-stable phrase) instead of locking repeatedly.
            return
        self._voice_lock_rearm_at = now + 10.0
        self._voice_event = "locked"

        def _do() -> None:
            locked_ok = False
            try:
                from app.lock_screen import lock_windows_session

                locked_ok = bool(lock_windows_session())
            except Exception:  # noqa: BLE001
                locked_ok = False
            if locked_ok:
                # Real Windows lock (Win+L) - nothing else to do.
                return
            # The OS refused the lock (VM / remote session / no interactive
            # desktop). Fall back to a fullscreen in-app lock so the voice
            # command still visibly works: password-protected if one is set,
            # otherwise a simple click-to-unlock screen.
            try:
                from app.password_store import has_password

                if has_password():
                    self._spawn_lock_screen()
                else:
                    self._spawn_unlock_click_screen()
            except Exception:  # noqa: BLE001
                try:
                    self._spawn_lock_screen()
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_do, daemon=True).start()

    def _spawn_unlock_click_screen(self) -> None:
        if self._lock_proc and self._lock_proc.is_alive():
            return
        from app.lock_screen import run_unlock_click_screen

        self._screen_locked = True
        self._unlock_event.clear()
        t = threading.Thread(target=run_unlock_click_screen, daemon=True)
        t.start()
        self._lock_proc = t

        def _on_unlock():
            t.join(timeout=60)
            self._unlock_event.set()
            self._post_lock_cooldown_until = time.monotonic() + 12.0
            self._owner_left_at = time.monotonic()

        threading.Thread(target=_on_unlock, daemon=True).start()

    def _set_camera_active(self, active: bool) -> None:
        with self._lock:
            self._camera_active = bool(active)

    def set_intruder_warning(self, enabled: bool) -> None:
        self._intruder_warning_enabled = enabled

    def is_intruder_warning_enabled(self) -> bool:
        return self._intruder_warning_enabled

    def set_camera_source(self, source: int | str) -> None:
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            self._camera_source = source
            self._switch_camera.set()
        elif isinstance(source, int):
            self._camera_source = max(0, source)
            self._switch_camera.set()

    def get_camera_source(self) -> int | str:
        return self._camera_source

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_loop_wrapper, name="SecureCamEngine", daemon=True)
            self._thread.start()

    def _run_loop_wrapper(self) -> None:
        try:
            self._run_loop()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._set_status(running=False, last_error=f"{type(e).__name__}: {e}", scene="no_camera")

    def stop(self) -> None:
        self._stop.set()
        self._analysis_stop.set()
        self._analysis_wanted.set()
        self._update_stop.set()
        self._stop_mic()
        self._voice_event = None
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._lock_proc and self._lock_proc.is_alive():
            self._lock_proc = None
        with self._preview_lock:
            self._preview_jpeg = None
        self._pending_enroll.clear()
        self._finish_enroll_attempt(False, "Camera stopped.")
        self._set_camera_active(False)
        self._set_status(
            running=False,
            last_error=None,
            scene="no_camera",
            faces_seen=0,
            guard_active=False,
        )

    def _run_loop(self) -> None:
        ok, err = face_ai.warmup()
        self._set_status(
            running=True,
            ai_ready=ok,
            last_error=None if ok else (err or face_ai.last_init_error()),
        )
        if not ok:
            detail = err or face_ai.last_init_error() or "Face AI failed to start."
            self._set_status(running=False, last_error=detail, scene="no_camera")
            if self._pending_enroll.is_set():
                self._pending_enroll.clear()
                self._finish_enroll_attempt(False, detail)
            return

        guard_active = False
        unknown_since: float | None = None
        person_run = 0
        interval_s = ANALYSIS_INTERVAL_MS / 1000.0
        last_analysis_at = 0.0
        enrolled_vec_cache: np.ndarray | None = None
        last_enroll_load_at = 0.0
        last_preview_at = 0.0

        cap = None

        while not self._stop.is_set():
            if self._screen_locked:
                # SecureCam's own lock screen is up (AI stranger lock / voice
                # lock fallback). Release the webcam - no recording of the
                # locked screen. The "not the owner" warning was already said
                # once at the moment the lock was triggered - it is NOT repeated
                # while the screen stays locked. On unlock (password accepted)
                # the flag resets, so the NEXT stranger lock says it once again.
                if cap is not None:
                    self._stop_frame_pump()
                    cap = None
                self._set_camera_active(False)
                self._set_preview_boxes([])
                self._set_status(
                    scene="locked",
                    faces_seen=0,
                    guard_active=False,
                    verify_started_at=None,
                )
                if not (self._lock_proc and self._lock_proc.is_alive()):
                    # Password (or click) accepted - overlay closed. Auto-resume:
                    # treat the owner as freshly present so the camera re-opens
                    # and guarding continues normally this loop.
                    self._screen_locked = False
                    self._owner_seen_at = time.monotonic()
                    unknown_since = None
                    person_run = 0
                    self._discard_analysis()
                    self._guard_announced = False
                    self._stranger_notice_armed = True
                time.sleep(0.25)
                continue
            # The camera may be fully released ONLY when Security Mode is OFF
            # AND Voice Control is OFF and no enrollment is pending. If Voice
            # Control is ON (the mic is already listening), we keep the camera
            # warm so a spoken "camera on/off" flips it ON/OFF almost instantly -
            # no ~1-4s webcam re-open. A "camera off" that turns Security Mode
            # OFF still stops the video stream / AI analysis right away; only
            # the hardware handle stays open until Voice Control is turned off.
            hold_camera = self._enabled or self._pending_enroll.is_set() or self._voice_enabled
            resources_idle = not hold_camera
            if resources_idle:
                if cap is not None:
                    self._stop_frame_pump()
                    cap = None
                self._set_camera_active(False)
                self._set_preview_boxes([])
                # Remember the owner as seen now so re-enabling while still at
                # the desk does not trigger a spurious welcome-back greeting.
                self._owner_seen_at = time.monotonic()
                guard_active = False
                self._guard_announced = False
                unknown_since = None
                person_run = 0
                self._set_status(
                    scene="disabled", faces_seen=0, guard_active=False, last_error=None
                )
                while (
                    not self._stop.is_set()
                    and not self._enabled
                    and not self._pending_enroll.is_set()
                    and not self._voice_enabled
                ):
                    # Poll tightly so a voice/click re-enable reacts fast.
                    time.sleep(0.1)
                continue

            # The camera is needed - open it (with retries) if not open yet.
            if cap is None or not cap.isOpened():
                self._set_camera_active(False)
                cap = None
                for attempt in range(5):
                    cap = open_camera()
                    if cap is not None:
                        break
                    self._set_status(
                        last_error=f"Opening camera (attempt {attempt + 1}/5)...",
                        scene="no_camera",
                    )
                    time.sleep(2.0)
                if cap is not None:
                    # Pre-populate camera cache after opening (avoids DShow conflict)
                    try:
                        list_cameras()
                    except Exception:
                        pass
                    # Hand the stream to the background pump so frames keep
                    # flowing at camera speed while the AI loop below does its
                    # heavy analysis - this is what makes the preview smooth.
                    self._start_frame_pump(cap)
                    if not self._wait_first_frame():
                        # Pump never produced a frame - bad open, retry it.
                        self._stop_frame_pump()
                        cap = None
                        self._set_status(
                            last_error="Camera produced no frames — reopening",
                            scene="no_camera",
                        )
                        time.sleep(1.0)
                        continue
                    unknown_since = None
                    person_run = 0
                    self._discard_analysis()
                    last_preview_at = 0.0
                    self._set_camera_active(True)
                    self._set_status(last_error=None)
                else:
                    self._set_status(
                        last_error="Could not open camera after 5 attempts",
                        scene="no_camera",
                    )
                    while (
                        not self._stop.is_set()
                        and not self._enabled
                        and not self._pending_enroll.is_set()
                    ):
                        time.sleep(2.0)
                    continue

            self._switch_camera.clear()
            while (
                not self._stop.is_set()
                and not self._switch_camera.is_set()
                and (self._enabled or self._pending_enroll.is_set() or self._voice_enabled)
            ):
                if self._screen_locked:
                    # A lock (stranger / voice-lock fallback) started while we
                    # were deep inside the camera loop. Return to the outer loop
                    # so its locked branch can release the webcam, repeat the
                    # warning, and wait for the owner to unlock.
                    break
                t0 = time.monotonic()
                # Grab the newest frame from the capture pump - instant, never
                # blocks on the camera, and never waits for the AI. If the feed
                # stalled (or the pump died), reopen the camera.
                frame = self._pump_snapshot()
                if frame is None:
                    self._set_status(last_error="Camera feed stalled â€” reopening", scene="no_camera")
                    self._stop_frame_pump()
                    cap = None
                    self._set_camera_active(False)
                    break

                now_preview = time.monotonic()
                if now_preview - last_preview_at >= PREVIEW_EVERY_SECONDS:
                    last_preview_at = now_preview
                    zoom = self._zoom
                    if zoom > 1.0:
                        h, w = frame.shape[:2]
                        crop_w = int(w / zoom)
                        crop_h = int(h / zoom)
                        x = (w - crop_w) // 2
                        y = (h - crop_h) // 2
                        cropped = frame[y:y + crop_h, x:x + crop_w]
                        preview = cv2.resize(cropped, (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_LINEAR)
                    else:
                        preview = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
                    ok_jpg, buf = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_QUALITY])
                    if ok_jpg:
                        with self._preview_lock:
                            self._preview_jpeg = buf.tobytes()

                if self._pending_enroll.is_set():
                    self._pending_enroll.clear()
                    try:
                        emb = face_ai.embedding_from_bgr(frame)
                    except Exception as e:
                        emb = None
                        self._finish_enroll_attempt(False, str(e))
                        time.sleep(0.05)
                        continue

                    if emb is not None:
                        save_embedding(emb.astype(float).tolist())
                        self._finish_enroll_attempt(True, "")
                    else:
                        self._finish_enroll_attempt(
                            False,
                            "No clear face found - face the camera with good lighting.",
                        )
                    enrolled_vec_cache = load_embedding()
                    last_enroll_load_at = time.monotonic()
                    time.sleep(0.03)
                    continue

                if not self._enabled:
                    # Security Mode is OFF. The camera is being held warm only
                    # because Voice Control is still ON (or enrollment pending),
                    # so we keep draining frames to keep the stream alive but
                    # STOP all AI analysis and guarding. As soon as Voice
                    # Control also turns off, the outer loop releases the
                    # camera completely.
                    self._set_status(
                        scene="disabled",
                        faces_seen=0,
                        guard_active=False,
                        last_error=None,
                        enrolled=self._status.enrolled,
                        verify_started_at=None,
                    )
                    guard_active = False
                    self._guard_announced = False
                    unknown_since = None
                    person_run = 0
                    self._set_preview_boxes([])
                    time.sleep(0.02)
                    continue

                now = time.monotonic()
                if (now - last_enroll_load_at) > 2.0:
                    enrolled_vec_cache = load_embedding()
                    last_enroll_load_at = now

                enrolled = enrolled_vec_cache is not None
                self._set_status(enrolled=enrolled, last_error=None, ai_ready=True)

                if not enrolled:
                    self._set_preview_boxes([])
                    self._set_status(scene="nobody", faces_seen=0, guard_active=False)
                    guard_active = False
                    self._guard_announced = False
                    time.sleep(PREVIEW_INTERVAL_SECONDS * 0.8)
                    continue

                if (now - last_analysis_at) >= interval_s:
                    # Time for a fresh AI pass - hand the newest frame to the
                    # background worker (never blocks). If it is still chewing
                    # the previous frame, keep spinning fluidly and retry the
                    # submit on the next tick.
                    if self._submit_analysis(frame, self._zoom):
                        last_analysis_at = now
                analysis_err = self._take_analysis_error()
                if analysis_err:
                    self._set_status(last_error=analysis_err, scene="no_camera")
                res = self._take_analysis_result()
                if res is None:
                    # No fresh AI result yet. The preview above was already
                    # refreshed from the newest frame, so just keep the loop
                    # spinning fast instead of ever waiting on the AI.
                    time.sleep(ENGINE_TICK_WAIT_SECONDS)
                    continue
                detections, fw, fh, zoom = res

                faces_seen = len(detections)
                if zoom > 1.0:
                    crop_w = int(fw / zoom)
                    crop_h = int(fh / zoom)
                    bx = (fw - crop_w) // 2
                    by = (fh - crop_h) // 2
                    bw, bh = crop_w, crop_h
                else:
                    bx, by, bw, bh = 0, 0, fw, fh
                preview_boxes: list[list[float]] = []
                for det in detections:
                    sx1, sy1, sx2, sy2 = det.bbox
                    x1 = sx1 / ANALYSIS_SCALE - bx
                    y1 = sy1 / ANALYSIS_SCALE - by
                    x2 = sx2 / ANALYSIS_SCALE - bx
                    y2 = sy2 / ANALYSIS_SCALE - by
                    nx1 = max(0.0, min(1.0, x1 / bw))
                    ny1 = max(0.0, min(1.0, y1 / bh))
                    nx2 = max(0.0, min(1.0, x2 / bw))
                    ny2 = max(0.0, min(1.0, y2 / bh))
                    if nx2 - nx1 >= 0.01 and ny2 - ny1 >= 0.01:
                        preview_boxes.append([nx1, ny1, nx2, ny2])
                self._set_preview_boxes(preview_boxes)
                now = time.monotonic()

                # No face in the frame -> the desk is empty and the guard arms.
                if faces_seen == 0:
                    self._set_preview_boxes([])
                    if now - self._owner_seen_at < OWNER_PRESENT_WINDOW_SECONDS:
                        # Owner was seen moments ago - this is a detection
                        # flicker, keep the last-seen timestamp fresh.
                        self._owner_left_at = now
                    guard_active = True
                    # Nobody is in front of the camera, so there is nothing to
                    # lock out. Cancel any in-progress verify window right away
                    # and reset the "someone present" count - a phantom face
                    # detection later starts from zero again and needs several
                    # clean frames before it can ever arm a countdown.
                    person_run = 0
                    unknown_since = None
                    self._set_status(
                        guard_active=True,
                        scene="armed",
                        faces_seen=0,
                        verify_started_at=None,
                    )
                    # Announce "Guard active." only when the owner has REALLY
                    # left (no fresh sighting for a full presence-window), the
                    # arm has held for a few frames (no flicker), and never more
                    # often than the cooldown or twice per single arm.
                    if not self._guard_announced:
                        owner_really_gone = now - self._owner_left_at >= OWNER_PRESENT_WINDOW_SECONDS
                        if owner_really_gone and now >= self._guard_voice_cd_until:
                            self._guard_announced = True
                            self._guard_voice_cd_until = now + GUARD_ANNOUNCE_COOLDOWN_SECONDS
                            self._speak_guard_active()
                    time.sleep(0.02)
                    continue

                assert enrolled_vec_cache is not None
                owner_seen = False
                intruder_sides: list[str] = []
                frame_w = fw
                for det in detections:
                    sim = _cosine_similarity(det.embedding, enrolled_vec_cache)
                    sx1, sy1, sx2, sy2 = det.bbox
                    x1 = int(sx1 / ANALYSIS_SCALE)
                    y1 = int(sy1 / ANALYSIS_SCALE)
                    x2 = int(sx2 / ANALYSIS_SCALE)
                    y2 = int(sy2 / ANALYSIS_SCALE)
                    if sim >= MATCH_THRESHOLD:
                        owner_seen = True
                    else:
                        face_cx = (x1 + x2) / 2.0
                        side = "left" if face_cx < frame_w / 2.0 else "right"
                        intruder_sides.append(side)
                # A face was present this analysis frame - keep counting how
                # many in a row so a single ghost detection can't arm a lock.
                person_run += 1

                if owner_seen:
                    # Recognized owner -> never lock; cancel any pending window.
                    # Notify only on a real return (owner was genuinely away
                    # for a while) and never more often than the cooldown
                    # allows - this stops the toast repeating on flicker.
                    was_away = now - self._owner_seen_at >= GREETING_MIN_ABSENCE_SECONDS
                    cooldown_ok = now - self._last_greeting_at >= WELCOME_COOLDOWN_SECONDS
                    self._owner_seen_at = now
                    self._owner_left_at = now
                    guard_active = False
                    self._guard_announced = False
                    unknown_since = None
                    person_run = 0
                    self._set_status(
                        scene="owner",
                        faces_seen=faces_seen,
                        guard_active=False,
                        verify_started_at=None,
                    )
                    if was_away and cooldown_ok:
                        self._face_match_pending = True
                        self._last_greeting_at = now
                elif now - self._owner_left_at < OWNER_LEFT_GRACE_SECONDS:
                    # Owner was seen very recently - probably still at the desk,
                    # just not matched in this exact frame. Do not lock yet.
                    self._guard_announced = False
                    self._set_status(
                        scene="owner",
                        faces_seen=faces_seen,
                        guard_active=False,
                        verify_started_at=None,
                    )
                else:
                    # Owner is gone and at least one unrecognized face is in
                    # view. Start / keep a face-reading window: keep re-checking
                    # every frame; only lock if it never matches the owner for
                    # the full UNKNOWN_VERIFY_SECONDS window.
                    guard_active = True
                    if now < self._post_lock_cooldown_until:
                        # Just unlocked - give the owner a moment to be seen.
                        self._set_status(
                            scene="armed",
                            faces_seen=faces_seen,
                            guard_active=True,
                            verify_started_at=None,
                        )
                        time.sleep(0.02)
                        continue
                    if unknown_since is None and person_run >= PERSON_CONFIRM_FRAMES:
                        # The unrecognized face has now been present for
                        # several consecutive frames - a real person, not a
                        # one-off false positive. Start the verify countdown.
                        unknown_since = now
                    if intruder_sides and unknown_since is not None and now - unknown_since >= UNKNOWN_VERIFY_SECONDS:
                        # The face never matched the owner during the whole window.
                        unknown_since = None
                        self._set_status(
                            scene="stranger",
                            faces_seen=faces_seen,
                            guard_active=True,
                            verify_started_at=None,
                        )
                        from app.stranger_voice import play_stranger_notice

                        # Say the warning exactly once per lock. It re-arms
                        # when the owner unlocks with the password, so the next
                        # stranger detection gets one fresh warning again.
                        if self._stranger_notice_armed:
                            self._stranger_notice_armed = False
                            play_stranger_notice()
                        # Password overlay if one is set, otherwise the REAL
                        # Windows lock - a missing password must NEVER mean a
                        # stranger walks away un-locked.
                        self._lock_for_stranger()
                        self._post_lock_cooldown_until = now + 8.0
                    else:
                        # Still reading the face - countdown shown in the UI.
                        self._set_status(
                            scene="verifying",
                            faces_seen=faces_seen,
                            guard_active=True,
                            verify_started_at=unknown_since,
                        )

                elapsed_loop = time.monotonic() - t0
                if elapsed_loop < ENGINE_LOOP_INTERVAL_SECONDS:
                    time.sleep(ENGINE_LOOP_INTERVAL_SECONDS - elapsed_loop)

            if self._voice_enabled:
                # Keep the camera hardware open so a voice toggle next frame is
                # instant; the inner loop re-enters because voice is still ON.
                self._set_status(
                    scene="disabled",
                    faces_seen=0,
                    guard_active=False,
                    last_error=None,
                    verify_started_at=None,
                )
                continue
            if cap:
                self._stop_frame_pump()
                cap = None
            self._set_camera_active(False)

        self._stop_frame_pump()
        cap = None
        self._set_camera_active(False)
        self._set_status(running=False, scene="no_camera")


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def build_embedding_from_bgr(image_bgr: np.ndarray) -> np.ndarray | None:
    if not face_ai.dependencies_installed():
        return None
    try:
        return face_ai.embedding_from_bgr(image_bgr)
    except Exception:
        return None
