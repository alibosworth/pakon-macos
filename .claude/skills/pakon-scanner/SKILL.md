---
name: pakon-scanner
description: >
  Working knowledge for the pakon-sane project — a clean-room cross-platform
  (Linux + macOS) SANE backend for the Kodak/Pakon F-X35 film scanner. Use when
  implementing, debugging, building, or testing any part of this repo:
  architecture/layering rules, build & test commands, the documented protocol,
  hard-won hardware facts about the test scanner, and the dev/sync workflow.
---

# Pakon F-X35 SANE backend — project guide

Clean-room reimplementation of a SANE backend for the Kodak/Pakon **F-135**
(and F-235/F-335/"Plus") film scanner, from documented protocol notes + our own
USB captures. The full phased plan is in `PAKON_SANE_PLAN.md` (read it before
starting a new phase). Living protocol notes are in `docs/PROTOCOL.md`.

## Golden rules (from the plan)

- **Work one phase at a time, in order.** Several phases need real hardware;
  stop and tell the human exactly what to run when you hit such a point.
- **Strict 3-layer separation, no leakage:**
  - transport (`pakon_usb`): libusb, enumeration, firmware download, bulk I/O.
    No packet or SANE knowledge.
  - protocol (`pakon_proto`): the 36-byte frame, checksum, encode/decode. No
    libusb, no SANE.
  - SANE (`backend/pakon.c`, Phase 6): delegates strictly downward.
  - `pakon_log` is a shared utility (tracing + the common `pakon_result` enum);
    both lower layers use it without depending on each other.
- **No speculative code** for undocumented layers. The scan/image path is
  unknown — build tracing/replay scaffolding to discover it (Phases 4–5), don't
  invent it. The FX2 firmware-download protocol and Intel HEX *are* standard, so
  those are fine to implement.
- **Every packet routes through `pakon_log`** (hexdump/trace) from the start;
  gate verbosity with `PAKON_DEBUG=0..4`.
- Small, testable commits. Don't hardcode guessed values (e.g. an unconfirmed
  VID/PID) — gate on them instead.

## Build & test

Deps: C11 compiler, CMake ≥ 3.16, pkg-config, libusb-1.0.
- macOS: `brew install cmake pkg-config libusb`
- Debian/Ubuntu: `sudo apt install build-essential cmake pkg-config libusb-1.0-0-dev`

```sh
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

Builds: static `libpakon` (log+proto+hex+usb), tools `pakon_probe` &
`pakon_replay`, unit tests `test_proto` + `test_hex` (both hardware-free). The
backend target is deferred to Phase 6 (built against installed sane-backends).
CMake uses `PkgConfig::LIBUSB` (imported target) so the static lib's libusb dep
propagates to test binaries with correct search dirs.

`pakon_probe` modes: default = classify + (if warm) dump endpoint map;
`--list` = dump all USB devices; `--load-firmware HEX` = gated FX2 download;
`--raw HEX` = Phase 2. `pakon_replay`: `--open` (Phase 3), `--scan` (Phase 5).

## Hardware facts learned (IMPORTANT — they change the plan)

- The **test scanner is physically an F-135** but enumerates **warm as
  `0f05:f235`** (class `0xff`, 3 endpoints, `bcdDevice aa.07`). The USB **PID is
  set by the booted firmware, not the physical model** — never infer model from
  PID; real model is a protocol-layer query.
- Treat **any `0f05:Fx35`** (F135/F235/F335) as warm: see `pakon_is_warm_id()`.
- The unit **auto-warms from an onboard EEPROM** (FX2 "C2" mode): `dmesg` on
  replug shows a *single* enumeration straight to `0f05:f235`, with **no** cold
  Cypress `04b4:*` device first. Therefore:
  - **Cold state is not practically reachable** (would need hardware EEPROM
    disable) and **is not needed**.
  - **STOP POINT A (capturing the cold VID/PID) is effectively moot.**
    `PAKON_COLD_VID/PID` stay `0`; the host-side FX2 firmware-download path is a
    **fallback**, not on the critical path. Work against the warm device.
- FX2 endpoint gotcha: bulk endpoints often live in a **non-default alternate
  setting** (altsetting 0 may be empty). Always scan all interfaces/altsettings
  (`cache_endpoints` does this) and select the right alt in Phase 2.
- On **Linux, `libusb_open` needs privileges** — run `pakon_probe` with `sudo`
  (or add a udev rule). With `sudo`, pass env explicitly:
  `sudo PAKON_DEBUG=3 ./build/pakon_probe`.

## Documented protocol (from notes; see docs/PROTOCOL.md)

- 36-byte frame: `[0]=type, [1]=count (≤34), [2..35]=data` where `data[0]` is an
  address byte. `static_assert(sizeof(pakon_packet)==36)`.
- Address enum (known): AD_HOST=0x10, AD_PICL=0x20, AD_BOOT_PICL=0x22,
  AD_PICM=0x24, AD_BOOT_PICM=0x26, plus `_PLUS` 0x40/0x42/0x44/0x46.
- Status byte: 0 success, 1 not acked, 2 invalid, 3 bad checksum, 4–6 USB,
  7 host-algo, 8 success, 9 bus error.
- `type` byte names (PH_CMD/PH_READ_STATUS/PH_INVALID) are documented but their
  **numeric values are unconfirmed**; observed 0x04 on host cmds, 0x07 on reply.
- **Checksum algorithm is TBD** — derive & hard-validate in Phase 3 from
  known-good packets (e.g. open packet `04 03 10 00 85` → expect `07 02 10 00`).
- **Scan/image path is UNKNOWN** — discover via Windows captures (Phase 4) and
  model as a state machine OPEN→CONFIGURE→CALIBRATE→SCAN_FRAMES→READ_IMAGE→DONE
  (Phase 5). This is the make-or-break part.

## Status & next step

- Phase 0 (scaffold) and Phase 1 transport are done **except** what cold/
  firmware needed — which the auto-warm hardware makes unnecessary. Real USB
  enumeration, warm-family matching, device listing, warm open, and full
  endpoint enumeration (all interfaces/altsettings) are implemented; Intel HEX
  parser is implemented + unit-tested; FX2 download is implemented but gated.
- **Next:** get the warm endpoint map off the hardware, classify command (OUT)
  vs image (bulk IN) endpoints, then **Phase 2** (`pakon_usb_send/recv` with
  exact sizing, timeouts, alt-setting selection, interface claim; macOS class-
  driver detach notes), then **Phase 3** (framing + checksum + open handshake).

## Dev / sync workflow

- Repo origin: forgejo (`origin/main`). The human **codes on a Mac** but the
  **scanner is on a separate Linux box reached over SSH**, with the repo cloned
  there too.
- To get changes to the hardware box: commit on `main` and **push**; the human
  `git pull`s on the Linux box and rebuilds. Keep commits small with a
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.
- When you need the human to run something on hardware, give exact copy-paste
  commands and say what output to paste back.

## Key files

- `include/` `pakon_log.h` (log + `pakon_result`), `pakon_proto.h` (frame/enums),
  `pakon_hex.h`, `pakon_usb.h` (transport API + VID/PID defines).
- `src/` matching `.c` files. `tools/pakon_probe.c`, `tools/pakon_replay.c`.
- `backend/pakon.c` (Phase 6 stub), `backend/pakon.conf` (warm USB IDs).
- `test/test_proto.c`, `test/test_hex.c`, `test/captures/` (Phase 4 dumps).
- `firmware/` (HEX provenance/legal note; no blob committed — not needed given
  auto-warm). `docs/PROTOCOL.md`, `docs/CAPTURE_GUIDE.md`.
