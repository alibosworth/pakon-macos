# pakon-sane

A cross-platform (Linux + macOS) [SANE](http://www.sane-project.org/) backend
for the Kodak/Pakon **F-135** film scanner (and the "Plus" / F-235 / F-335
variants). Unofficial, clean-room reimplementation built from documented
protocol notes and our own USB captures.

> **Status:** Phases 0–5 complete. Full end-to-end scan works on hardware —
> firmware load, open handshake, scan drive, and image decode are all
> validated. Phase 6 (SANE backend shim) is next.

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

- **`.pakscan`** — a scan operation list extracted from a USB capture of the
  Windows driver performing a scan. It contains the ordered sequence of
  commands, control transfers, and image reads that the driver sends.
  `pakon_replay --scan` replays this verbatim against the live scanner to drive
  a real scan. Generate it from a capture with
  `analyze_capture.py --extract-scan`.

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

**4. Run a scan**

Load film into the scanner, then:

```sh
./build/pakon_replay --scan scan.pakscan --image scan.raw
```

Streams ~240 MB of raw image data per 4-frame strip. Watch for the progress
output; a couple of transfer errors at the very end are normal.

**5. Decode the image**

Requires Python 3, `numpy`, and ImageMagick (`magick`).

```sh
python3 tools/pakon_image.py scan.raw --rotate 90 --frames 4
```

Writes `frame_1.tif` … `frame_4.tif` as 16-bit RGB TIFFs — registered,
autocropped raw negatives, orange mask intact. Feed them to Negative Lab Pro,
darktable negadoctor, or similar for proper C-41 inversion.

Key decoder options:

| Flag | Default | Effect |
|------|---------|--------|
| `--frames N` | 1 | split the ribbon into N frames |
| `--rotate {90,180,270}` | 0 | rotate each output frame |
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
