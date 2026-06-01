# pakon-sane

A cross-platform (Linux + macOS) [SANE](http://www.sane-project.org/) backend
for the Kodak/Pakon **F-135** film scanner (and the "Plus" / F-235 / F-335
variants). Unofficial, clean-room reimplementation built from documented
protocol notes and our own USB captures.

> **Status:** Phases 0–5 complete. Full end-to-end scan works on hardware —
> firmware load, open handshake, film advance, scan drive, and image decode
> are all validated on Linux and macOS. Phase 6 is a Python web service
> (FastAPI + browser UI) that wraps the existing C tools and image pipeline
> so any machine on the local network can drive the scanner. SANE backend
> for Linux follows.
>
> The decoder handles both the 4-frame and whole-roll scan modes, including
> Digital ICE IR channel removal, wrap-order de-interleaving, and per-zone
> trilinear registration.

## Architecture

Three strictly separated layers:

- **transport** (`pakon_usb`): libusb context, enumeration, FX2 firmware
  download, raw bulk I/O. No packet or SANE knowledge.
- **protocol** (`pakon_proto`): the command frame, encode/decode, command
  primitive. No USB and no SANE knowledge.
- **SANE** (`backend/pakon.c`): the backend shim (Phase 6), delegating downward.

`pakon_log` is a shared utility (tracing + the common `pakon_result` type) used
by both lower layers without coupling them to each other.

## Building

Requires a C11 compiler, CMake ≥ 3.16, `pkg-config`, and **libusb-1.0**.

### Linux (Debian/Ubuntu)

```sh
sudo apt install build-essential cmake pkg-config libusb-1.0-0-dev
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

### macOS (Homebrew)

```sh
brew install cmake pkg-config libusb
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

Produces: static `libpakon`, tools `pakon_probe` and `pakon_replay`, and unit
tests `test_proto` / `test_hex`.

## Usage

### Overview

The scanner has two firmware stages. On power-on it enumerates as a bare FX2
bootloader (`0F05:F235`). The working driver downloads a second-stage firmware
image over USB, after which the device re-enumerates as the operational scanner
(`0F05:F135`). Our tools replicate this sequence.

Two file types drive the process:

- **`.pakfw`** — a firmware replay script extracted from a USB capture of the
  Windows driver performing the firmware load. It contains the exact sequence
  of USB control transfers needed to bring the scanner from cold (`0F05:F235`)
  to operational (`0F05:F135`). Generate it once from a capture with
  `analyze_capture.py --extract-firmware`; reuse it every session. Not
  committed here because it contains Kodak firmware bytes — see
  `firmware/README.md`.

- **`.pakscan`** — an operation script extracted from a USB capture of the
  Windows driver. Two kinds:
  - **Scan script** — ordered sequence of commands, control transfers, and
    image reads. `pakon_replay --scan` replays it verbatim to drive a real
    scan. Generate with `analyze_capture.py --extract-scan`.
  - **Advance script** — motor command sequence for film transport.
    `pakon_replay advance.pakscan` replays it and then loops the
    start/poll/finalize sequence for as many frames as needed. Generate by
    capturing an advance operation and extracting with `analyze_capture.py`.

### Step-by-step

**1. List devices**

```sh
./build/pakon_probe --list
```

With the scanner plugged in you should see `0f05:f235` (cold/bootstrap). If
the device is already warm (`0f05:f135`) from a previous session, skip step 2.

**2. Load firmware**

```sh
# Linux (needs privileges for libusb)
sudo ./build/pakon_probe --load-firmware f135.pakfw

# macOS (no sudo needed)
./build/pakon_probe --load-firmware f135.pakfw
```

The scanner re-enumerates as `0f05:f135`. Confirm with `--list`.

**3. Verify the open handshake**

```sh
./build/pakon_replay --open
```

Should print `OK` for each step and reach `Idle`.

**4. Advance film**

To transport film to the desired position (e.g. to the first frame):

```sh
./build/pakon_replay advance.pakscan            # advance 1 frame
./build/pakon_replay advance.pakscan --steps N  # advance N frames
```

Each step sends the start command, polls until the scanner signals the frame
is in position, then sends the finalize command. `--limit SEC` sets a
wall-clock safety cap (default 60 s). The advance duration (how far each step
moves the film) is set by the TLX software in seconds and is encoded in the
`.pakscan` script.

**5. Run a scan**

Load film into the scanner, then:

```sh
./build/pakon_replay --scan scan.pakscan --image scan.raw
```

Streams ~240 MB per 4-frame strip, or ~1.2 GB for a whole roll. A couple of
transfer errors at the very end are normal.

**6. Decode the image**

**Option A — web UI (recommended):** start the web service (see below) and
open `http://localhost:8000` in a browser. Upload the `.raw` file, set the
frame count, click Process, and download individual TIFFs or a zip of all frames.

**Option B — command line:** requires Python 3, `numpy`, and ImageMagick (`magick`).

```sh
# 4-frame strip
python3 tools/pakon_image.py scan.raw --rotate 90 --frames 4 --resample-to 3000x2000

# Whole roll (24 frames)
python3 tools/pakon_image.py fullroll.raw --rotate 90 --frames 24 --resample-to 3000x2000
```

Writes `frame_1.tif` … `frame_N.tif` as 16-bit RGB TIFFs — registered,
autocropped raw negatives, orange mask intact. Feed them to Negative Lab Pro,
darktable negadoctor, or similar for proper C-41 inversion.

The decoder automatically handles:
- **Digital ICE IR channel** — a ~658 px neutral-grayscale band embedded in
  each scan line for dust/scratch detection; detected and removed automatically
- **Wrap-order de-interleaving** — in whole-roll mode the IR band lands in the
  middle of the line, wrapping the visible image; the decoder rejoins the halves
  at the correct sensor seam
- **Per-zone trilinear registration** — the two wrapped halves come from
  opposite CCD tap ends with different R/G/B line offsets and are registered
  independently (eliminates colour ghosting at edges)
- **Aspect ratio correction** — raw pixels are non-square; `--resample-to
  3000x2000` outputs correct 3:2 geometry matching Pakon's native resolution

Key decoder options:

| Flag | Default | Effect |
|------|---------|--------|
| `--frames N` | 1 | split the ribbon into N frames (uses gap detection) |
| `--rotate {90,180,270}` | 0 | rotate each output frame |
| `--resample-to WxH` | off | resample to exact size (use `3000x2000` for 35mm) |
| `--register` / `--no-register` | on | co-register the trilinear R/G/B sensor lines |
| `--autocrop` / `--no-autocrop` | on | strip leader, blank pre-load scan, and gate margin |
| `--invert` | off | quick linear positive (preview only — not real C-41) |
| `-o PREFIX` | `frame` | output filename prefix |

### Debug logging

Set `PAKON_DEBUG=0..4` to control verbosity. Level 4 hexdumps every packet.

```sh
PAKON_DEBUG=3 ./build/pakon_probe
```

On Linux, pass the env explicitly with `sudo`:

```sh
sudo PAKON_DEBUG=3 ./build/pakon_probe
```

## Web service

The web service runs on the machine with the scanner plugged in and exposes a
browser UI for the full workflow: firmware load, scan, and image processing.
Any device on the local network can then open it.

```sh
pip install fastapi uvicorn python-multipart numpy pillow
uvicorn web.app:app --host 0.0.0.0 --port 8000
```

Open `http://<host>:8000`. The UI shows scanner connection state, lets you load
firmware, trigger a scan with a live progress bar (bytes received), upload or
reprocess a `.raw` file, set frame count, and download individual frames or a
zip of all frames as 16-bit TIFFs.

The server calls the compiled `pakon_probe` / `pakon_replay` binaries for
hardware control and runs the Python image pipeline in a thread pool for processing.
Build the C tools first (`cmake --build build`).

## Firmware

See `firmware/README.md` for provenance and the legal note. The `.pakfw` route
(replay from capture) is the practical path for the F-135; the Intel HEX
(`.hex`) route remains available for clean redistribution.

## License

**TBD.** SANE backends are conventionally GPL; the license will be chosen
before any release. See `LICENSE`.

## Disclaimer

Reverse-engineered and unofficial. Not affiliated with or endorsed by Kodak or
Pakon. The firmware blob is a third-party device-bootstrapping artifact used
as-is; see `firmware/README.md`.
