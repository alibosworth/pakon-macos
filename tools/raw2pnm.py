#!/usr/bin/env python3
"""
raw2pnm.py — turn a raw Pakon scan dump (pakon_replay --scan output) into a
viewable PNM, and help find the image geometry.

The scan is a continuous line-by-line ribbon, so the first unknown is the row
**stride** (bytes per scan line). Render as 8-bit grayscale at a candidate
byte-width: at the correct stride, image features line up vertically; at a
wrong stride they shear diagonally. Once the byte-stride is known, the pixel
width = stride / (channels * bytes_per_sample).

Examples:
  # render 1200 lines as grayscale at a 9294-byte stride, into a small PNM:
  python3 tools/raw2pnm.py scan.raw --width 9294 --lines 1200 --out test.pnm
  # skip a leading calibration/leader region:
  python3 tools/raw2pnm.py scan.raw --width 9294 --offset 2000000 --lines 1200 -o t.pnm
  # optional: let it propose a stride (needs numpy):
  python3 tools/raw2pnm.py scan.raw --guess-stride

Modes: gray8 (default; 1 byte/px — best for finding the stride), rgb8 (3 B/px),
gray16le/gray16be (2 B/px), rgb16le/rgb16be (6 B/px). For 16-bit, output PNM is
big-endian as the format requires.
"""
import argparse
import sys


