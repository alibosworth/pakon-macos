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


def _invert16(part16, mode: str = "density"):
    """Negative→positive as 16-bit RGB.

    `mode="density"` (default): invert in density (log) space —
    ``d = log10(max/transmission)``, then a per-channel black/white stretch in
    density. The C-41 orange mask is a per-channel *density* offset, so removing
    it there yields neutral shadows. A linear inversion leaves that offset as a
    warm floor that glows through the shadows of dark scenes (the "light bleed"),
    identical on every frame because the mask is the same on every frame.

    `mode="linear"`: the older per-channel linear stretch + invert. Kept for
    comparison; warm in the shadows of dark frames.

    Percentiles are measured on a subsample for speed, applied at full res.
    """
    f = part16.astype(np.float32)
    out = np.empty(f.shape, np.float32)

    if mode == "linear":
        sub = f[::4, ::4]
        for c in range(3):
            lo, hi = np.percentile(sub[..., c], (0.5, 99.5))
            if hi <= lo:
                hi = lo + 1.0
            out[..., c] = 1.0 - np.clip((f[..., c] - lo) / (hi - lo), 0.0, 1.0)
    else:
        mx = float(f.max()) or 1.0
        d = np.log10(np.clip(mx / np.clip(f, 1.0, None), 1.0, None))
        sub = d[::4, ::4]
        for c in range(3):
            lo, hi = np.percentile(sub[..., c], (1.0, 99.5))
            if hi <= lo:
                hi = lo + 1e-6
            out[..., c] = np.clip((d[..., c] - lo) / (hi - lo), 0.0, 1.0)

    return (out * 65535.0).astype(np.uint16)


def decode_raw(
    raw_path,
    n_frames: int | None = None,
    rotate: int = 90,
    resample: tuple[int, int] | None = None,
    base_order: str = "012",
    invert_mode: str = "density",
    crop_pct: float = 100.0,
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
        base_order: interleave phase — the raw sample positions (mod 3) that map
                    to R,G,B. Different scan/replay modes frame their data at a
                    different phase, cyclically rotating the colour channels.
                    "012" matches the 36-exposure replay; "120" the earlier
                    F-135 captures (legacy B,R,G). Whichever phase is chosen, the
                    inter-zone (dual-tap) relative permutation is unchanged.
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
    # Interleave phase: which raw sample position (mod 3) carries R, G, B. Fixed
    # by the scan/replay mode, not the film (see base_order docstring).
    if len(base_order) != 3 or set(base_order) != set("012"):
        base_order = "012"
    pr, pg, pb = (int(d) for d in base_order)
    chans = {
        "r": img[:, pr:n3:3],
        "g": img[:, pg:n3:3],
        "b": img[:, pb:n3:3],
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
        # Optional centre crop: keep the central crop_pct of each axis, trimming
        # the frame-edge rebate/gap band that can leak in. 100 = no crop.
        if crop_pct < 100.0:
            frac = max(0.1, min(1.0, crop_pct / 100.0))
            ph, pw = part.shape[:2]
            ch, cw = int(ph * frac), int(pw * frac)
            r0c, c0c = (ph - ch) // 2, (pw - cw) // 2
            part = part[r0c:r0c + ch, c0c:c0c + cw]
        if rot_k:
            part = np.rot90(part, k=rot_k)
        part = np.ascontiguousarray(part)

        pos = _invert16(part, mode=invert_mode)              # 16-bit positive
        pos8 = (pos >> 8).astype(np.uint8)          # 8-bit for JPEG/thumb

        raw_tiff = WORK_DIR / f"frame_{i + 1:02d}_raw.tif"
        pos_tiff = WORK_DIR / f"frame_{i + 1:02d}.tif"
        jpg_path = WORK_DIR / f"frame_{i + 1:02d}.jpg"
        thumb_path = WORK_DIR / f"thumb_{i + 1:02d}.jpg"

        # Raw negative TIFF — 16-bit, as scanned (native resolution).
        tifffile.imwrite(str(raw_tiff), part, photometric="rgb")

        # Inverted positive TIFF + JPEG (resample-corrected if requested).
        if resample:
            rw, rh = resample
            h, w, _ = pos.shape
            fd, tmp = tempfile.mkstemp(suffix=".rgb")
            os.close(fd)
            pos.astype(">u2").tofile(tmp)
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
                        str(pos_tiff),
                    ],
                    check=True,
                )
            finally:
                os.unlink(tmp)
            Image.fromarray(pos8).resize((rw, rh), Image.LANCZOS).save(
                str(jpg_path), "JPEG", quality=92)
        else:
            tifffile.imwrite(str(pos_tiff), pos, photometric="rgb")
            Image.fromarray(pos8).save(str(jpg_path), "JPEG", quality=92)

        # Small grid thumbnail (max 600 px, inverted positive).
        step = max(1, max(pos8.shape[0] // 600, pos8.shape[1] // 600))
        Image.fromarray(pos8[::step, ::step]).save(
            str(thumb_path), "JPEG", quality=85)

        frames.append({"index": i + 1, "raw_tiff": raw_tiff,
                       "tiff": pos_tiff, "jpg": jpg_path, "thumb": thumb_path})

    emit("Done", 1.0)
    return frames
