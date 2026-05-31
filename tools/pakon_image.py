#!/usr/bin/env python3
"""
pakon_image.py — decode a Pakon raw scan stream into 16-bit RGB TIFF(s).

The captured 0x86 image stream (pakon_replay --scan output) is, on this F-135:
  - 16-bit little-endian samples,
  - per-pixel interleaved (column autocorrelation peaks at lag 3/6/9), in the
    order **B, R, G** — i.e. samples run B,R,G,B,R,G,... NOT R,G,B. Green is
    the middle trilinear line (position 2), red is position 1, blue position 0;
    confirmed by natural skin tones across all 6 channel permutations of a real
    frame (the wrong orders give green or "lomography purple" skin).
  - `--linewidth` samples per scan line (default 8000 = 16000-byte stride),
    giving width = linewidth//3 px (~2666); the last 0-2 samples are padding.

The sensor is **trilinear** (separate R/G/B lines spaced along the scan
direction), so the three channels are offset by a few scan lines and show
colour ghosting at edges if naively combined. By default it **co-registers**
them (auto-measured; lines are spaced ~8 apart, order B/G/R along the scan, so
green and blue lead red by ~8 and ~15 lines on this F-135);
`--no-register` disables it, `--reg-leads G,B` forces the offsets.

By default it **autocrops** the ribbon: a real scan begins with a dark leader
and often a blank stretch (light through no film, before the strip is loaded),
ends with another blank tail, and carries a uniform gate margin on one side.
`autocrop` drops those (leader = dark rows; blank = bright *and* colour-neutral
rows; margin = low-detail columns) and keeps the film. Pass `--no-autocrop` for
the full raw ribbon.

Structural bright bands (scanner artifacts — persistent bright column ranges
with low variation, e.g. scan-mode light artifacts) are automatically detected
and stitched out of the output; the image zones on either side are concatenated.

This deinterleaves to RGB and writes a **16-bit RGB TIFF**, by default the raw
(uninverted) negative — orange-mask/negative intact — so you can feed it to your
own color-inversion software. `--invert` does a quick linear positive for a
sanity preview only (proper negative inversion is left to dedicated tools).

Use `--resample-to WxH` to correct for non-square raw pixels and get the right
output geometry; for 35mm film at Pakon native resolution use `--resample-to
3000x2000`. The frame splitting (`--frames N`) uses actual inter-frame gap
detection when N > 1, so frames land on real boundaries rather than equal
divisions.

Needs numpy and ImageMagick (`magick`). Writes a small PNG preview alongside.
"""
import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np


