from __future__ import annotations

from app.config import VOICE_NOTICE_PATH
from app.spoken_audio import play_wav


def play_stranger_notice() -> bool:
    """Play the "not the owner" girl-voice warning once (non-blocking)."""
    return play_wav(VOICE_NOTICE_PATH)