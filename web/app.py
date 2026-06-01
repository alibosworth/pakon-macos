"""
Pakon Scanner web service.

Run from the repo root:
    uvicorn web.app:app --host 0.0.0.0 --port 8000

The server must be run with USB access (root on Linux, or with a udev rule
granting access to 0f05:f135 / 0f05:f235).  On macOS no privileges are
needed.

Endpoints
---------
GET  /api/status                  scanner state + current job info
POST /api/firmware                SSE — load firmware (cold device)
POST /api/scan                    SSE — run a scan
POST /api/process                 SSE — decode .raw (upload or last scan)
GET  /api/frames                  list decoded frames
GET  /api/frames/{n}/thumb        JPEG thumbnail
GET  /api/frames/{n}/tiff         16-bit TIFF download
GET  /api/export                  zip of all TIFFs
"""
import asyncio
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .decode import WORK_DIR, decode_raw

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO = Path(__file__).resolve().parent.parent
_BUILD = Path(os.environ.get("PAKON_BUILD", str(_REPO / "build")))
_RES = Path(os.environ.get("PAKON_RESOURCES", str(_REPO / "resources")))

PROBE_BIN  = _BUILD / "pakon_probe"
REPLAY_BIN = _BUILD / "pakon_replay"
PAKFW      = _RES / "f135.pakfw"
PAKSCAN    = _RES / "scan_fullroll.pakscan"

# ── App & state ───────────────────────────────────────────────────────────────

app = FastAPI(title="Pakon Scanner")

_executor = ThreadPoolExecutor(max_workers=1)
_state: dict = {
    "scan_path": None,   # Path to the last .raw file
    "frames": [],        # list of {"index", "tiff", "thumb"}
    "processing": False,
}


# ── Scanner detection ─────────────────────────────────────────────────────────

def _scanner_state() -> str:
    try:
        import usb.core  # pyusb
        if usb.core.find(idVendor=0x0F05, idProduct=0xF135) is not None:
            return "warm"
        if usb.core.find(idVendor=0x0F05, idProduct=0xF235) is not None:
            return "cold"
        return "disconnected"
    except Exception:
        return "unknown"


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    sp = _state["scan_path"]
    return {
        "state": _scanner_state(),
        "scan_file": sp.name if sp else None,
        "scan_bytes": sp.stat().st_size if sp and sp.exists() else 0,
        "frame_count": len(_state["frames"]),
        "processing": _state["processing"],
    }


# ── Firmware load ─────────────────────────────────────────────────────────────

