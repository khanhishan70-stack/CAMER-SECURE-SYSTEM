from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

HOST = "127.0.0.1"
PORT = 8844


def _debug_log(msg: str) -> None:
    try:
        with open(os.environ.get("SECURECAM_LOG", str(Path.home() / "securecam_debug.log")), "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def main() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    _debug_log(f"=== SecureCam startup ===")
    _debug_log(f"sys.path: {sys.path}")
    _debug_log(f"_MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}")

    for k in ("Insightface", "onnxruntime"):
        try:
            m = __import__(k.lower())
            _debug_log(f"{k} OK: {m.__file__}")
        except Exception as e:
            _debug_log(f"{k} FAIL: {e}")

    print(f"SecureCam started: http://{HOST}:{PORT}")
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