def render(args):
    bps = {"gray8": 1, "rgb8": 3, "gray16le": 2, "gray16be": 2,
           "rgb16le": 6, "rgb16be": 6}[args.mode]
    is_rgb = args.mode.startswith("rgb")
    is16 = "16" in args.mode
    little = args.mode.endswith("le")

    stride = args.width * bps          # bytes per output row
    with open(args.raw, "rb") as f:
        f.seek(args.offset)
        data = f.read(stride * args.lines) if args.lines else f.read()
    rows = len(data) // stride
    data = data[:rows * stride]
    if rows == 0:
        sys.exit("not enough data for one row at this width/offset")

    maxval = 65535 if is16 else 255
    magic = "P6" if is_rgb else "P5"

    if args.transpose or args.rotate or args.resize or args.planar:
        # The scan's line axis is across the film and the line-count axis is
        # along it (and the across axis is oversampled), so a frame comes out
        # sideways and stretched. Color is planar: each line is N concatenated
        # equal planes (R,G,B). Rotate/resize/deplanarize to view correctly.
        try:
            import numpy as np
        except ImportError:
            sys.exit("--transpose/--rotate/--resize/--planar need numpy")
        if is_rgb:
            sys.exit("these options take a gray mode input; use --planar for color")
        dt = (("<u2" if little else ">u2") if is16 else "u1")
        a = np.frombuffer(data, dtype=dt).reshape(rows, args.width)  # (rows, W)

        if args.planar:                    # split a line into N planes -> RGB
            pw = args.width // args.planar
            planes = [a[:, i * pw:(i + 1) * pw] for i in range(args.planar)]
            a = np.stack(planes[:3], axis=-1)            # (rows, pw, 3)

        if args.transpose:
            a = np.swapaxes(a, 0, 1)
        if args.rotate:
            a = np.rot90(a, k=(args.rotate // 90) % 4)
        if args.resize:
            tw, th = (int(v) for v in args.resize.lower().split("x"))
            yi = np.arange(th) * a.shape[0] // th
            xi = np.arange(tw) * a.shape[1] // tw
            a = a[yi][:, xi]                              # nearest-neighbour

        color = (a.ndim == 3)
        a = np.ascontiguousarray(a)
        oh, ow = a.shape[0], a.shape[1]
        with open(args.out, "wb") as out:
            out.write(f"{'P6' if color else 'P5'}\n{ow} {oh}\n{maxval}\n".encode())
            out.write(a.astype(">u2").tobytes() if is16 else a.tobytes())
        print(f"wrote {args.out}: {ow}x{oh} "
              f"{'rgb' if color else 'gray'}{'16' if is16 else '8'}"
              f"{' planar%d' % args.planar if args.planar else ''}")
        return

    with open(args.out, "wb") as out:
        out.write(f"{magic}\n{args.width} {rows}\n{maxval}\n".encode())
        if not is16:
            out.write(data)                # 8-bit: bytes map 1:1 to PNM samples
        else:
            mv = memoryview(data)          # repack to big-endian for PNM
            if little:
                buf = bytearray(len(data))
                buf[0::2] = mv[1::2]
                buf[1::2] = mv[0::2]
                out.write(buf)
            else:
                out.write(data)
    print(f"wrote {args.out}: {args.width}x{rows} {args.mode} "
          f"({rows} lines from offset {args.offset})")


def guess_stride(args):
    try:
        import numpy as np
    except ImportError:
        sys.exit("--guess-stride needs numpy (pip install numpy / apt install "
                 "python3-numpy), or just try candidate --width values by eye")

    import os
    fsize = os.path.getsize(args.raw)
    # Sample from deep in the file by default, to skip the leader/calibration
    # region at the start (real image content gives a clean row peak).
    off = args.offset if args.offset else min(fsize // 3, max(0, fsize - args.sample))
    with open(args.raw, "rb") as f:
        f.seek(off)
        sample = np.frombuffer(f.read(args.sample), dtype=np.uint8).astype(np.float32)
    print(f"sampling {len(sample)} bytes from offset {off} (file {fsize})")

    # High-pass (first difference) removes slow gradients/DC so the row-stride
    # periodicity dominates the autocorrelation.
    x = np.diff(sample)
    x -= x.mean()
    n = 1 << (len(x) - 1).bit_length()
    fft = np.fft.rfft(x, n)
    ac = np.fft.irfft(fft * np.conj(fft))[:args.max_stride + 1]
    # Normalize for the shrinking overlap at larger lags.
    lags = np.arange(len(ac))
    ac = ac / np.maximum(len(x) - lags, 1)
    ac[:args.min_stride] = 0

    # Find local maxima (real peaks), rank by height.
    lo, hi = args.min_stride, len(ac) - 1
    peaks = [(ac[L], L) for L in range(max(lo, 1), hi)
             if ac[L] > ac[L - 1] and ac[L] >= ac[L + 1]]
    peaks.sort(reverse=True)
    print("top stride candidates (bytes/line), strongest first:")
    seen = []
    for score, L in peaks:
        if all(abs(L - s) > 16 for s in seen):
            seen.append(L)
            facs = [f"{L//d}px@{d}Bpp" for d in (1, 2, 3, 6) if L % d == 0]
            print(f"  {L:6d}  (score {score:.3e})   {'  '.join(facs)}")
        if len(seen) >= 10:
            break
    print("\nThe true row stride should appear with its harmonics (2x, 3x...).")
    print("width = stride / (channels * bytes_per_sample); RGB16 => /6, RGB8 => /3.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw")
    ap.add_argument("--mode", default="gray8",
                    choices=["gray8", "rgb8", "gray16le", "gray16be",
                             "rgb16le", "rgb16be"])
    ap.add_argument("--width", type=int, help="pixels per row")
    ap.add_argument("--lines", type=int, default=1200,
                    help="rows to render (0 = all; default 1200)")
    ap.add_argument("--offset", type=int, default=0, help="skip leading bytes")
    ap.add_argument("--transpose", action="store_true",
                    help="transpose (90° + mirror); gray modes")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                    help="rotate the image by this many degrees (gray modes)")
    ap.add_argument("--resize", metavar="WxH",
                    help="resample to WxH (nearest), e.g. 3000x2000 (gray modes)")
    ap.add_argument("--planar", type=int, metavar="N",
                    help="treat each --width line as N concatenated planes and "
                         "build an RGB image (N=3 for planar R,G,B color)")
    ap.add_argument("-o", "--out", default="scan.pnm")
    ap.add_argument("--guess-stride", action="store_true")
    ap.add_argument("--sample", type=int, default=8 << 20,
                    help="bytes sampled for --guess-stride (default 8 MiB)")
    ap.add_argument("--min-stride", type=int, default=256)
    ap.add_argument("--max-stride", type=int, default=40000)
    args = ap.parse_args()

    if args.guess_stride:
        guess_stride(args)
    elif args.width:
        render(args)
    else:
        ap.error("give --width to render, or --guess-stride to propose one")


if __name__ == "__main__":
    main()
