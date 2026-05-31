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

    out = open(args.out, "wb")
    maxval = 65535 if is16 else 255
    magic = "P6" if is_rgb else "P5"
    out.write(f"{magic}\n{args.width} {rows}\n{maxval}\n".encode())

    if not is16:
        out.write(data)                # 8-bit: bytes map 1:1 to PNM samples
    else:
        # repack samples to big-endian (PNM 16-bit is big-endian)
        mv = memoryview(data)
        buf = bytearray(len(data))
        if little:
            buf[0::2] = mv[1::2]
            buf[1::2] = mv[0::2]
            out.write(buf)
        else:
            out.write(data)
    out.close()
    print(f"wrote {args.out}: {args.width}x{rows} {args.mode} "
          f"({rows} lines from offset {args.offset})")


def guess_stride(args):
    try:
        import numpy as np
    except ImportError:
        sys.exit("--guess-stride needs numpy (pip install numpy), or just try "
                 "candidate --width values by eye")
    with open(args.raw, "rb") as f:
        f.seek(args.offset)
        sample = np.frombuffer(f.read(args.sample), dtype=np.uint8).astype(np.float32)
    sample -= sample.mean()
    # autocorrelation via FFT; the row stride shows up as a strong peak
    n = 1 << (len(sample) - 1).bit_length()
    fft = np.fft.rfft(sample, n)
    ac = np.fft.irfft(fft * np.conj(fft))[:args.max_stride]
    ac[:args.min_stride] = 0
    order = np.argsort(ac)[::-1]
    print("top stride candidates (bytes/line):")
    seen = []
    for idx in order:
        if all(abs(idx - s) > 8 for s in seen):
            seen.append(int(idx))
            print(f"  {int(idx):6d}  (score {ac[idx]:.3e})")
        if len(seen) >= 8:
            break
    print("\ndivide a stride by 1/2/3/6 bytes-per-pixel to get the pixel width.")


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
