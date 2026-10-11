from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.Lock()
_playing = False


def play_wav(path: str | Path) -> bool:
    """Play a WAV file once through the speakers, non-blocking.

    Only a single sound plays at a time - if one is already playing (or the
    file is missing) the new request is silently skipped. Returns True when
    playback was started.
    """
    global _playing
    with _lock:
        if _playing:
            return False
        p = Path(path)
        if not p.exists():
            return False
        _playing = True

    def _run() -> None:
        global _playing
        try:
            import winsound

            winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        except Exception:
            pass
        finally:
            with _lock:
                _playing = False

    threading.Thread(target=_run, daemon=True).start()
    return True