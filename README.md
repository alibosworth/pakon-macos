# pakon-sane

A cross-platform (Linux + macOS) [SANE](http://www.sane-project.org/) backend
for the Kodak/Pakon **F-135** film scanner (and the "Plus" / F-235 / F-335
variants). This is an unofficial, clean-room reimplementation built from
documented protocol notes and our own USB captures.

> **Status: Phase 0 (scaffold).** Only the build skeleton, logging, and the
> framing struct/enums exist. Nothing talks to hardware yet. See
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
./build/pakon_probe          # prints "no device" with nothing attached
PAKON_DEBUG=4 ./build/pakon_probe   # with full trace logging
```

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