def _vlead(ch, ref, rng=40, rowstep=1, colstep=3):
    """Vertical 'lead' of channel `ch` over `ref` in scan lines: the dy that
    maximizes corr(ch[row], ref[row+dy]). On this F-135 the sensor is trilinear
    (separate R/G/B lines along the scan), so a feature at ref row y appears in
    `ch` at row y-dy. Measured on a central detail band, subsampled for speed."""
    n = ch.shape[0]
    a, b = int(n * 0.35), int(n * 0.65)
    A = ch[a:b:rowstep, ::colstep].astype(np.float32)
    Rf = ref[a:b:rowstep, ::colstep].astype(np.float32)
    rng = min(rng, A.shape[0] // 4)
    base = A[rng:-rng]
    best = (0, -2.0)
    for dy in range(-rng, rng + 1):
        cmp = Rf[rng + dy:Rf.shape[0] - rng + dy]
        x = base.ravel() - base.mean()
        y = cmp.ravel() - cmp.mean()
        d = np.linalg.norm(x) * np.linalg.norm(y)
        if d:
            c = float(np.dot(x, y) / d)
            if c > best[1]:
                best = (dy, c)
    return best[0]


def measure_leads(chans, c0, c1):
    """Measure the trilinear G/B line leads (in scan lines) relative to R, on a
    single column zone [c0:c1] of the full-width deinterleaved channels."""
    return {"r": 0,
            "g": _vlead(chans["g"][:, c0:c1], chans["r"][:, c0:c1]),
            "b": _vlead(chans["b"][:, c0:c1], chans["r"][:, c0:c1])}


def register_zones(chans, zones, leads_per_zone):
    """Co-register the trilinear R/G/B planes and assemble the visible image.

    `zones` is a list of (c0, c1) column ranges in *output order*; for a
    wrap-split scan that is [after-IR, before-IR] so the halves rejoin at the
    sensor wrap seam. Each zone has its own R/G/B leads (the two halves of a
    wrap-split image come from opposite ends of the sensor readout and need
    different leads — using one global lead leaves one half ghosted).

    All zones are row-cropped against a *common* lead span (global max/min over
    every zone) so they stay aligned to the same scan lines, then concatenated
    left-to-right. Returns the assembled (H, W, 3) array."""
    allv = [v for leads in leads_per_zone for v in leads.values()]
    gm, glo = max(allv), min(allv)
    n = chans["r"].shape[0]
    L = n - (gm - glo)
    parts = []
    for (c0, c1), leads in zip(zones, leads_per_zone):
        out = {c: chans[c][gm - leads[c]: gm - leads[c] + L, c0:c1] for c in chans}
        parts.append(np.stack([out["r"], out["g"], out["b"]], axis=-1))
    return np.concatenate(parts, axis=1)


def find_ir_band(chans, full):
    """Locate the IR (Digital ICE) band: a contiguous run of bright,
    colour-neutral columns (R≈G≈B). Returns (ir0, ir1) in deinterleaved column
    space, or None. Measured on a central film slice."""
    n = chans["r"].shape[0]
    a, b = max(0, n // 2 - 5000), n // 2 + 5000
    sl = np.stack([chans["r"][a:b], chans["g"][a:b], chans["b"][a:b]],
                  axis=-1).astype(np.float32)
    col_mean = sl.mean((0, 2))
    col_spread = sl.max(2).mean(0) - sl.min(2).mean(0)
    ir = (col_mean > 0.35 * full) & (col_spread < 0.06 * full)
    runs = _all_runs(ir, min_len=100)
    return runs[0] if runs else None


def write_tiff(rgb16, path, resize=None):
    """Write an (H,W,3) uint16 array as a 16-bit RGB TIFF via ImageMagick.
    If resize=(W,H) is given, resample to exactly that size."""
    h, w, _ = rgb16.shape
    fd, tmp = tempfile.mkstemp(suffix=".rgb")
    os.close(fd)
    rgb16.astype(">u2").tofile(tmp)              # big-endian interleaved RGB
    try:
        cmd = ["magick", "-size", f"{w}x{h}", "-depth", "16",
               "-endian", "msb", f"rgb:{tmp}"]
        if resize:
            rw, rh = resize
            # '!' forces exact dimensions (no aspect preservation — we are
            # deliberately correcting the pixel aspect ratio)
            cmd += ["-filter", "Lanczos", "-resize", f"{rw}x{rh}!", "-depth", "16"]
        cmd.append(path)
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        sys.exit("ImageMagick 'magick' not found (install imagemagick)")
    finally:
        os.unlink(tmp)


def _all_runs(mask, min_len=1):
    """Return list of (start, end) inclusive for all True runs in mask,
    filtered to minimum length min_len, sorted by length descending."""
    idx = np.where(mask)[0]
    if not len(idx):
        return []
    runs = []
    s, p = idx[0], idx[0]
    for i in idx[1:]:
        if i != p + 1:
            if p - s + 1 >= min_len:
                runs.append((int(s), int(p)))
            s = i
        p = i
    if p - s + 1 >= min_len:
        runs.append((int(s), int(p)))
    runs.sort(key=lambda r: r[1] - r[0], reverse=True)
    return runs


def autocrop(rgb):
    """Trim the dark leader, the blank (no-film) pre/post-load scan, and the
    residual low-detail margins, returning (cropped, (r0, r1, c0, c1)).

    Operates on the already de-wrapped, IR-removed visible image (a single
    contiguous zone). Rows: a row is non-image if it's very dark (leader) OR
    bright *and* neutral (light through no film). We keep the largest contiguous
    run of image rows. Cols: trim the residual low-detail margins — after
    de-wrapping the IR-adjacent edges (gate margin, orange-base sliver) sit at
    the outer edges, where this drops them, leaving the continuous interior.
    """
    full = float(rgb.max()) or 1.0
    sub = rgb[:, ::8, :].astype(np.float32)
    bright = sub.mean((1, 2))                       # per-row brightness
    spread = sub.max(2).mean(1) - sub.min(2).mean(1)  # per-row channel spread
    dark = bright < 0.06 * full
    blank = (bright > 0.55 * full) & (spread < 0.03 * full)
    r0, r1 = _all_runs(~(dark | blank))[0]

    lum = rgb[r0:r1 + 1:15].astype(np.float32).mean(2)
    cs = lum.std(0)
    c0, c1 = _all_runs(cs > 0.28 * cs.max())[0]
    return rgb[r0:r1 + 1, c0:c1 + 1], (r0, r1, c0, c1)


def find_frame_boundaries(ribbon, n_frames):
    """Find row positions of (n_frames - 1) inter-frame boundaries.

    Inter-frame zones (unexposed film base between frames) appear as rows with
    low spatial detail. The search uses a smoothed per-row detail signal and
    finds the local minimum near each expected frame boundary rather than
    dividing equally.

    Returns a sorted list of (n_frames - 1) row indices.
    """
    rows, cols, _ = ribbon.shape
    if n_frames <= 1:
        return []

    # Subsample for speed: at most ~4000 rows, ~200 cols in the detail signal
    step_r = max(1, rows // 4000)
    step_c = max(1, cols // 200)
    lum = ribbon[::step_r, ::step_c, :].astype(np.float32).mean(2)  # (R, C)
    detail = lum.std(1)  # std across columns per row → shape (R,)

    # Smooth with a box filter (~2% of ribbon)
    win = max(5, int(0.02 * len(detail)))
    kernel = np.ones(win) / win
    smoothed = np.convolve(detail, kernel, mode='same')

    expected = len(smoothed) // n_frames
    # Search window: ±25% of expected spacing
    half = max(expected // 4, 10)

    boundaries = []
    for i in range(1, n_frames):
        center = i * expected
        lo = max(0, center - half)
        hi = min(len(smoothed), center + half)
        local_min = lo + int(np.argmin(smoothed[lo:hi]))
        boundaries.append(int(local_min * step_r))

    return sorted(boundaries)


def write_preview(rgb16, path, maxdim=1000):
    try:
        from PIL import Image
    except ImportError:
        return
    a = rgb16
    step = max(1, max(a.shape[0] // maxdim, a.shape[1] // maxdim))
    a8 = (a[::step, ::step] >> 8).astype(np.uint8)
    Image.fromarray(a8).save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw")
    ap.add_argument("--linewidth", type=int, default=8000,
                    help="samples per scan line (default 8000)")
    ap.add_argument("--order", default="rgb",
                    help="channel order of the interleave (default rgb)")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    ap.add_argument("--frames", type=int, default=1,
                    help="split the ribbon into N frames using inter-frame gap "
                         "detection (default 1 = full ribbon)")
    ap.add_argument("--crop-width", type=int,
                    help="keep only this many px of width (drop blank overscan)")
    ap.add_argument("--autocrop", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="auto-trim leader/blank-scan/structural-bands (default "
                         "on; --no-autocrop keeps the full raw ribbon)")
    ap.add_argument("--register", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="co-register the trilinear R/G/B sensor lines (default "
                         "on; fixes colour ghosting at edges)")
    ap.add_argument("--reg-leads",
                    help="force channel leads in lines as 'G,B' (rel. to R), "
                         "e.g. 16,8; default = auto-measure")
    ap.add_argument("--resample-to",
                    help="resample each output frame to WxH (e.g. 3000x2000) to "
                         "correct non-square raw pixels; for 35mm use 3000x2000")
    ap.add_argument("--invert", action="store_true",
                    help="quick linear positive (preview only; not real C-41)")
    ap.add_argument("-o", "--out", default="frame",
                    help="output path prefix (default 'frame')")
    args = ap.parse_args()

    resize = None
    if args.resample_to:
        try:
            rw, rh = (int(v) for v in args.resample_to.lower().split("x"))
            resize = (rw, rh)
        except ValueError:
            sys.exit("--resample-to must be WxH, e.g. 3000x2000")

    raw = np.memmap(args.raw, dtype="<u2", mode="r")
    lw = args.linewidth
    lines = raw.size // lw
    img = raw[:lines * lw].reshape(lines, lw)

    n3 = (lw // 3) * 3
    # Interleave order is B,R,G: position 0 = B, position 1 = R, position 2 = G
    # (green is the middle trilinear line). Verified by skin-tone test across all
    # 6 permutations of a real frame.
    chans = {"r": img[:, 1:n3:3], "g": img[:, 2:n3:3], "b": img[:, 0:n3:3]}
    full = float(max(chans["r"][::997].max(), chans["g"][::997].max(),
                     chans["b"][::997].max())) or 1.0
    width = chans["r"].shape[1]

    # Locate the IR (Digital ICE) band and define the visible zones. A scan line
    # is laid out [visible | IR]; a horizontal buffer offset can wrap the visible
    # image around the line edge, dropping the IR band into the middle. The
    # visible columns are then everything after the IR band, then everything
    # before it (wrap order) — which rejoins the image at the true sensor seam.
    ir = find_ir_band(chans, full)
    if ir:
        ir0, ir1 = ir
        # Wrap order: columns after the IR band, then columns before it. Drop
        # empty zones (when the IR band sits flush against a line edge, e.g. the
        # 4-frame scan, one side is empty and there is nothing to wrap).
        zones = [z for z in [(ir1 + 1, width), (0, ir0)] if z[1] > z[0]]
        print(f"IR band: cols {ir0}-{ir1} (Digital ICE); visible zones "
              f"{zones}")
    else:
        zones = [(0, width)]

    if args.register:
        if args.reg_leads:
            g, b = (int(v) for v in args.reg_leads.split(","))
            leads_per_zone = [{"r": 0, "g": g, "b": b} for _ in zones]
        else:
            # Each visible zone gets its own leads — the wrap-split halves come
            # from opposite ends of the sensor readout and need different
            # offsets; one global lead leaves one half ghosted.
            leads_per_zone = [measure_leads(chans, c0, c1) for c0, c1 in zones]
        for (c0, c1), leads in zip(zones, leads_per_zone):
            print(f"register: zone {c0}-{c1} trilinear leads "
                  f"R=0 G={leads['g']} B={leads['b']}")
    else:
        leads_per_zone = [{"r": 0, "g": 0, "b": 0} for _ in zones]

    rgb = register_zones(chans, zones, leads_per_zone)  # (lines, Wvis, 3)
    order_idx = {"r": 0, "g": 1, "b": 2}
    perm = [order_idx[c] for c in args.order.lower()]
    if perm != [0, 1, 2]:
        rgb = rgb[:, :, perm]

    if args.autocrop:
        rgb, (r0, r1, c0, c1) = autocrop(rgb)
        print(f"autocrop: rows {r0}-{r1}, cols {c0}-{c1} -> {rgb.shape[1]}x{rgb.shape[0]}")
    if args.crop_width:
        rgb = rgb[:, :args.crop_width]
    if args.invert:
        rgb = rgb.max() - rgb

    # Find frame boundaries using inter-frame gap detection
    fr = args.frames
    rot = (args.rotate // 90) % 4

    if fr > 1:
        boundaries = find_frame_boundaries(rgb, fr)
        # Build split points: [0, b1, b2, ..., b_{n-1}, end]
        split_rows = [0] + boundaries + [rgb.shape[0]]
        print(f"frame boundaries (rows): {boundaries}")
    else:
        split_rows = [0, rgb.shape[0]]

    for i in range(fr):
        r_start = split_rows[i]
        r_end = split_rows[i + 1]
        part = rgb[r_start:r_end]
        if rot:
            part = np.rot90(part, k=rot)
        part = np.ascontiguousarray(part)
        suffix = f"_{i+1}" if fr > 1 else ""
        tif = f"{args.out}{suffix}.tif"
        write_tiff(part, tif, resize=resize)
        w_out = resize[0] if resize else part.shape[1]
        h_out = resize[1] if resize else part.shape[0]
        print(f"wrote {tif}  ({w_out}x{h_out}, 16-bit RGB"
              f"{' resampled' if resize else ''}"
              f"{' inverted' if args.invert else ' raw/negative'})")


if __name__ == "__main__":
    main()
