from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.camera_utils import list_cameras
from app.face_store import clear_enrollment, has_enrollment, save_embedding
from app.password_store import clear_password, has_password, set_password
from app.security_engine import SecurityEngine, build_embedding_from_bgr

engine = SecurityEngine()


class _WsHub:
    def __init__(self) -> None:
        self._clients: list[WebSocket] = []
        self._stop_task: asyncio.Task[None] | None = None
        self._engine_lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)
        if self._stop_task and not self._stop_task.done():
            self._stop_task.cancel()
        async with self._engine_lock:
            if self._clients:
                engine.start()

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._clients:
            self._clients.remove(ws)
        if not self._clients:
            self._stop_task = asyncio.create_task(self._stop_engine_when_idle())

    async def _stop_engine_when_idle(self) -> None:
        try:
            await asyncio.sleep(1.5)
            async with self._engine_lock:
                if not self._clients and not engine.is_voice_enabled():
                    await asyncio.to_thread(engine.stop)
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        if self._stop_task and not self._stop_task.done():
            self._stop_task.cancel()
            try:
                await self._stop_task
            except asyncio.CancelledError:
                pass
        async with self._engine_lock:
            await asyncio.to_thread(engine.stop)

    async def broadcast(self, payload: dict) -> None:
        stale: list[WebSocket] = []
        for client in self._clients:
            try:
                await client.send_json(payload)
            except Exception:
                stale.append(client)
        for c in stale:
            self.disconnect(c)


hub = _WsHub()


async def _status_pump() -> None:
    while True:
        await asyncio.sleep(0.12)
        await hub.broadcast(engine.status_snapshot())


@asynccontextmanager
async def lifespan(app: FastAPI):
    pump = asyncio.create_task(_status_pump())
    await asyncio.to_thread(engine.start)
    yield
    pump.cancel()
    try:
        await pump
    except asyncio.CancelledError:
        pass
    await hub.shutdown()


app = FastAPI(title="SecureCam", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.middleware("http")
async def _no_store_static(request, call_next):
    """Never cache UI assets - the app is edited in place and browsers would
    otherwise keep serving stale app.js / index.html copies."""
    response = await call_next(request)
    if request.url.path.startswith(("/static/", "/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    index = static_dir / "index.html"
    if not index.is_file():
        raise HTTPException(500, "Missing static/index.html")
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/api/status")
async def api_status() -> dict:
    return engine.status_snapshot()


@app.get("/api/preview")
async def api_preview() -> Response:
    data = engine.get_preview_jpeg()
    if not data:
        raise HTTPException(status_code=503, detail="Preview not ready")
    return Response(content=data, media_type="image/jpeg")


@app.post("/api/setupcamera")
@app.post("/api/setup_camera")
@app.post("/api/capture_register")
async def api_capture_register() -> dict:
    ok, detail = await asyncio.to_thread(engine.request_capture_enroll)
    if not ok:
        raise HTTPException(status_code=400, detail=detail or "Enrollment failed")
    return {"ok": True, "enrolled": True}


@app.post("/api/register")
async def api_register(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Could not decode image - use JPG or PNG")

    emb = build_embedding_from_bgr(img)
    if emb is None:
        raise HTTPException(
            status_code=400,
            detail="No clear face found - face the camera, improve lighting, and try again.",
        )
    save_embedding(emb.astype(float).tolist())
    return {"ok": True, "enrolled": True}


@app.delete("/api/register")
async def api_register_delete() -> dict:
    clear_enrollment()
    return {"ok": True, "enrolled": False}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json(engine.status_snapshot())
        while True:
            await asyncio.sleep(3600)
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


@app.get("/api/meta")
async def api_meta() -> dict:
    return {"has_enrollment": has_enrollment(), "has_password": has_password()}


@app.get("/api/password")
async def api_password_status() -> dict:
    return {"has_password": has_password()}


@app.post("/api/password")
async def api_password_set(body: dict) -> dict:
    pwd = body.get("password", "")
    if len(pwd) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    set_password(pwd)
    return {"ok": True}


@app.delete("/api/password")
async def api_password_clear() -> dict:
    clear_password()
    return {"ok": True, "has_password": False}


@app.get("/api/intruder-warning")
async def api_intruder_warning_status() -> dict:
    return {"intruder_warning": engine.is_intruder_warning_enabled()}


@app.post("/api/intruder-warning")
async def api_intruder_warning_set(body: dict) -> dict:
    engine.set_intruder_warning(bool(body.get("enabled", True)))
    return {"ok": True, "intruder_warning": engine.is_intruder_warning_enabled()}


@app.get("/api/enable")
async def api_enable_status() -> dict:
    return {"enabled": engine.is_enabled()}


@app.post("/api/enable")
async def api_enable_set(body: dict) -> dict:
    engine.set_enabled(bool(body.get("enabled", True)))
    return {"ok": True, "enabled": engine.is_enabled()}


@app.get("/api/voice")
async def api_voice_status() -> dict:
    return engine.voice_status()


@app.post("/api/voice")
async def api_voice_set(body: dict) -> dict:
    ok, err = engine.set_voice_enabled(bool(body.get("enabled", True)))
    if not ok:
        raise HTTPException(status_code=400, detail=err or "Microphone unavailable")
    return engine.voice_status() | {"ok": True}


@app.post("/api/voice/mute")
async def api_voice_mute(body: dict) -> dict:
    """Fully mute/unmute the microphone (privacy).

    MUTED releases the mic completely - no wake word, no auto-restart - and
    the state is saved to disk so it stays muted even after an app restart.
    Only the user's own click on the UI can unmute it.
    """
    ok, err = engine.set_voice_muted(bool(body.get("muted", True)))
    if not ok:
        raise HTTPException(status_code=400, detail=err or "Microphone unavailable")
    return engine.voice_status() | {"ok": True}


@app.get("/api/cameras")
async def api_cameras_list() -> dict:
    devices = await asyncio.to_thread(list_cameras)
    current = engine.get_camera_source()
    return {"devices": devices, "current": current}


@app.post("/api/camera")
async def api_camera_select(body: dict) -> dict:
    raw = body.get("source") or body.get("index")
    if raw is None:
        raise HTTPException(status_code=400, detail="Missing 'source' or 'index'")
    if isinstance(raw, str) and raw.startswith(("http://", "https://")):
        engine.set_camera_source(raw)
    else:
        idx = int(raw)
        if idx < 0:
            raise HTTPException(status_code=400, detail="Invalid camera index")
        engine.set_camera_source(idx)
    return {"ok": True, "camera_source": engine.get_camera_source()}


@app.post("/api/shutdown")
async def api_shutdown() -> dict:
    engine.stop()
    return {"ok": True}


@app.post("/api/lock-now")
async def api_lock_now() -> dict:
    await asyncio.to_thread(engine.lock_now)
    return {"ok": True}


@app.get("/api/zoom")
async def api_zoom_get() -> dict:
    return {"zoom": engine.get_zoom()}


@app.post("/api/zoom")
async def api_zoom_set(body: dict) -> dict:
    z = float(body.get("zoom", 1.0))
    engine.set_zoom(z)
    return {"ok": True, "zoom": engine.get_zoom()}


if __name__ == "__main__":
    import uvicorn

    host = "127.0.0.1"
    port = 8844
    print(f"SecureCam is starting: http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
