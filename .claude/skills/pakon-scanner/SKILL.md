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

- Phases 0–1 done (cold/firmware path is moot per the auto-warm hardware).
- **Endpoint map is known** (warm `0f05:f235`, interface 0, 4 alt settings;
  see docs/PROTOCOL.md). Physical FX2 endpoints EP1/EP2/EP4/EP6/EP8. Hypothesis
  (unconfirmed): EP1 0x01/0x81 = command/status, EP2/EP4 OUT = host→device bulk,
  EP6/EP8 IN = image stream. The driver's chosen alt setting is unknown.
- **Phase 2 transport implemented:** `pakon_usb_claim(dev, ifc, alt)` /
  `pakon_usb_release`, and `pakon_usb_send/recv(dev, ep, ...)` which dispatch
  bulk vs interrupt based on the endpoint's type in the current alt, with
  timeouts, stall (PIPE) recovery via clear_halt, and trace hexdumps. The
  command endpoint is intentionally NOT hardcoded.
  - `pakon_probe --raw HEX --out 0xNN [--in 0xNN] [--alt N] [--timeout MS]`
    lets us probe endpoints empirically.
- **Empirical wall hit (`--probe-open`):** the open packet NAKs on *every* OUT
  endpoint in *every* alt setting (timeout, 0 bytes, device-side). So raw bulk
  is NOT the command path — the device needs an init step, and the 36-byte
  protocol is most likely **EP0 vendor control transfers** (matches the Windows
  IOCTL). Request codes are undocumented; **do not guess them**.
- **Phase 4 in progress.** Setup: Linux host runs a **Windows VM with the
  scanner passed through**; capture on the **host with usbmon** (passthrough
  URBs traverse the host controller). Tooling is ready:
  - `docs/CAPTURE_GUIDE.md` — host-usbmon→pcapng via dumpcap primary; in-VM
    USBPcap fallback; usbmon-text quick path for the small handshake.
  - `tools/analyze_capture.py` — ingests pcapng (via `tshark`), tshark TSV, or
    usbmon text; prints a URB timeline; **decodes control setup packets (the
    vendor request codes)** and 36-byte Pakon frames; summarizes distinct
    transfer/endpoint/request combos.
  - `analyze_capture.py` parses pcapng **natively** (no tshark needed) plus
    tshark TSV / usbmon text; `--commands` hides standard USB chatter; pairs
    setup↔completion by URB id; prints a bus/device inventory.
  - **First capture decoded:** firmware load `f235→f135` is standard FX2 fxload
    over EP0 — `0xA0` (internal RAM) + `0xA3` (external RAM) + CPUCS `0xE600`
    reset + `0xA4 wValue=0x00A1` renumerate (see docs/PROTOCOL.md). The
    operational descriptor is the clean 3-endpoint one (0x01 OUT, 0x81 IN,
    0x86 IN bulk).
  - **Scan capture decoded (the big one).** Operational `f135` = interface 0,
    3 bulk endpoints: **`0x01` OUT = command, `0x81` IN = reply/status,
    `0x86` IN = image stream**. Command frame wire format = `[type][count][data]`,
    length **2+count** (NOT padded to 36). **Open handshake confirmed** on EP1,
    verbatim per docs (`04 03 10 00 85`→`07 02 10 00`; `02 04 10 01 8f 00`→…;
    then PIC probes `04 03 <addr> 00 00`). The `0x85` is a param byte, not a
    checksum. A control `0xA4`/`0xA9` pair reads a 32-byte-chunked calibration/
    param table. See docs/PROTOCOL.md for all of it.
- **Phase 3 implemented (untested on hardware):**
  - `pakon_proto`: real `pakon_packet_build`/`_serialize`/`_parse`, wire length
    `2+count`, `pakon_wire_len`/`_addr`/`_status` helpers. No checksum (short
    frames carry none; the trailing byte is a param). Unit-tested in test_proto
    against the real open bytes.
  - `pakon_cmd.[ch]` (glue using both layers): `pakon_cmd`/`pakon_cmd_raw` send
    a frame on EP1 OUT, read the reply on EP1 IN. Endpoint constants
    `PAKON_EP_CMD_OUT/IN/IMAGE_IN` in pakon_usb.h.
  - `pakon_replay --open`: replays the captured open sequence and verifies each
    reply. Needs operational `0F05:F135` on the host.
- **Firmware load implemented (our own f235→f135).** Identities reclassified:
  **cold=`0F05:F235`** (bootstrap), **warm=`0F05:F135`** (operational) — see
  `PAKON_COLD_*`/`PAKON_WARM_*` and `pakon_is_warm_id` (f135 only). Approach:
  extract the captured FX2 control-transfer sequence to a `.pakfw` script
  (`analyze_capture.py --extract-firmware`) and replay it verbatim
  (`pakon_usb_load_firmware` → `pakon_probe --load-firmware f135.pakfw`), then
  wait for re-enumeration. `.pakfw` holds Kodak bytes → gitignored, regenerate
  from a capture.
- **Testing caveat:** the cold device must be on the HOST, not held by the VM
  (disable the VirtualBox USB filter / shut down the VM first).
- **Phase 5 TODO:** image bytes are NOT in the host-usbmon capture (usbfs
  passthrough drops large bulk-IN payloads; `0x86` chunks are 20480 bytes). Need
  an in-VM USBPcap capture for the image-stream format. Command vocabulary is
  known: `03 01 10` poll (1605×), `01 03 20/24 01 02` PIC writes, etc.

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
