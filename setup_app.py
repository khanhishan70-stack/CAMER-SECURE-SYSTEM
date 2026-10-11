"""CAMER SECURE SYSTEM - one-click setup (compiled to SETUP EXE).

Finds/installs Python 3.12, installs all AI packages, creates a Desktop
icon, launches the app, and prints how to use it. Stdlib only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request

SITE = "https://khanhishan70-stack.github.io/CAMER-SECURE-SYSTEM/"
PYTHON312_URL = (
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
)
WINGET_ID = "Python.Python.3.12"


def log(msg: str) -> None:
    print(msg, flush=True)


def root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_app(root: str) -> str | None:
    for cand in (
        os.path.join(root, "SecureCam-win32-x64", "SecureCam.exe"),
        os.path.join(root, "SecureCam.exe"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def py_version(exe: list[str]) -> str | None:
    try:
        out = subprocess.run(
            exe + ["--version"], capture_output=True, text=True, timeout=30
        )
        txt = (out.stdout or out.stderr or "").strip()  # "Python 3.12.9"
        parts = txt.split()
        if len(parts) == 2 and parts[0] == "Python":
            return parts[1]
    except Exception:
        pass
    return None


def is_312(ver: str | None) -> bool:
    return bool(ver) and ver.split(".")[:2] == ["3", "12"]


def find_python() -> list[str] | None:
    local = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), r"Programs\Python\Python312\python.exe"
    )
    for cand in (r"C:\Program Files\Python312\python.exe", local):
        if cand and os.path.isfile(cand):
            return [cand]
    for launcher in (["py", "-3.12"], ["python"]):
        if shutil.which(launcher[0]) and is_312(py_version(launcher)):
            return launcher
    return None


def install_python() -> list[str] | None:
    if shutil.which("winget"):
        log("Installing Python 3.12 via winget ...")
        subprocess.run(
            ["winget", "install", "-e", "--id", WINGET_ID, "--silent",
             "--accept-package-agreements", "--accept-source-agreements"],
        )
    else:
        installer = os.path.join(os.environ.get("TEMP", "."), "python312.exe")
        log("Downloading the Python 3.12 installer ...")
        urllib.request.urlretrieve(PYTHON312_URL, installer)
        log("Installing Python for this Windows user only, no admin needed ...")
        subprocess.run(
            [installer, "/quiet", "InstallAllUsers=0",
             "PrependPath=1", "Include_test=0"],
        )
        try:
            os.remove(installer)
        except OSError:
            pass
    return find_python()


def pip(py: list[str], *args: str) -> int:
    return subprocess.run(py + ["-m", "pip", *args]).returncode


def make_shortcut(app: str) -> None:
    ps = (
        "$w = New-Object -ComObject WScript.Shell; "
        "$d = $w.SpecialFolders('Desktop'); "
        f"$s = $w.CreateShortcut(\"$d\\CAMER SECURE SYSTEM.lnk\"); "
        f"$s.TargetPath = '{app}'; "
        f"$s.WorkingDirectory = '{os.path.dirname(app)}'; "
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", ps],
        capture_output=True,
    )


def main() -> int:
    auto = "--auto" in sys.argv[1:]
    print("=" * 60)
    print("  CAMER SECURE SYSTEM - one-click setup")
    print("  Installs everything automatically, then launches the app.")
    print("=" * 60 + "\n")

    root = root_dir()
    app = find_app(root)
    if not app:
        log("[ERROR] Could not find SecureCam.exe next to this file.")
        log("Keep this setup file inside the extracted download folder.")
        input("Press Enter to close ... ") if not auto else None
        return 1
    req = os.path.join(root, "requirements.txt")

    log("[1/4] Looking for Python 3.12 ...")
    py = find_python()
    if not py:
        log("Python 3.12 not found - installing it now, automatic ...")
        py = install_python()
    if not py:
        log("\n[ERROR] Python did not get installed. Easiest fix:")
        log("  1. Install Python 3.12 from https://www.python.org/downloads/")
        log("     (tick Add python.exe to PATH on the first screen)")
        log("  2. Re-run this setup.")
        input("Press Enter to close ... ") if not auto else None
        return 1
    log(f"Using: {' '.join(py)} ({py_version(py)})")

    log("\n[2/4] Installing the AI brain packages - one time, a few minutes ...")
    if pip(py, "install", "--upgrade", "pip") != 0:
        log("[WARN] pip upgrade failed, continuing anyway ...")
    if os.path.isfile(req):
        if pip(py, "install", "-r", req) != 0:
            log("\n[ERROR] Package install failed. Check internet and re-run.")
            input("Press Enter to close ... ") if not auto else None
            return 1
    else:
        log("[WARN] requirements.txt not found next to this file - skipping.")

    log("\n[3/4] Creating the Desktop icon ...")
    try:
        make_shortcut(app)
    except Exception as e:  # noqa: BLE001
        log(f"[WARN] Shortcut failed ({e}), continuing ...")

    log("\n[4/4] Launching CAMER SECURE SYSTEM ...")
    try:
        os.startfile(app)  # noqa: PGH003 - Windows only, this is a Windows setup
    except Exception:
        subprocess.Popen([app])

    print("\n" + "=" * 60)
    print("  DONE! The app is starting now.")
    print("  First start takes about 30 seconds - the face-AI model")
    print("  downloads itself once, so keep the internet on.")
    print("")
    print("  HOW TO LAUNCH NEXT TIME - no setup needed:")
    print('    - Double-click the Desktop icon "CAMER SECURE SYSTEM"')
    print("    - or double-click SecureCam.exe in this folder")
    print("")
    print("  HOW TO ACTIVATE - inside the app:")
    print("    1. Register your face - Capture from camera or Upload")
    print("    2. Set your lock screen password")
    print("    3. Switch Security Mode ON - guarding starts")
    print("")
    print(f"  Full illustrated guide:\n  {SITE}")
    print("=" * 60)
    if auto:
        return 0
    try:
        input("Press Enter to close ... ") if not auto else None
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
