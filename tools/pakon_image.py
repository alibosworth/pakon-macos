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


def measure_leads(chans, c0, c1, perm=None):
    """Measure the trilinear G/B line leads (in scan lines) relative to R, on a
    single column zone [c0:c1] of the full-width deinterleaved channels.

    `perm` is an optional dict remapping channel names, e.g. {"r":"g","g":"b","b":"r"}
    for zones whose tap outputs channels in a different order than the global default."""
    ch = {c: chans[perm[c]] if perm else chans[c] for c in ("r", "g", "b")}
    return {"r": 0,
            "g": _vlead(ch["g"][:, c0:c1], ch["r"][:, c0:c1]),
            "b": _vlead(ch["b"][:, c0:c1], ch["r"][:, c0:c1])}


def register_zones(chans, zones, leads_per_zone, correct_seam=False, zone_perms=None):
    """Co-register the trilinear R/G/B planes and assemble the visible image.

    `zones` is a list of (c0, c1) column ranges in *output order*; for a
    wrap-split scan that is [after-IR, before-IR] so the halves rejoin at the
    sensor wrap seam. Each zone has its own R/G/B leads (the two halves of a
    wrap-split image come from opposite ends of the sensor readout and need
    different leads — using one global lead leaves one half ghosted).

    `zone_perms` is an optional list of per-zone channel permutation dicts (same
    format as `measure_leads` perm). Used when a zone's tap outputs channels in a
    different order than the global default (e.g. the second tap in a wrap-split
    scan).

    All zones are row-cropped against a *common* lead span (global max/min over
    every zone) so they stay aligned to the same scan lines, then concatenated
    left-to-right. Returns the assembled (H, W, 3) array."""
    allv = [v for leads in leads_per_zone for v in leads.values()]
    gm, glo = max(allv), min(allv)
    n = chans["r"].shape[0]
    L = n - (gm - glo)
    parts = []
    for i, ((c0, c1), leads) in enumerate(zip(zones, leads_per_zone)):
        perm = zone_perms[i] if zone_perms else None
        src = {c: chans[perm[c]] if perm else chans[c] for c in ("r", "g", "b")}
        out = {c: src[c][gm - leads[c]: gm - leads[c] + L, c0:c1] for c in src}
        parts.append(np.stack([out["r"], out["g"], out["b"]], axis=-1))
    if correct_seam:
        parts = dual_tap_correct(parts)
    return np.concatenate(parts, axis=1)


