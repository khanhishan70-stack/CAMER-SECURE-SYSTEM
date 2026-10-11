"""Offline voice command detection using Vosk (no internet required).

Listens on the default microphone and recognises a small set of spoken
commands in English:

* "camera on"   -> turn Security Mode / camera ON (the name may come first:
                   "Esta camera on" works too)
* "camera off"  -> turn Security Mode / camera OFF ("Esta camera off" works too)
* "voice on" / "voice off"
                   -> toggle Voice Control itself ("Esta voice on/off" also
                      works; only audible while the mic is already listening)
* "<name> lock pc" ("Esta lock pc" / "Esta lock screen" / "Esta lock")
                   -> lock the PC. The wake name must be said first; a plain
                      "lock screen" alone never locks.

Speech recognition runs fully locally via the Vosk small English model that
ships under ``data/models/``. The microphone is only opened while ``start()``
is in effect; ``stop()`` releases the stream and the thread. Unknown speech is
simply ignored - only the three commands above take any action.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Callable
from pathlib import Path

import numpy as np

from app.config import VOICE_SAMPLE_RATE, VOICE_MODEL_PATH

# Command phrase -> the single callback it triggers. Matching is word-boundary
# only (never substring): "clock"/"block"/"unlock" must NOT count as "lock".
# The wake name is optional before camera commands ("Esta camera on" works,
# plain "camera on" still works too).
_RE_CAMERA_ON = re.compile(
    r"\b(?:esta\s+)?(?:camera on|turn camera on|turn on the camera|camera on now|cam on|turn on camera|camera start)\b"
)
_RE_CAMERA_OFF = re.compile(
    r"\b(?:esta\s+)?(?:camera off|turn camera off|turn off the camera|camera off now|cam off|turn off camera|switch off camera|camera stop|camera disable)\b"
)
# Locking is wake-name gated: the PC only locks when the name is said first,
# followed by a lock phrase ("Esta lock pc" / "Esta lock screen" / "Esta lock").
# A plain "lock screen" alone does NOT lock - this stops accidental triggers.
_WAKE_NAME = "esta"
_RE_LOCK = re.compile(r"\b" + _WAKE_NAME + r"\s+lock\b")
# Toggle Voice Control itself (only audible while the mic is already
# listening). The name is optional ("Esta voice on" or plain "voice on").
_RE_VOICE_ON = re.compile(r"\b(?:esta\s+)?(?:voice on|voice ok|turn voice on|voice control on)\b")
_RE_VOICE_OFF = re.compile(r"\b(?:esta\s+)?(?:voice off|turn voice off|voice control off)\b")
# How many consecutive matching partial frames confirm a real spoken command
# before we act. A genuine command stays stable for many frames (~50ms each);
# forced-grammar noise flickers for a single frame and gets ignored.
_CONFIRM_FRAMES = 3


def dependencies_available() -> bool:
    try:
        import vosk  # noqa: F401
        return True
    except Exception:
        return False


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "no default input device" in low or "invalid default device" in low or "error querying device" in low:
        return "Microphone not available - connect a microphone and try again."
    if "access" in low or "permission" in low or "not allowed" in low:
        return "Microphone permission denied - allow microphone access."
    if "unplugged" in low or "disconnected" in low:
        return "Microphone disconnected."
    if "model" in low and ("path" in low or "load" in low or "not found" in low):
        return f"Speech model missing - place it under {VOICE_MODEL_PATH}."
    return f"Voice error: {text or type(exc).__name__}"


def _dbg(msg: str) -> None:
    if os.environ.get("VOICE_DEBUG"):
        print(f"[voice {time.monotonic():.2f}] {msg}", flush=True)


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace/punctuation for fuzzy matching."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _match_command(text: str) -> str | None:
    norm = _normalise(text)
    if not norm:
        return None
    if _RE_CAMERA_ON.search(norm):
        return "camera_on"
    if _RE_CAMERA_OFF.search(norm):
        return "camera_off"
    if _RE_VOICE_ON.search(norm):
        return "voice_on"
    if _RE_VOICE_OFF.search(norm):
        return "voice_off"
    if _RE_LOCK.search(norm):
        return "lock"
    return None


class VoiceCommandDetector:
    """Detects spoken commands on the default microphone via Vosk.

    ``on_camera_on`` / ``on_camera_off`` / ``on_lock`` fire when the matching
    phrase is recognised. ``on_unknown`` fires (throttled) when speech was
    heard that did not match a command, so the UI can hint at the valid
    commands without spamming.
    """

    def __init__(
        self,
        on_camera_on: Callable[[], None],
        on_camera_off: Callable[[], None],
        on_lock: Callable[[], None],
        on_voice_on: Callable[[], None] | None = None,
        on_voice_off: Callable[[], None] | None = None,
        on_unknown: Callable[[], None] | None = None,
        sample_rate: int = VOICE_SAMPLE_RATE,
    ) -> None:
        self._on_camera_on = on_camera_on
        self._on_camera_off = on_camera_off
        self._on_lock = on_lock
        self._on_voice_on = on_voice_on
        self._on_voice_off = on_voice_off
        self._on_unknown = on_unknown
        self._sample_rate = sample_rate
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state = "inactive"  # inactive | active | error
        self._error: str | None = None
        self._model_path = Path(VOICE_MODEL_PATH)
        self._model = None
        self._recognizer = None
        self._last_unknown_at = 0.0
        self._level = 0.0
        self._last_cmd_at: dict[str, float] = {}
        # Consecutive-matching partial state (guards against noise triggers).
        self._partial_cmd: str | None = None
        self._partial_streak = 0
        # Wake-only mode: the microphone stays listening but ONLY the "voice
        # on" (wake) command is acted on - so "voice on" can re-enable voice
        # control even while it is turned off.
        self._wake_only = False

    # -- public API --------------------------------------------------------

    def start(self) -> tuple[bool, str | None]:
        """Open the microphone and begin listening. Returns (ok, error)."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                _dbg("start: already alive")
                return True, None
            if not dependencies_available():
                self._state = "error"
                self._error = "Speech library missing - run 'pip install vosk' and restart."
                return False, self._error
            try:
                self._load_model()
                _dbg("start: model loaded")
            except Exception as e:  # noqa: BLE001
                self._state = "error"
                self._error = _friendly_error(e)
                _dbg(f"start: model error {self._error}")
                return False, self._error
            self._stop.clear()
            self._state = "active"
            self._error = None
            self._thread = threading.Thread(
                target=self._run, name="VoiceCommand", daemon=True
            )
            self._thread.start()
            return True, None

    def stop(self) -> None:
        """Release the microphone and stop the listening thread."""
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
            if t.is_alive():
                self._thread = t
            else:
                self._thread = None
        self._state = "inactive"
        self._error = None

    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def state(self) -> str:
        return self._state

    @property
    def error(self) -> str | None:
        return self._error

    def set_model_path(self, path: str) -> None:
        self._model_path = Path(path)

    def set_wake_only(self, wake_only: bool) -> None:
        """When True, only the "voice on" wake command is acted on."""
        with self._lock:
            self._wake_only = bool(wake_only)

    def get_level(self) -> float:
        """Current microphone loudness in 0..1, refreshed each audio block."""
        return self._level

    # -- internals ---------------------------------------------------------

    def _load_model(self) -> None:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        if self._model is None:
            if not self._model_path.exists():
                raise RuntimeError(f"model path {self._model_path} does not exist")
            self._model = Model(str(self._model_path))
        # A small command vocabulary focused on our commands improves
        # reliability and cuts latency versus free-form dictation. The plain
        # "lock screen"/"lock" phrases stay in the grammar so Vosk hears them
        # exactly, but the engine only locks when the wake name precedes them.
        self._recognizer = KaldiRecognizer(
            self._model,
            self._sample_rate,
            '["camera on", "camera off", "esta camera on", "esta camera off", '
            '"voice on", "voice off", "esta voice on", "esta voice off", '
            '"lock screen", "lock", '
            '"esta lock pc", "esta lock screen", "esta lock", "[unk]"]',
        )
        self._recognizer.SetWords(False)

    def _run(self) -> None:
        try:
            import sounddevice as sd
            from vosk import SetLogLevel

            SetLogLevel(-1)
            info = sd.query_devices(kind="input")
            _dbg(f"run: query input -> {info}")
            if not isinstance(info, dict) or info.get("index") in (None, -1):
                raise RuntimeError("no default input device configured")
            device = int(info.get("index"))
            block = max(256, int(self._sample_rate * 0.05))
            _dbg(f"run: opening InputStream device={device} sr={self._sample_rate} block={block}")
            stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                blocksize=block,
                device=device,
            )
            with stream:
                self._state = "active"
                self._error = None
                _dbg("run: stream open, listening")
                while not self._stop.is_set():
                    data, _overflowed = stream.read(block)
                    if self._stop.is_set():
                        break
                    samples = np.asarray(data).reshape(-1)
                    if samples.size:
                        self._level = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)) / 32768.0)
                    self._feed(samples.tobytes())
        except Exception as e:  # noqa: BLE001
            self._state = "error"
            self._error = _friendly_error(e)
            _dbg(f"run: EXCEPTION {self._error}")

    def _feed(self, pcm: bytes) -> None:
        rec = self._recognizer
        if rec is None:
            return
        if rec.AcceptWaveform(pcm):
            # finalised utterance
            try:
                import json

                res = json.loads(rec.Result())
                text = res.get("text", "")
            except Exception:
                text = ""
            _dbg(f"feed: FINAL text={text!r}")
            if text:
                self._handle_text(text)
        else:
            # Not finalised yet. We can act on partials when a command stays
            # stable across several consecutive frames - that means a real
            # utterance, not forced-grammar noise flickering for one frame.
            try:
                import json

                res = json.loads(rec.PartialResult())
                part = res.get("partial", "") or ""
            except Exception:
                part = ""
            if part:
                _dbg(f"feed: partial={part!r}")
            cmd = _match_command(part) if part else None
            if cmd is not None:
                if cmd == self._partial_cmd:
                    self._partial_streak += 1
                else:
                    self._partial_cmd = cmd
                    self._partial_streak = 1
                _dbg(f"feed: partial streak {self._partial_cmd} x{self._partial_streak}")
                if self._partial_streak >= _CONFIRM_FRAMES:
                    _dbg(f"feed: CONFIRMED PARTIAL {part!r} x{self._partial_streak}")
                    self._partial_cmd = None
                    self._partial_streak = 0
                    self._emit_command(part)
                    # Flush the recognizer so the same phrase neither re-fires
                    # on the trailing silence nor double-fires on the final.
                    try:
                        rec.Reset()
                    except Exception:
                        pass
            else:
                # Speech moved on or a non-command partial arrived - any
                # in-progress confirmation streak is no longer trustworthy.
                self._partial_cmd = None
                self._partial_streak = 0

    def _handle_text(self, text: str) -> None:
        self._emit_command(text)
        if _match_command(text) is None:
            self._notify_unknown()

    def _emit_command(self, text: str) -> None:
        cmd = _match_command(text)
        if cmd is None:
            return
        if self._wake_only and cmd != "voice_on":
            # Voice control is off - ignore everything except the wake command.
            return
        # The same phrase can appear in the partial result, then again when
        # Vosk finalizes it. Cooldown per command stops the double-fire.
        now = time.monotonic()
        last = self._last_cmd_at.get(cmd, 0.0)
        if now - last < 1.5:
            return
        self._last_cmd_at[cmd] = now
        if cmd == "camera_on":
            self._emit(self._on_camera_on)
        elif cmd == "camera_off":
            self._emit(self._on_camera_off)
        elif cmd == "lock":
            self._emit(self._on_lock)
        elif cmd == "voice_on":
            self._emit(self._on_voice_on)
        elif cmd == "voice_off":
            self._emit(self._on_voice_off)

    def _emit(self, handler: Callable[[], None] | None) -> None:
        if handler is None:
            return
        try:
            handler()
        except Exception:
            pass

    def _notify_unknown(self) -> None:
        if not self._on_unknown:
            return
        now = time.monotonic()
        if now - self._last_unknown_at < 2.0:
            return
        self._last_unknown_at = now
        try:
            self._on_unknown()
        except Exception:
            pass
