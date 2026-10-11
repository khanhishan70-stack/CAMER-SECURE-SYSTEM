"""Self-update system for CAMER SECURE SYSTEM.

Flow:
  1. Backend checks the latest GitHub release (cached, every few hours).
  2. UI shows an "Update available" popup -> user clicks Update now.
  3. Backend downloads the release ZIP with progress, extracts to staging,
     writes a small apply script, launches it detached, then exits itself.
  4. The apply script (plain .bat, runs outside the app):
       kills the app -> copies new files over (never touching the user's
       face embedding / password / mic state) -> re-runs SETUP-AND-RUN.bat
       in /auto mode (fresh dependencies, relaunches the app) -> writes a
       .just_updated flag -> cleans up.
  5. On next boot the backend sees the flag and the UI pops
     "Update complete".

Only active for packaged installs (detected via the SecureCam-win32-x64
layout). Dev/source checkouts report packaged=False.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"

# User-owned files that an update must NEVER overwrite or delete.
PERSONAL_FILES = [
    "face_embedding.enc",
    ".salt",
    "password.hash",
    "voice_state.json",
    ".just_updated",
]

JUST_UPDATED_NAME = ".just_updated"


def parse_version(tag: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(tag).strip())
    return tuple(int(p) for p in parts[:4]) or (0,)


def fetch_latest_release(repo: str, timeout: float = 12.0) -> dict | None:
    """Latest GitHub release metadata, or None when offline / on error."""
    try:
        req = urllib.request.Request(
            GITHUB_API.format(repo=repo),
            headers={
                "User-Agent": "CAMER-SECURE-SYSTEM-updater",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assets = data.get("assets") or []
        zip_asset = next(
            (a for a in assets if str(a.get("name", "")).lower().endswith(".zip")),
            None,
        )
        if not zip_asset:
            return None
        return {
            "tag": str(data.get("tag_name", "")),
            "notes": str(data.get("body", "") or ""),
            "url": str(zip_asset.get("browser_download_url", "")),
            "size": int(zip_asset.get("size", 0) or 0),
        }
    except Exception:
        return None


def check_for_update(current: str, repo: str) -> dict:
    """Compare current version against the latest release."""
    latest = fetch_latest_release(repo)
    if not latest or not latest["tag"] or not latest["url"]:
        return {"available": False, "current": current, "latest": "",
                "notes": "", "url": "", "size": 0}
    available = parse_version(latest["tag"]) > parse_version(current)
    return {"available": available, "current": current,
            "latest": latest["tag"], "notes": latest["notes"],
            "url": latest["url"], "size": latest["size"]}


def find_install_root(start: Path) -> Path | None:
    """Locate the install root (folder containing SecureCam-win32-x64/).

    Packaged layout: <root>/SecureCam-win32-x64/{SecureCam.exe,resources/}.
    Returns None for dev/source checkouts so they never self-modify.
    """
    p = start.resolve()
    for parent in [p, *p.parents]:
        if parent.name == "SecureCam-win32-x64":
            candidate = parent / "SecureCam.exe"
            if candidate.is_file():
                return parent.parent
    return None


def download_with_progress(url: str, dest: Path, cb=None) -> None:
    """Download url -> dest, calling cb(downloaded, total) along the way."""
    req = urllib.request.Request(url, headers={"User-Agent": "CAMER-SECURE-SYSTEM-updater"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        got = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if cb:
                cb(got, total)


def write_apply_script(stage_dir: Path, install_root: Path, version: str) -> Path:
    """Write the detached apply .bat. Returns its path.

    The script kills the app, copies the staged update over the install
    (personal files excluded), re-runs the bundled setup in /auto mode
    (refreshes dependencies + relaunches the app), stamps .just_updated
    so the UI can pop "Update complete", then cleans up staging.
    """
    app_dir = install_root / "SecureCam-win32-x64"
    data_flag = app_dir / "resources" / "data" / JUST_UPDATED_NAME
    xf = " ".join(PERSONAL_FILES)
    bat = f"""@echo off
setlocal EnableExtensions
title CAMER SECURE SYSTEM - applying update {version}
cd /d "%TEMP%"
set "LOG=%TEMP%\\css_update_{version}.log"
echo [%date% %time%] Stopping app for update >> "%LOG%"
taskkill /F /IM SecureCam.exe >> "%LOG%" 2>&1
timeout /t 5 /nobreak >nul
taskkill /F /IM SecureCam.exe >> "%LOG%" 2>&1
timeout /t 2 /nobreak >nul
echo [%date% %time%] Copying new files >> "%LOG%"
robocopy "{stage_dir}\\SecureCam-win32-x64" "{app_dir}" /E /NFL /NDL /NJH /NJS /R:3 /W:3 /XF {xf} >> "%LOG%" 2>&1
robocopy "{stage_dir}" "{install_root}" SETUP-AND-RUN.bat SETUP-CAMER-SECURE-SYSTEM.exe requirements.txt /NFL /NDL /NJH /NJS /R:3 /W:3 >> "%LOG%" 2>&1
echo [%date% %time%] Refreshing dependencies + relaunching >> "%LOG%"
call "{stage_dir}\\SETUP-AND-RUN.bat" /auto >> "%LOG%" 2>&1
echo {version} > "{data_flag}"
echo [%date% %time%] Cleaning up >> "%LOG%"
rmdir /S /Q "{stage_dir}" >> "%LOG%" 2>&1
exit /b 0
"""
    path = stage_dir.parent / f"apply_update_{version}.bat"
    path.write_text(bat, encoding="utf-8")
    return path


def read_just_updated(data_dir: Path) -> str | None:
    """One-shot read of the post-update flag (consumes it)."""
    flag = Path(data_dir) / JUST_UPDATED_NAME
    try:
        if flag.is_file():
            ver = flag.read_text(encoding="utf-8").strip()
            try:
                flag.unlink()
            except OSError:
                pass
            return ver or "?"
    except OSError:
        pass
    return None


def peek_just_updated(data_dir: Path) -> str | None:
    """Read the post-update flag WITHOUT consuming it (UI acks explicitly)."""
    try:
        flag = Path(data_dir) / JUST_UPDATED_NAME
        if flag.is_file():
            return flag.read_text(encoding="utf-8").strip() or "?"
    except OSError:
        pass
    return None


def ack_just_updated(data_dir: Path) -> None:
    try:
        Path(data_dir, JUST_UPDATED_NAME).unlink(missing_ok=True)
    except OSError:
        pass
