"""
Build SecureCam into a standalone Windows executable.
Usage:  python build_exe.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent

for d in [PROJECT / "build", PROJECT / "dist" / "SecureCam"]:
    if d.exists():
        shutil.rmtree(d)

model_dir = Path.home() / ".insightface" / "models" / "buffalo_l"
model_files = sorted(str(p) for p in model_dir.rglob("*.onnx")) if model_dir.is_dir() else []
if model_files:
    print(f"Found {len(model_files)} insightface model files")
else:
    print("Warning: buffalo_l model files not found! Face detection will not work.")

# Locate insightface site-package for --add-data bundling
import site
_insightface_site = None
for _p in site.getsitepackages() + [site.getusersitepackages()]:
    _candidate = Path(_p) / "insightface"
    if _candidate.is_dir():
        _insightface_site = _candidate
        break
if not _insightface_site:
    _insightface_site = Path.home() / "AppData" / "Roaming" / "Python" / "Python314" / "site-packages" / "insightface"
assert _insightface_site and _insightface_site.is_dir(), f"Cannot find insightface at {_insightface_site}"
print(f"Bundling insightface from: {_insightface_site}")

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--name", "SecureCam",
    "--onedir",
    "--noconsole",
    "--add-data", f"{PROJECT / 'static'};static",
    "--add-data", f"{PROJECT / 'app'};app",
    "--hidden-import", "app.main",
    "--hidden-import", "app.config",
    "--hidden-import", "app.camera_utils",
    "--hidden-import", "app.face_ai",
    "--hidden-import", "app.face_store",
    "--hidden-import", "app.password_store",
    "--hidden-import", "app.security_engine",
    "--hidden-import", "app.voice_commands",
    "--hidden-import", "sounddevice",
    "--hidden-import", "vosk",
    "--add-data", f"{PROJECT / 'data' / 'models' / 'vosk-model-small-en-us-0.15'};data/models/vosk-model-small-en-us-0.15",
    "--hidden-import", "app.lock_screen",
    "--hidden-import", "app.windows_lock",
    "--hidden-import", "onnxruntime",
    "--collect-data", "onnxruntime",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--exclude-module", "torch",
    "--exclude-module", "tensorflow",
    "--exclude-module", "jax",
    "--exclude-module", "sklearn",
    "--exclude-module", "skimage",
    "--exclude-module", "matplotlib",
    "--exclude-module", "sympy",
    "--exclude-module", "tables",
    "--exclude-module", "PIL.ImageQt",
    "--exclude-module", "insightface",
    "--add-data", f"{_insightface_site};insightface",
]

for mf in model_files:
    cmd.extend(["--add-data", f"{mf};.insightface/models/buffalo_l"])

cmd.append(str(PROJECT / "run.py"))

print("Building SecureCam.exe ...")
subprocess.check_call(cmd)

print(f"\nDone! EXE at: {PROJECT / 'dist' / 'SecureCam' / 'SecureCam.exe'}")