def dual_tap_correct(parts, seam_cols=50):
    """Correct the dual-tap CCD gain/offset seam for wrap-split (LowRes) scans.

    When the IR band sits in the middle of the line the visible image wraps into
    two zones, each read by a different CCD output tap with its own analogue
    gain and offset. This produces a hard per-channel tint seam at the zone
    junction. Fix: measure per-channel pixel statistics at the seam boundary
    (last seam_cols of zone 0 and first seam_cols of zone 1) and apply a linear
    gain+offset correction to zone 0 so it matches zone 1 at the seam.
    """
    if len(parts) != 2:
        return parts
    z0 = parts[0].astype(np.float64)
    z1 = parts[1].astype(np.float64)
    k = min(seam_cols, z0.shape[1], z1.shape[1])
    s0 = z0[:, -k:, :].reshape(-1, 3)
    s1 = z1[:, :k, :].reshape(-1, 3)
    ch_names = ("R", "G", "B")
    corrected = z0.copy()
    for ch in range(3):
        mu0, mu1 = s0[:, ch].mean(), s1[:, ch].mean()
        sigma0, sigma1 = s0[:, ch].std(), s1[:, ch].std()
        if sigma0 > 1.0:
            a = sigma1 / sigma0
            b = mu1 - a * mu0
        else:
            a, b = 1.0, mu1 - mu0
        print(f"  dual-tap {ch_names[ch]}: a={a:.4f} b={b:+.1f}  "
              f"seam mu {mu0:.0f} -> {mu1:.0f}")
        corrected[:, :, ch] = a * z0[:, :, ch] + b
    return [np.clip(corrected, 0, 65535).astype(np.uint16),
            np.clip(z1, 0, 65535).astype(np.uint16)]


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
    run of image rows. After that, further trim uniform (low-detail) rows from
    both ends — this removes blank film base (orange mask, no exposure) that is
    neither dark nor bright-neutral. Cols: trim residual low-detail margins.
    """
    full = float(rgb.max()) or 1.0
    sub = rgb[:, ::8, :].astype(np.float32)
    bright = sub.mean((1, 2))
    spread = sub.max(2).mean(1) - sub.min(2).mean(1)
    dark = bright < 0.06 * full
    blank = (bright > 0.55 * full) & (spread < 0.03 * full)
    r0, r1 = _all_runs(~(dark | blank))[0]

    # Trim low-detail (uniform film base) from both ends. Sample every 4 rows;
    # threshold at 25% of the median detail in the central half of the ribbon.
    step = 4
    lum_e = rgb[r0:r1 + 1:step, ::8, :].astype(np.float32)
    row_det = lum_e.std(axis=(1, 2))
    n = len(row_det)
    mid_det = np.median(row_det[n // 4: 3 * n // 4]) if n > 4 else row_det.mean()
    thresh = 0.25 * mid_det
    trim_s = 0
    while trim_s < n and row_det[trim_s] < thresh:
        trim_s += 1
    trim_e = n - 1
    while trim_e > trim_s and row_det[trim_e] < thresh:
        trim_e -= 1
    r0 = r0 + trim_s * step
    r1 = min(r1, r0 + (trim_e - trim_s + 1) * step)

    lum = rgb[r0:r1 + 1:15].astype(np.float32).mean(2)
    cs = lum.std(0)
    c0, c1 = _all_runs(cs > 0.28 * cs.max())[0]
    return rgb[r0:r1 + 1, c0:c1 + 1], (r0, r1, c0, c1)


def find_frame_boundaries(ribbon, n_frames=None):
    """Find inter-frame boundary row positions.

    If n_frames is given, returns exactly (n_frames-1) boundaries (top
    by prominence). If n_frames is None, auto-detects frame count from
    the valley prominence distribution: the largest ratio between
    consecutive sorted prominence values marks the separation between
    real inter-frame gaps and noise.

    Uses the central 50% of columns to avoid constant film-edge stripes.
    Returns a sorted list of boundary row indices.
    """
    rows, cols, _ = ribbon.shape
    if n_frames is not None and n_frames <= 1:
        return []

    step_r = max(1, rows // 6000)
    cen0, cen1 = cols // 4, 3 * cols // 4
    step_c = max(1, (cen1 - cen0) // 200)
    lum = ribbon[::step_r, cen0:cen1:step_c, :].astype(np.float32).mean(2)
    detail = lum.std(1)

    win = max(5, int(0.02 * len(detail)))
    smoothed = np.convolve(detail, np.ones(win) / win, mode='same')

    half = max(3, len(smoothed) // (3 * (n_frames or 30)))
    valleys = [i for i in range(half, len(smoothed) - half)
               if smoothed[i] == smoothed[i - half: i + half + 1].min()]

    def prominence(i):
        l = smoothed[:i].max() if i > 0 else smoothed[i]
        r = smoothed[i + 1:].max() if i < len(smoothed) - 1 else smoothed[i]
        return min(l, r) - smoothed[i]

    prom = {i: prominence(i) for i in valleys}

    if n_frames is not None:
        # Caller knows the count: take top (n_frames-1) by prominence
        if len(valleys) < n_frames - 1:
            # Fallback: equal-spacing
            expected = len(smoothed) // n_frames
            half_fb = max(expected // 4, 10)
            bnd = []
            for k in range(1, n_frames):
                lo = max(0, k * expected - half_fb)
                hi = min(len(smoothed), k * expected + half_fb)
                bnd.append(int((lo + int(np.argmin(smoothed[lo:hi]))) * step_r))
            return sorted(bnd)
        selected = sorted(sorted(valleys, key=lambda i: -prom[i])[:n_frames - 1])
        return [int(i * step_r) for i in selected]

    # Auto-detect: find the largest prominence ratio gap to separate real
    # inter-frame gaps from noise, then keep everything above the threshold.
    if not valleys:
        return []
    sorted_prom = sorted(prom[i] for i in valleys)
    # Find the biggest jump between consecutive sorted prominence values
    best_ratio, threshold = 1.0, sorted_prom[0]
    for a, b in zip(sorted_prom, sorted_prom[1:]):
        if a > 0 and b / a > best_ratio:
            best_ratio, threshold = b / a, (a + b) / 2
    real = sorted(i for i in valleys if prom[i] > threshold)
    return [int(i * step_r) for i in real]


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
    ap.add_argument("--frames", type=int, default=None,
                    help="split into N frames (default: auto-detect from valley count)")
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
    ap.add_argument("--seam-correct", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="correct dual-tap CCD gain/offset seam for wrap-split "
                         "(LowRes/full-roll) scans (default on; no-op for "
                         "single-zone HiRes scans)")
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
        # Zone1 (before-IR) is read by the other CCD tap which outputs channels
        # in a different order: pos0→G, pos1→B, pos2→R vs zone0's pos0→B, pos1→R,
        # pos2→G. Confirmed by permutation sweep on fullroll.raw (seam_RGB winner).
        zone_perms = [None, {"r": "g", "g": "b", "b": "r"}] if len(zones) == 2 else [None]
    else:
        zones = [(0, width)]
        zone_perms = [None]

    if args.register:
        if args.reg_leads:
            g, b = (int(v) for v in args.reg_leads.split(","))
            leads_per_zone = [{"r": 0, "g": g, "b": b} for _ in zones]
        else:
            # Each visible zone gets its own leads, measured with the correct
            # per-zone channel mapping so the cross-correlation sees real RGB.
            leads_per_zone = [measure_leads(chans, c0, c1, perm=zone_perms[i])
                              for i, (c0, c1) in enumerate(zones)]
        for (c0, c1), leads in zip(zones, leads_per_zone):
            print(f"register: zone {c0}-{c1} trilinear leads "
                  f"R=0 G={leads['g']} B={leads['b']}")
    else:
        leads_per_zone = [{"r": 0, "g": 0, "b": 0} for _ in zones]

    correct_seam = args.seam_correct and len(zones) == 2
    rgb = register_zones(chans, zones, leads_per_zone,
                         correct_seam=correct_seam,
                         zone_perms=zone_perms)  # (lines, Wvis, 3)
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

    rot = (args.rotate // 90) % 4

    boundaries = find_frame_boundaries(rgb, args.frames)
    fr = len(boundaries) + 1 if boundaries else 1
    split_rows = [0] + boundaries + [rgb.shape[0]]

    if fr > 1:
        frame_widths = [split_rows[i + 1] - split_rows[i] for i in range(fr)]
        target_w = min(int(np.median(frame_widths)), 3000)
        print(f"detected {fr} frames, boundaries (rows): {boundaries}")
        print(f"frame widths: min={min(frame_widths)} med={int(np.median(frame_widths))} "
              f"max={max(frame_widths)}  -> normalising to {target_w}")
    else:
        target_w = None

    for i in range(fr):
        r_start = split_rows[i]
        r_end = split_rows[i + 1]
        if target_w is not None:
            # Centre-crop to target_w; clamp to ribbon bounds
            centre = (r_start + r_end) // 2
            r_start = max(0, centre - target_w // 2)
            r_end = r_start + target_w
            if r_end > rgb.shape[0]:
                r_end = rgb.shape[0]
                r_start = max(0, r_end - target_w)
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
