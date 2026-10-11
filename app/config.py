from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Current app version. Bumped on every release - the in-app updater compares
# this against the latest GitHub release tag to offer one-click updates.
APP_VERSION = "1.0.2"
UPDATE_REPO = "khanhishan70-stack/CAMER-SECURE-SYSTEM"
UPDATE_CHECK_HOURS = 6.0

EMBEDDING_FILE = DATA_DIR / "face_embedding.enc"
SALT_FILE = DATA_DIR / ".salt"

ANALYSIS_INTERVAL_MS = 500
MATCH_THRESHOLD = 0.38
# How long an unrecognized face must stay in front of the camera before the
# screen locks. During this window every frame is re-checked against the owner's
# enrollment - as soon as the owner is recognized, locking is cancelled.
UNKNOWN_VERIFY_SECONDS = 5.0
CAMERA_INDEX = 0
# Smooth live preview: the camera is opened at 720p/30fps on purpose. Full
# 1080p triples the CPU cost of every frame (capture, downscale, face AI) and
# is what made the preview lag and glitch - 720p is still plenty sharp for
# face recognition and looks crisp in the app's preview panel.
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FPS = 30
PREVIEW_INTERVAL_SECONDS = 0.25
# How often a fresh preview JPEG is encoded from the newest camera frame.
# The capture pump keeps frames flowing at camera speed, so the preview no
# longer waits on the heavy face-AI pass (that was the old 2-3 fps lag).
PREVIEW_EVERY_SECONDS = 0.12
# Inner engine loop tick: with frames arriving on a background thread, the AI
# loop can spin fast (~10 Hz) so the preview, the face boxes and the guard
# logic all stay live and fluid.
ENGINE_LOOP_INTERVAL_SECONDS = 0.1
ENGINE_TICK_WAIT_SECONDS = 0.08
ANALYSIS_SCALE = 1.0
PREVIEW_QUALITY = 85
PREVIEW_MAX_WIDTH = 640

# Voice command control (offline Vosk speech recognition)
VOICE_SAMPLE_RATE = 16000
VOICE_MODEL_PATH = str(DATA_DIR / "models" / "vosk-model-small-en-us-0.15")
# Persistent voice/mic state (survives app restarts): the mic MUTE flag is
# saved here so a fully-muted microphone stays muted until the user clicks
# the unmute button - the AI never silently re-opens someone's mic.
VOICE_STATE_FILE = DATA_DIR / "voice_state.json"

# Spoken warning played when a stranger is detected while Security Mode is ON.
VOICE_NOTICE_PATH = DATA_DIR / "voice" / "stranger_notice.wav"
# Short spoken confirmation ("Done.") after a camera-command is actioned.
VOICE_DONE_PATH = DATA_DIR / "voice" / "done.wav"
# Spoken "Guard active." announcement right after the owner leaves and the
# security guard arms itself over the (now empty) desk.
VOICE_GUARD_PATH = DATA_DIR / "voice" / "guard_active.wav"
# Minimum gap between two "Guard active." announcements, and the empty-scene
# arm must hold at least this many frames with the owner genuinely away before
# the phrase is spoken (prevents flicker-triggered announcements).
GUARD_ANNOUNCE_COOLDOWN_SECONDS = 20.0
