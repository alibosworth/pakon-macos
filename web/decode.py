"""
Decode pipeline for the Pakon web service.

Imports the core functions from tools/pakon_image.py and wraps them with
a progress callback suitable for async SSE streaming.  All I/O goes to
WORK_DIR (/tmp/pakon_web); callers can read .tif and .jpg files from there.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

# Pull the decode functions from the CLI tool without installing it.
_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from pakon_image import (  # noqa: E402
    autocrop,
    find_frame_boundaries,
    find_ir_band,
    measure_leads,
    register_zones,
)

WORK_DIR = Path("/tmp/pakon_web")
_LINEWIDTH = 8000


def decode_raw(
    raw_path,
    n_frames: int = 36,
    rotate: int = 90,
    resample: tuple[int, int] | None = None,
    progress=None,
) -> list[dict]:
    """
    Decode a Pakon .raw file into per-frame TIFFs and JPEG thumbnails.

    Args:
        raw_path:  path to the .raw scan file
        n_frames:  number of frames to split the ribbon into
        rotate:    degrees CW to rotate each frame (0 / 90 / 180 / 270)
        resample:  optional (width, height) to resample output via ImageMagick
        progress:  optional callable(step: str, pct: float 0–1)

    Returns:
        list of {"index": int, "tiff": Path, "thumb": Path}
    """
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    def emit(step: str, pct: float) -> None:
        if progress:
            progress(step, round(pct, 3))

    # ── Load ─────────────────────────────────────────────────────────────────
    emit("Loading", 0.0)
    raw = np.memmap(str(raw_path), dtype="<u2", mode="r")
    lw = _LINEWIDTH
    lines = raw.size // lw
    img = raw[: lines * lw].reshape(lines, lw)
    n3 = (lw // 3) * 3
    # Interleave order confirmed as B=0, R=1, G=2 on this F-135.
    chans = {
        "r": img[:, 1:n3:3],
        "g": img[:, 2:n3:3],
        "b": img[:, 0:n3:3],
    }
    sampled = [chans[c][::997] for c in ("r", "g", "b")]
    full = float(max(a.max() for a in sampled)) or 1.0
    width = chans["r"].shape[1]

    # ── IR band & zones ───────────────────────────────────────────────────────
    emit("Finding IR band", 0.05)
    ir = find_ir_band(chans, full)
    if ir:
        ir0, ir1 = ir
        zones = [z for z in [(ir1 + 1, width), (0, ir0)] if z[1] > z[0]]
    else:
        zones = [(0, width)]

    # ── Trilinear registration ────────────────────────────────────────────────
    emit("Measuring registration leads", 0.15)
    leads_per_zone = [measure_leads(chans, c0, c1) for c0, c1 in zones]

    emit("Registering channels", 0.35)
    rgb = register_zones(chans, zones, leads_per_zone)

    # ── Autocrop ──────────────────────────────────────────────────────────────
    emit("Autocropping", 0.50)
    rgb, _ = autocrop(rgb)

    # ── Frame boundaries ──────────────────────────────────────────────────────
    emit("Finding frame boundaries", 0.57)
    if n_frames > 1:
        boundaries = find_frame_boundaries(rgb, n_frames)
        split_rows = [0] + boundaries + [rgb.shape[0]]
    else:
        split_rows = [0, rgb.shape[0]]

    # ── Export frames ─────────────────────────────────────────────────────────
    rot_k = (rotate // 90) % 4
    frames = []

    for i in range(n_frames):
        emit(f"Exporting frame {i + 1}/{n_frames}", 0.60 + 0.40 * i / n_frames)

        part = rgb[split_rows[i] : split_rows[i + 1]]
        if rot_k:
            part = np.rot90(part, k=rot_k)
        part = np.ascontiguousarray(part)

        tiff_path = WORK_DIR / f"frame_{i + 1:02d}.tif"

        if resample:
            rw, rh = resample
            h, w, _ = part.shape
            fd, tmp = tempfile.mkstemp(suffix=".rgb")
            os.close(fd)
            part.astype(">u2").tofile(tmp)
            try:
                subprocess.run(
                    [
                        "magick",
                        "-size", f"{w}x{h}",
                        "-depth", "16",
                        "-endian", "msb",
                        f"rgb:{tmp}",
                        "-filter", "Lanczos",
                        "-resize", f"{rw}x{rh}!",
                        "-depth", "16",
                        str(tiff_path),
                    ],
                    check=True,
                )
            finally:
                os.unlink(tmp)
        else:
            tifffile.imwrite(str(tiff_path), part, photometric="rgb")

        # JPEG thumbnail — max 600 px on longest side, 8-bit
        step = max(1, max(part.shape[0] // 600, part.shape[1] // 600))
        thumb_arr = (part[::step, ::step] >> 8).astype(np.uint8)
        thumb_path = WORK_DIR / f"thumb_{i + 1:02d}.jpg"
        Image.fromarray(thumb_arr).save(str(thumb_path), "JPEG", quality=82)

        frames.append({"index": i + 1, "tiff": tiff_path, "thumb": thumb_path})

    emit("Done", 1.0)
    return frames
