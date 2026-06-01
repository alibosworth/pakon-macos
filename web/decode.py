"""
Decode pipeline for the Pakon web service.

Imports the core functions from tools/pakon_image.py and wraps them with
a progress callback suitable for async SSE streaming.  All I/O goes to
WORK_DIR (/tmp/pakon_web); callers can read .tif and .jpg files from there.

Each frame produces:
  - raw_tiff : the raw negative, 16-bit, as scanned (native resolution)
  - tiff     : the OEM-faithful C-41 inverted positive, 16-bit (plain, full
               control — the unrendered scene-referred file)
  - jpg      : the vibrant rendered JPEG (Kodak rpd.pf profile + scene balance
               + highlight roll-off), 8-bit
  - thumb    : a small grid thumbnail of the rendered JPEG
"""
import sys
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
    detect_zone_perm,
    find_frame_grid,
    find_ir_band,
    invert_c41,
    measure_dmin,
    measure_leads,
    register_zones,
    render_jpeg,
    _c41_lut,
)

WORK_DIR = Path("/tmp/pakon_web")
_LINEWIDTH = 8000
_REPO = Path(__file__).resolve().parent.parent
RPD_PROFILE = _REPO / "profiles" / "rpd.pf"
_FRAMES_PER_ROLL = 36   # fixed: a 36-exposure roll, by design (not user-tunable)


def decode_raw(
    raw_path,
    rotate: int = 90,
    crop_pct: float = 100.0,
    progress=None,
) -> list[dict]:
    """
    Decode a Pakon .raw file into per-frame TIFFs + rendered JPEGs.

    Mirrors tools/pakon_image.py main(): IR-band zone split (with auto per-zone
    channel order + dual-tap seam correction), trilinear registration, autocrop,
    a fixed 36-frame grid, the OEM-faithful C-41 inversion, and the rpd.pf JPEG
    render.

    Args:
        raw_path:  path to the .raw scan file
        rotate:    degrees CW to rotate each frame (0 / 90 / 180 / 270)
        crop_pct:  keep this % of the frame centre (trims edge rebate); 100 = full
        progress:  optional callable(step: str, pct: float 0–1)

    Returns:
        list of {"index", "raw_tiff", "tiff", "jpg", "thumb"}
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
    # Global interleave: position 0/1/2 -> the global chans keys b/r/g. The true
    # per-zone R/G/B identity is auto-detected below from the orange film base.
    chans = {"r": img[:, 1:n3:3], "g": img[:, 2:n3:3], "b": img[:, 0:n3:3]}
    sampled = [chans[c][::997] for c in ("r", "g", "b")]
    full = float(max(a.max() for a in sampled)) or 1.0
    width = chans["r"].shape[1]

    # ── IR band & zones ───────────────────────────────────────────────────────
    # A scan line is [visible | IR]; a buffer offset can wrap the visible image
    # around the line edge, dropping the IR band into the middle. Visible cols
    # are then [after IR] + [before IR] (wrap order), rejoining at the sensor
    # seam. Each zone is read by a different CCD tap with its own channel order.
    emit("Finding IR band", 0.05)
    ir = find_ir_band(chans, full)
    if ir:
        ir0, ir1 = ir
        zones = [z for z in [(ir1 + 1, width), (0, ir0)] if z[1] > z[0]]
    else:
        zones = [(0, width)]

    # Auto-detect each zone's R/G/B identity from the orange film base (robust;
    # fixes the purple cast that a hardcoded order causes on the wrong zone).
    zone_perms = [detect_zone_perm(chans, c0, c1)[0] for (c0, c1) in zones]

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

    # ── Fixed 36-frame grid ─────────────────────────────────────────────────────
    emit("Finding frame grid", 0.57)
    cut_rows, pitch = find_frame_grid(rgb, _FRAMES_PER_ROLL)
    n_out = max(0, len(cut_rows) - 1)
    if n_out <= 1:
        cut_rows, n_out, target_w = [0, rgb.shape[0]], 1, None
    else:
        target_w = min(pitch, 3000)

    # ── C-41 inversion setup ────────────────────────────────────────────────────
    # Film base (Dmin) measured once on the whole ribbon so every frame inverts
    # against the same white point.
    base = measure_dmin(rgb, pct=99.5)
    lut = _c41_lut()
    use_rpd = RPD_PROFILE.exists()

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

        # Optional centre crop: trim the frame-edge rebate/gap band. 100 = none.
        if crop_pct < 100.0:
            frac = max(0.1, min(1.0, crop_pct / 100.0))
            ph, pw = part.shape[:2]
            ch, cw = int(ph * frac), int(pw * frac)
            r0c, c0c = (ph - ch) // 2, (pw - cw) // 2
            part = part[r0c:r0c + ch, c0c:c0c + cw]
        if rot_k:
            part = np.rot90(part, k=rot_k)
        part = np.ascontiguousarray(part)

        pos = invert_c41(part, base, lut)            # 16-bit sRGB positive
        if use_rpd:
            rendered = render_jpeg(pos, str(RPD_PROFILE))   # 8-bit vibrant
        else:
            rendered = (pos >> 8).astype(np.uint8)          # fallback: plain

        raw_tiff = WORK_DIR / f"frame_{i + 1:02d}_raw.tif"
        pos_tiff = WORK_DIR / f"frame_{i + 1:02d}.tif"
        jpg_path = WORK_DIR / f"frame_{i + 1:02d}.jpg"
        thumb_path = WORK_DIR / f"thumb_{i + 1:02d}.jpg"

        tifffile.imwrite(str(raw_tiff), part, photometric="rgb")   # raw negative
        tifffile.imwrite(str(pos_tiff), pos, photometric="rgb")    # plain positive
        Image.fromarray(rendered).save(str(jpg_path), "JPEG", quality=92)

        step = max(1, max(rendered.shape[0] // 600, rendered.shape[1] // 600))
        Image.fromarray(rendered[::step, ::step]).save(
            str(thumb_path), "JPEG", quality=85)

        frames.append({"index": i + 1, "raw_tiff": raw_tiff,
                       "tiff": pos_tiff, "jpg": jpg_path, "thumb": thumb_path})

    emit("Done", 1.0)
    return frames


def make_contact_sheet(frames, cols: int = 6, cell_w: int = 480,
                       pad: int = 10, bg=(22, 22, 22), fg=(226, 226, 226)):
    """Tile the rendered frame JPEGs into one labelled contact-sheet image.

    `frames` is the decode_raw() result list. Returns a PIL Image."""
    from PIL import ImageDraw, ImageFont

    items = [f for f in frames if f.get("jpg") and Path(f["jpg"]).exists()]
    if not items:
        return None

    # Cell aspect from the first frame; label strip beneath each thumbnail.
    with Image.open(items[0]["jpg"]) as im0:
        aspect = im0.height / im0.width
    cell_h = int(cell_w * aspect)
    label_h = 26
    rows = (len(items) + cols - 1) // cols
    W = cols * cell_w + (cols + 1) * pad
    H = rows * (cell_h + label_h) + (rows + 1) * pad

    sheet = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial.ttf", 16)
    except OSError:
        font = ImageFont.load_default()

    for k, f in enumerate(items):
        r, c = divmod(k, cols)
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + label_h + pad)
        with Image.open(f["jpg"]) as im:
            thumb = im.convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
        sheet.paste(thumb, (x, y))
        draw.text((x + 2, y + cell_h + 4), f"Frame {f['index']:02d}",
                  fill=fg, font=font)
    return sheet
