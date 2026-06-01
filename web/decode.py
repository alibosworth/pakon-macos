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
    find_frame_grid,
    find_ir_band,
    measure_leads,
    register_zones,
)

WORK_DIR = Path("/tmp/pakon_web")
_LINEWIDTH = 8000


def _preview_png(part16, out_path, maxdim: int = 600) -> None:
    """Write a viewable PNG preview from a 16-bit RGB negative.

    Basic positive: per-channel percentile stretch then invert. This is a
    *preview only* — it ignores the C-41 orange mask, so colour is approximate
    (same caveat as `pakon_image.py --invert`). The TIFF keeps the raw negative.
    """
    step = max(1, max(part16.shape[0] // maxdim, part16.shape[1] // maxdim))
    sub = part16[::step, ::step].astype(np.float32)
    out = np.empty(sub.shape, np.uint8)
    for c in range(3):
        ch = sub[..., c]
        lo, hi = np.percentile(ch, (0.5, 99.5))
        if hi <= lo:
            hi = lo + 1.0
        norm = np.clip((ch - lo) / (hi - lo), 0.0, 1.0)
        out[..., c] = ((1.0 - norm) * 255.0).astype(np.uint8)  # invert
    Image.fromarray(out).save(str(out_path), "PNG")


def decode_raw(
    raw_path,
    n_frames: int | None = None,
    rotate: int = 90,
    resample: tuple[int, int] | None = None,
    progress=None,
) -> list[dict]:
    """
    Decode a Pakon .raw file into per-frame TIFFs and JPEG thumbnails.

    Mirrors tools/pakon_image.py main(): IR-band zone split (with per-zone
    channel permutation + dual-tap seam correction), trilinear registration,
    autocrop, then a fixed-pitch frame grid.

    Args:
        raw_path:  path to the .raw scan file
        n_frames:  frames to split into; None / <=1 = auto-detect from the grid
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
    # A scan line is [visible | IR]; a buffer offset can wrap the visible image
    # around the line edge, dropping the IR band into the middle. Visible cols
    # are then [after IR] + [before IR] (wrap order), rejoining at the sensor
    # seam. Zone1 (before-IR) is read by the other CCD tap with a rotated
    # channel order, so it gets its own permutation + the seam needs correcting.
    emit("Finding IR band", 0.05)
    ir = find_ir_band(chans, full)
    if ir:
        ir0, ir1 = ir
        zones = [z for z in [(ir1 + 1, width), (0, ir0)] if z[1] > z[0]]
        zone_perms = (
            [None, {"r": "g", "g": "b", "b": "r"}] if len(zones) == 2 else [None]
        )
    else:
        zones = [(0, width)]
        zone_perms = [None]

    # ── Trilinear registration ────────────────────────────────────────────────
    emit("Measuring registration leads", 0.15)
    leads_per_zone = [
        measure_leads(chans, c0, c1, perm=zone_perms[i])
        for i, (c0, c1) in enumerate(zones)
    ]

    emit("Registering channels", 0.35)
    correct_seam = len(zones) == 2
    rgb = register_zones(
        chans, zones, leads_per_zone,
        correct_seam=correct_seam, zone_perms=zone_perms,
    )

    # ── Autocrop ──────────────────────────────────────────────────────────────
    emit("Autocropping", 0.50)
    rgb, _ = autocrop(rgb)

    # ── Frame grid (fixed pitch) ────────────────────────────────────────────────
    emit("Finding frame grid", 0.57)
    grid_n = n_frames if (n_frames and n_frames > 1) else None
    cut_rows, pitch = find_frame_grid(rgb, grid_n)
    n_out = max(0, len(cut_rows) - 1)
    if n_out <= 1:
        # No grid (single frame / short ribbon): emit the whole ribbon.
        cut_rows, n_out, target_w = [0, rgb.shape[0]], 1, None
    else:
        # Centre a fixed-width window (= pitch, capped 3000) in each cell so
        # every frame is the same width and inter-frame gaps trim equally.
        target_w = min(pitch, 3000)

    # ── Export frames ─────────────────────────────────────────────────────────
    rot_k = (rotate // 90) % 4
    frames = []

    for i in range(n_out):
        emit(f"Exporting frame {i + 1}/{n_out}", 0.60 + 0.40 * i / n_out)

        r_start, r_end = cut_rows[i], cut_rows[i + 1]
        if target_w is not None:
            centre = (r_start + r_end) // 2
            r_start = max(0, centre - target_w // 2)
            r_end = min(rgb.shape[0], r_start + target_w)
            r_start = max(0, r_end - target_w)
        part = rgb[r_start:r_end]
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

        # PNG preview — inverted (negative→positive), max 600 px on longest side.
        thumb_path = WORK_DIR / f"thumb_{i + 1:02d}.png"
        _preview_png(part, thumb_path)

        frames.append({"index": i + 1, "tiff": tiff_path, "thumb": thumb_path})

    emit("Done", 1.0)
    return frames
