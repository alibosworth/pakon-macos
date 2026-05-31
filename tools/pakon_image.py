#!/usr/bin/env python3
"""
pakon_image.py — decode a Pakon raw scan stream into 16-bit RGB TIFF(s).

The captured 0x86 image stream (pakon_replay --scan output) is, on this F-135:
  - 16-bit little-endian samples,
  - per-pixel INTERLEAVED RGB (confirmed: column autocorrelation peaks at
    lag 3/6/9), so samples run R,G,B,R,G,B,...
  - `--linewidth` samples per scan line (default 8000 = 16000-byte stride),
    giving width = linewidth//3 px (~2666); the last 0-2 samples are padding.

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
    chans = {"r": img[:, 0:n3:3], "g": img[:, 1:n3:3], "b": img[:, 2:n3:3]}
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
        write_preview(part, f"{args.out}{suffix}.png")
        print(f"wrote {tif}  ({part.shape[1]}x{part.shape[0]}, 16-bit RGB"
              f"{' inverted' if args.invert else ' raw/negative'})")


if __name__ == "__main__":
    main()
