# pakon-sane

A cross-platform (Linux + macOS) [SANE](http://www.sane-project.org/) backend
for the Kodak/Pakon **F-135** film scanner (and the "Plus" / F-235 / F-335
variants). This is an unofficial, clean-room reimplementation built from
documented protocol notes and our own USB captures.

> **Status: Phase 1 (transport), paused at STOP POINT A.** Build skeleton,
> logging, framing struct/enums (Phase 0), plus real USB enumeration, a
> device-listing diagnostic, warm-device open + endpoint dump, an Intel HEX
> parser (unit-tested), and the FX2 firmware-download pipeline — the last is
> gated until the scanner's *cold* VID/PID is confirmed on real hardware. See
> `PAKON_SANE_PLAN.md` for the full phased plan.

## Architecture

Three strictly separated layers — no leakage between them:

- **transport** (`pakon_usb`): libusb context, enumeration, FX2 firmware
  download, raw bulk I/O. No packet or SANE knowledge.
- **protocol** (`pakon_proto`): the 36-byte command frame, checksum,
  encode/decode. No USB and no SANE knowledge.
- **SANE** (`backend/pakon.c`): the backend shim (Phase 6), delegating downward.

`pakon_log` is a shared utility (tracing + the common `pakon_result` type) that
both lower layers use without depending on each other.

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

This produces the static `libpakon`, the `pakon_probe` and `pakon_replay`
tools, and the `test_proto` unit test.

## Trying it (Phase 0)

```sh
./build/pakon_probe          # classify; prints "no device" with nothing attached
./build/pakon_probe --list   # dump every USB device (use this for STOP POINT A)
PAKON_DEBUG=4 ./build/pakon_probe   # with full trace logging
```

### STOP POINT A (Phase 1, hardware)

Before the firmware-download path can be enabled, the scanner's **cold**
(pre-firmware, FX2 bootloader) USB VID/PID must be confirmed on real hardware —
it must not be guessed. Power-cycle the scanner and, *before any driver loads*,
run `pakon_probe --list` (or `lsusb` / `system_profiler SPUSBDataType`) and note
the new device's `vid:pid`. That value then goes into `PAKON_COLD_VID/PID` in
`include/pakon_usb.h`, after which `pakon_probe --load-firmware <f.hex>` can run.

`PAKON_DEBUG` ranges `0` (errors only, default) to `4` (trace; every packet
hexdumped).

## Firmware

The scanner is an EZ-USB FX2 device that needs an Intel HEX firmware image
downloaded to it before it enumerates as `0F05:F135`. See `firmware/README.md`
for the provenance and legal note — no blob is committed until Phase 1.

## License

**TBD.** SANE backends are conventionally GPL; the license is to be chosen
before any release. See `LICENSE`.

## Disclaimer

Reverse-engineered and unofficial. Not affiliated with or endorsed by Kodak or
Pakon. The firmware blob is a third-party device-bootstrapping artifact used
as-is; see `firmware/README.md`.
