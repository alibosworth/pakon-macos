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

This deinterleaves to RGB and writes a **16-bit RGB TIFF**, by default the raw
(uninverted) negative — orange-mask/negative intact — so you can feed it to your
own color-inversion software. `--invert` does a quick linear positive for a
sanity preview only (proper negative inversion is left to dedicated tools).

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


def register_channels(chans, leads):
    """Co-register trilinear R/G/B planes given each channel's lead (in lines)
    relative to R. Output row y takes chans[c][y - lead_c]; all planes are
    cropped to the common valid span. `lead_c` is how far ahead channel c is vs
    R; offsets may be negative when R is not the leading line. Returns (aligned
    dict, new line count)."""
    m, lo = max(leads.values()), min(leads.values())
    L = next(iter(chans.values())).shape[0] - (m - lo)
    out = {c: chans[c][m - leads[c]: m - leads[c] + L] for c in chans}
    return out, L


def write_tiff(rgb16, path):
    """Write an (H,W,3) uint16 array as a 16-bit RGB TIFF via ImageMagick."""
    h, w, _ = rgb16.shape
    fd, tmp = tempfile.mkstemp(suffix=".rgb")
    os.close(fd)
    rgb16.astype(">u2").tofile(tmp)              # big-endian interleaved RGB
    try:
        subprocess.run(["magick", "-size", f"{w}x{h}", "-depth", "16",
                        "-endian", "msb", f"rgb:{tmp}", path], check=True)
    except FileNotFoundError:
        sys.exit("ImageMagick 'magick' not found (install imagemagick)")
    finally:
        os.unlink(tmp)


def _largest_run(mask):
    """(start, end) inclusive of the longest contiguous True run in a 1-D mask."""
    idx = np.where(mask)[0]
    if not len(idx):
        return 0, len(mask) - 1
    runs, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i != p + 1:
            runs.append((s, p))
            s = i
        p = i
    runs.append((s, p))
    runs.sort(key=lambda r: r[1] - r[0])
    return int(runs[-1][0]), int(runs[-1][1])


def autocrop(rgb):
    """Trim the dark leader, the blank (no-film) pre/post-load scan, and the
    neutral right margin, returning (cropped_rgb, (r0, r1, c0, c1)).

    Rows: a row is non-image if it's very dark (leader) OR bright *and* neutral
    (light through no film — channels nearly equal). We keep the largest
    contiguous run of image rows. Cols: keep where vertical detail (column std)
    is high; the gate margin is uniform/low-detail and the orange base edge is
    a thin low-detail sliver, so both fall away.
    """
    full = float(rgb.max()) or 1.0
    sub = rgb[:, ::8, :].astype(np.float32)
    bright = sub.mean((1, 2))                       # per-row brightness
    spread = sub.max(2).mean(1) - sub.min(2).mean(1)  # per-row channel spread
    dark = bright < 0.06 * full
    blank = (bright > 0.55 * full) & (spread < 0.03 * full)
    r0, r1 = _largest_run(~(dark | blank))
    lum = rgb[r0:r1 + 1:15].astype(np.float32).mean(2)
    cs = lum.std(0)
    c0, c1 = _largest_run(cs > 0.30 * cs.max())
    return rgb[r0:r1 + 1, c0:c1 + 1], (r0, r1, c0, c1)


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
                    help="split the ribbon into N equal frames (default 1)")
    ap.add_argument("--crop-width", type=int,
                    help="keep only this many px of width (drop blank overscan)")
    ap.add_argument("--autocrop", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="auto-trim leader/blank-scan/right-margin (default on; "
                         "--no-autocrop keeps the full raw ribbon)")
    ap.add_argument("--register", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="co-register the trilinear R/G/B sensor lines (default "
                         "on; fixes colour ghosting at edges)")
    ap.add_argument("--reg-leads",
                    help="force channel leads in lines as 'G,B' (rel. to R), "
                         "e.g. 16,8; default = auto-measure")
    ap.add_argument("--invert", action="store_true",
                    help="quick linear positive (preview only; not real C-41)")
    ap.add_argument("-o", "--out", default="frame",
                    help="output path prefix (default 'frame')")
    args = ap.parse_args()

    raw = np.memmap(args.raw, dtype="<u2", mode="r")
    lw = args.linewidth
    lines = raw.size // lw
    img = raw[:lines * lw].reshape(lines, lw)

    n3 = (lw // 3) * 3
    # Interleave order is B,R,G: position 0 = B, position 1 = R, position 2 = G
    # (green is the middle trilinear line). Verified by skin-tone test across all
    # 6 permutations of a real frame.
    chans = {"r": img[:, 1:n3:3], "g": img[:, 2:n3:3], "b": img[:, 0:n3:3]}

    if args.register:
        if args.reg_leads:
            g, b = (int(v) for v in args.reg_leads.split(","))
            leads = {"r": 0, "g": g, "b": b}
        else:
            leads = {"r": 0,
                     "g": _vlead(chans["g"], chans["r"]),
                     "b": _vlead(chans["b"], chans["r"])}
        chans, _ = register_channels(chans, leads)
        print(f"register: trilinear leads (lines) R=0 G={leads['g']} B={leads['b']}")

    rgb = np.stack([chans[c] for c in args.order.lower()], axis=-1)  # (lines,W,3)

    if args.autocrop:
        rgb, (r0, r1, c0, c1) = autocrop(rgb)
        print(f"autocrop: rows {r0}-{r1}, cols {c0}-{c1} -> {rgb.shape[1]}x{rgb.shape[0]}")
    if args.crop_width:
        rgb = rgb[:, :args.crop_width]
    if args.invert:
        rgb = rgb.max() - rgb

    # Split frames along the ribbon's long axis (axis 0) *before* rotating, so
    # each frame is whole; rotation is applied per frame for the final layout.
    fr = args.frames
    h = rgb.shape[0] // fr
    rot = (args.rotate // 90) % 4
    for i in range(fr):
        part = rgb[i * h:(i + 1) * h] if fr > 1 else rgb
        if rot:
            part = np.rot90(part, k=rot)
        part = np.ascontiguousarray(part)
        suffix = f"_{i+1}" if fr > 1 else ""
        tif = f"{args.out}{suffix}.tif"
        write_tiff(part, tif)
        # write_preview(part, f"{args.out}{suffix}.png")  # Disabled per request
        print(f"wrote {tif}  ({part.shape[1]}x{part.shape[0]}, 16-bit RGB"
              f"{' inverted' if args.invert else ' raw/negative'})")


if __name__ == "__main__":
    main()