async def _firmware_stream():
    for path, label in [(PROBE_BIN, "pakon_probe binary"), (PAKFW, "firmware file")]:
        if not path.exists():
            yield _sse({"type": "error", "message": f"{label} not found: {path}"})
            return

    proc = await asyncio.create_subprocess_exec(
        str(PROBE_BIN), "--load-firmware", str(PAKFW),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async for line in proc.stdout:
        yield _sse({"type": "log", "message": line.decode().rstrip()})
    await proc.wait()
    if proc.returncode == 0:
        yield _sse({"type": "done"})
    else:
        yield _sse({"type": "error",
                    "message": f"firmware load failed (exit {proc.returncode})"})


@app.post("/api/firmware")
async def api_firmware():
    return StreamingResponse(_firmware_stream(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


# ── Scan ──────────────────────────────────────────────────────────────────────

async def _scan_stream():
    for path, label in [(REPLAY_BIN, "pakon_replay binary"), (PAKSCAN, "scan script")]:
        if not path.exists():
            yield _sse({"type": "error", "message": f"{label} not found: {path}"})
            return

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out_path = WORK_DIR / "scan.raw"
    _state["scan_path"] = out_path
    _state["frames"] = []

    # Remove stale output so size polling starts from 0.
    if out_path.exists():
        out_path.unlink()

    proc = await asyncio.create_subprocess_exec(
        str(REPLAY_BIN), "--scan", str(PAKSCAN), "--image", str(out_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    while True:
        done = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.5)
            done = True
        except asyncio.TimeoutError:
            pass

        size = out_path.stat().st_size if out_path.exists() else 0
        mb = round(size / 1_048_576, 1)
        yield _sse({"type": "progress", "bytes": size, "mb": mb})
        if done:
            break

    size = out_path.stat().st_size if out_path.exists() else 0
    mb = round(size / 1_048_576, 1)
    if proc.returncode == 0:
        yield _sse({"type": "done", "bytes": size, "mb": mb})
    else:
        yield _sse({"type": "error",
                    "message": f"scan failed (exit {proc.returncode})"})


@app.post("/api/scan")
async def api_scan():
    return StreamingResponse(_scan_stream(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


# ── Process ───────────────────────────────────────────────────────────────────

@app.post("/api/process")
async def api_process(
    file: UploadFile | None = File(default=None),
    frames: int = Form(default=36),
    rotate: int = Form(default=90),
    resample: str = Form(default=""),
):
    if _state["processing"]:
        async def _busy():
            yield _sse({"type": "error", "message": "Already processing"})
        return StreamingResponse(_busy(), media_type="text/event-stream",
                                 headers=_SSE_HEADERS)

    # Resolve raw file path.
    if file is not None:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = WORK_DIR / "upload.raw"
        with open(upload_path, "wb") as f:
            while chunk := await file.read(1 << 16):
                f.write(chunk)
        raw_path = upload_path
        _state["scan_path"] = upload_path
    elif _state["scan_path"] and _state["scan_path"].exists():
        raw_path = _state["scan_path"]
    else:
        async def _no_file():
            yield _sse({"type": "error",
                        "message": "No raw file — run a scan or upload a .raw file"})
        return StreamingResponse(_no_file(), media_type="text/event-stream",
                                 headers=_SSE_HEADERS)

    resample_size: tuple[int, int] | None = None
    if resample:
        try:
            rw, rh = (int(v) for v in resample.lower().split("x"))
            resample_size = (rw, rh)
        except ValueError:
            pass

    _state["frames"] = []
    _state["processing"] = True

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _progress(step: str, pct: float) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait, {"type": "progress", "step": step, "pct": pct}
        )

    def _run() -> None:
        try:
            result = decode_raw(raw_path, n_frames=frames, rotate=rotate,
                                resample=resample_size, progress=_progress)
            _state["frames"] = result
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": "done", "count": len(result)}
            )
        except Exception as exc:
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": "error", "message": str(exc)}
            )

    _executor.submit(_run)

    async def _stream():
        try:
            while True:
                event = await queue.get()
                yield _sse(event)
                if event["type"] in ("done", "error"):
                    break
        finally:
            _state["processing"] = False

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


# ── Frame access ──────────────────────────────────────────────────────────────

@app.get("/api/frames")
async def api_frames():
    return [{"index": f["index"]} for f in _state["frames"]]


@app.get("/api/frames/{n}/thumb")
async def api_thumb(n: int):
    match = next((f for f in _state["frames"] if f["index"] == n), None)
    if not match or not match["thumb"].exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(match["thumb"], media_type="image/jpeg")


@app.get("/api/frames/{n}/tiff")
async def api_tiff(n: int):
    match = next((f for f in _state["frames"] if f["index"] == n), None)
    if not match or not match["tiff"].exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(
        match["tiff"],
        media_type="image/tiff",
        filename=f"frame_{n:02d}.tif",
    )


# ── Zip export ────────────────────────────────────────────────────────────────

@app.get("/api/export")
async def api_export():
    frames = _state["frames"]
    if not frames:
        return JSONResponse({"error": "no frames to export"}, status_code=404)

    zip_path = WORK_DIR / "export.zip"

    def _make_zip() -> Path:
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for f in frames:
                zf.write(str(f["tiff"]), f"frame_{f['index']:02d}.tif")
        return zip_path

    await asyncio.get_event_loop().run_in_executor(None, _make_zip)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="pakon_frames.zip",
    )


# ── Static files (must be last) ───────────────────────────────────────────────

app.mount(
    "/",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="static",
)
