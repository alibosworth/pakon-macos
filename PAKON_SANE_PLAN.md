# Implementation Plan: SANE Backend for the Pakon F-X35 Film Scanner

This document is the working spec for building a cross-platform (Linux + macOS) SANE
backend for the Kodak/Pakon F-135 (and "Plus" / F-235 / F-335 variants) film scanner.
It is written to be executed by Claude Code phase by phase.

## How to use this document (instructions for Claude Code)

- Work **one phase at a time**, in order. Do not start a phase until the previous
  phase's exit criteria are met and I (the human) have confirmed.
- Each phase has **exit criteria** that require evidence from real hardware or a real
  build — not just "code compiles." Several phases **cannot** be completed without me
  running something against the physical scanner. When you reach such a point, stop and
  tell me exactly what to run and what output to paste back.
- Do **not** write speculative code for layers whose protocol is still unknown. Where the
  protocol is undocumented (the scan/image path), build the *tracing* and *replay*
  scaffolding first so we can discover the protocol, then implement it.
- Keep the three architectural layers strictly separated (transport / protocol / SANE).
  No USB calls in the SANE layer; no SANE types in the protocol layer; no Pakon packet
  knowledge in the transport layer.
- Prefer small, testable commits. After each, state what was added and how to verify it.
- This is a clean-room reimplementation from a documented protocol and from our own USB
  captures. Do not copy GPL/driver source verbatim; the Intel HEX firmware blob is a
  device-bootstrapping artifact and is used as-is, not reverse-engineered.

## Background facts (known going in)

- Device is a Cypress/Anchor "EZ-USB" FX2-family chip. On cold power-on it enumerates in
  bootloader state (likely a default Cypress VID/PID), **not** as the scanner.
- After firmware download it re-enumerates as **VID 0x0F05, PID 0xF135** with **3 endpoints**.
- Firmware is shipped as **Intel HEX** files (in the FX35Package folder of the
  ktkaufman03/FX35 repo). The FX2 firmware-download protocol (vendor request 0xA0 to
  internal RAM) is standard and implemented by `fxload`/`cyusb` — we port that, not invent it.
- Documented packet frame (from Kai Kaufman's reverse-engineering blog):
  - byte 0 = `type` (enum: e.g. PH_CMD, PH_READ_STATUS, PH_INVALID)
  - byte 1 = `count` (data length, max 34)
  - bytes 2..(2+count) = `data`; `data[0]` is an **address** byte
  - total struct size = **36 bytes**
- Address enum: AD_HOST=0x10, AD_PICL=0x20, AD_BOOT_PICL=0x22, AD_PICM=0x24,
  AD_BOOT_PICM=0x26, AD_PICL_PLUS=0x40, AD_BOOT_PICL_PLUS=0x42, AD_PICM_PLUS=0x44,
  AD_BOOT_PICM_PLUS=0x46.
- Scanner status codes (4th byte of a scanner→host packet): 0 = success, 1 = not acked,
  2 = invalid packet, 3 = bad checksum, 4–6 = USB-related, 7 = host algo error,
  8 = success, 9 = bus error.
- The Windows IOCTL used for packet exchange was 0x222090 with a 36-byte response struct
  (a buffer-size bug there declared 64 — we avoid that class of bug by sizing exactly).
- **KNOWN GAP:** the actual scan path (calibration → frame detection → bulk image stream
  format) is NOT documented. This is the bulk of the original reverse-engineering work and
  must be discovered from our own captures (Phase 5).

## Repository layout (target)

```
pakon-sane/
  README.md
  LICENSE
  CMakeLists.txt
  firmware/
    f135.hex                 # copied from FX35Package (document provenance in README)
    README.md                # which file maps to which model
  include/
    pakon_usb.h              # transport layer
    pakon_proto.h            # protocol layer
    pakon_log.h              # hexdump/trace helpers
  src/
    pakon_usb.c
    pakon_proto.c
    pakon_log.c
  tools/
    pakon_probe.c            # standalone libusb test harness (NOT SANE)
    pakon_replay.c           # replays/extends captured handshakes
  backend/
    pakon.c                  # SANE backend entry points
    pakon.conf               # USB IDs
    dll.conf.fragment
  test/
    captures/                # *.pcap / usbmon dumps + annotated notes
    test_proto.c             # unit tests for framing/checksum (no hardware)
  docs/
    PROTOCOL.md              # living document of what we've decoded
    CAPTURE_GUIDE.md         # how to capture a Windows scan session
```

---

## Phase 0 — Project scaffold and environment

**Goal:** A building skeleton with the layering enforced, no hardware needed.

Tasks:
1. Create the repo layout above. Empty/stub functions are fine.
2. CMake build that produces: the static lib (`libpakon` = usb+proto+log), the two
   `tools/` binaries, and `test_proto`. Link libusb-1.0 (find via pkg-config).
3. `pakon_log.[ch]`: a `pakon_hexdump(tag, buf, len)` and a debug-level gate via env var
   `PAKON_DEBUG`. Every packet in/out will route through this from the start.
4. Stub `include/` headers with the structs/enums from the Background section
   (packet struct, address enum, status enum). Add `static_assert(sizeof(pakon_packet)==36)`.
5. `test/test_proto.c`: assert the struct size and (once written) checksum round-trips.
6. README documenting build steps for Linux and macOS, and the legal/provenance note on
   the firmware blob.

**Exit criteria:** `cmake --build` succeeds on the dev machine; `test_proto` passes the
struct-size assertion; `pakon_probe` runs and prints "no device" cleanly.

---

## Phase 1 — Transport: enumerate and firmware-load (HARDWARE REQUIRED)

**Goal:** Bring a cold scanner up to its warm `0F05:F135` identity from our own code.

Tasks:
1. In `pakon_usb.c`: open a libusb context, enumerate, and classify devices into
   "cold FX2 bootloader" vs "warm Pakon (0F05:F135)". We need to confirm the cold VID/PID.
   - **STOP POINT A:** Have me plug in the scanner and run `lsusb` (Linux) /
     `system_profiler SPUSBDataType` (macOS) **before any driver is loaded**, and paste the
     VID/PID it shows on cold boot. Do not guess this value into the code.
2. Implement Intel HEX parsing and FX2 RAM download (vendor request 0xA0): hold 8051 in
   reset (write 1 to CPUCS 0xE600), write HEX records, release reset (write 0).
   - Copy the correct `.hex` from FX35Package into `firmware/`; document the mapping.
3. After download, wait for re-enumeration and confirm the warm device appears with 3
   endpoints. Print the endpoint map (addresses, types, max packet sizes).

**Exit criteria (I will run this):** `pakon_probe --load-firmware` turns a freshly powered
cold scanner into `0F05:F135`, and prints its 3 endpoints. Paste the output.

**If blocked:** if re-enumeration doesn't happen, capture the control transfers and we
diff against a known FX2 fxload sequence before proceeding.

---

## Phase 2 — Transport: raw bulk I/O wrappers

**Goal:** Reliable `send`/`recv` over the right endpoints.

Tasks:
1. Identify which of the 3 endpoints are the command channel vs image/bulk-in. Annotate in
   `docs/PROTOCOL.md`. (Initial guess from endpoint directions; confirm empirically.)
2. Implement `pakon_usb_send` / `pakon_usb_recv` with explicit, exact buffer sizes, libusb
   timeouts, and full hexdump tracing through `pakon_log`. Never over-read a bulk IN.
3. Implement claim/release of the interface, with macOS notes (may need to detach/seize
   from any default class driver; document the entitlement/Info.plist situation if it bites).

**Exit criteria:** A `pakon_probe --raw` mode can send an arbitrary hex string and dump the
reply without crashing or stalling. Verified on hardware on at least one OS.

---

## Phase 3 — Protocol: framing, checksum, command primitive

**Goal:** Encode/decode the documented packet layer and reach the device's "Idle" state.

Tasks:
1. Implement packet build/parse for the 36-byte frame; implement the checksum (derive exact
   algorithm from the known-good byte sequences in the blog logs; e.g. the `04 03 10 00 85`
   open packet). Add round-trip unit tests in `test_proto.c` (no hardware).
2. Implement `pakon_cmd(dev, addr, payload, plen, resp)` that sends, reads, and checks the
   status byte; map status 9/3/etc to named errors.
3. Implement the **open handshake** by replaying the exact documented sequence
   (host: `04 03 10 00 85` → expect `07 02 10 00`; then the `02 04 10 01 8f 00` exchange,
   etc.) to drive the scanner to Idle. Put the sequence in `tools/pakon_replay.c`.

**Exit criteria (hardware):** `pakon_replay --open` reaches Idle: we observe the same
success status bytes the blog shows on a 32-bit reference session. Paste the trace.

---

## Phase 4 — Capture a real Windows scan (HARDWARE + WINDOWS REQUIRED)

**Goal:** Get ground-truth data for the undocumented scan path before writing scan code.

This phase is mostly me, not you. You produce the guide and the analysis tooling.

Tasks (you):
1. Write `docs/CAPTURE_GUIDE.md`: how to run a full scan under the working Windows driver
   (Kaufman's 64-bit driver + TLXClientDemo) while capturing USB with Wireshark/USBPcap,
   and separately how to capture with `usbmon`+Wireshark if a Linux box can host the VM
   with USB passthrough.
2. Write `tools/` analysis: a parser that ingests an exported capture (pcap or text),
   filters to our endpoints, and emits annotated packet sequences aligned to the
   open→calibrate→scan→image phases.

Tasks (me): perform 2–3 captures (different resolutions, color + B&W) and commit them to
`test/captures/` with notes on what I did when.

**Exit criteria:** At least one full, annotated scan capture committed, with the phase
boundaries (calibration start, first image bytes, end) marked.

---

## Phase 5 — Protocol: the scan state machine (THE HARD PART)

**Goal:** Reproduce a scan from our code and pull image bytes off the device.

Tasks:
1. From the Phase 4 captures, extend `docs/PROTOCOL.md` with the calibration sequence,
   the scan-start commands, and the **image-stream format** (framing, geometry, bit depth,
   how frame boundaries are signaled, whether on-device correction is applied).
2. Model the scan as an explicit state machine: `OPEN → CONFIGURE → CALIBRATE →
   SCAN_FRAMES → READ_IMAGE → DONE`, with `CANCEL` transitions. Implement transition by
   transition, replaying against hardware after each.
3. Implement image read: pump bulk-IN into a buffer, parse into raw frames, write to PNM/
   TIFF from `pakon_replay --scan` so we can eyeball real output.
4. Note: film is motor-fed as whole rolls — design CANCEL to handle film already in transit
   (may need to let feed finish rather than hard-abort).

**Exit criteria (hardware):** `pakon_replay --scan` produces a recognizable image file from
a real strip of film, on at least one OS. This is the make-or-break milestone.

---

## Phase 6 — SANE backend shim

**Goal:** Wrap the working protocol layer in SANE so standard frontends drive it.

Tasks:
1. Implement entry points in `backend/pakon.c`, each delegating to lower layers:
   - `sane_init`/`sane_exit` → libusb ctx lifecycle.
   - `sane_get_devices` → enumerate; firmware-load cold devices first.
   - `sane_open`/`sane_close` → claim + open handshake to Idle.
   - `sane_get_option_descriptor`/`sane_control_option` → **minimal** options first:
     resolution and mode (color/gray) only. Expand later.
   - `sane_get_parameters` → report geometry/depth derived from selected resolution.
   - `sane_start` → run CONFIGURE→CALIBRATE→SCAN.
   - `sane_read` → stream image bytes in chunks into the frontend's buffer.
   - `sane_cancel` → drive the CANCEL transitions safely.
2. Ship `pakon.conf` (USB IDs) and a `dll.conf` fragment. Document install paths for Linux
   (sane-backends dirs) and macOS.
3. Build the backend as a proper SANE dynamic backend against installed sane-backends.

**Exit criteria (hardware):** `scanimage -d pakon ... > out.pnm` yields a correct image via
the standard SANE path, on Linux. Then confirm on macOS (sane-backends via Homebrew).

---

## Phase 7 — Hardening, options, and reach

**Goal:** Make it usable, not just demoable.

Tasks:
1. Expand SANE options: bit depth, Digital ICE toggle (if drivable), multi-frame roll
   handling, preview mode, geometry/crop.
2. Robust error recovery: device unplug mid-scan, USB 3.0 controller quirks (the blog hit a
   USB-3-specific crash — verify our libusb path is clean), timeouts, re-init.
3. Support the variants: F-235/F-335 and the "Plus" models — wire the address enum
   `_PLUS` values and per-model firmware selection. Gate on whatever hardware I can test.
4. macOS packaging notes: codesigning/entitlements if a frontend needs them; Image Capture
   bridging is out of scope but note the path.
5. README: supported models matrix (tested vs theoretical), install instructions, the
   firmware provenance/legal note, and a clear "reverse-engineered, unofficial" disclaimer.

**Exit criteria:** A tagged 0.1 release that a third party with the same scanner can build
and use on Linux from the README alone.

---

## Standing risks to keep visible

- **Image-stream format is undocumented** (Phase 5). If on-device correction makes raw
  output unusable, we may need to decode Pakon's correction or accept raw + post-process.
- **macOS USB claiming**: a default class driver may grab the interface; may need detach,
  an Info.plist, or running the frontend with elevated rights. Test early (Phase 2).
- **Cold-boot VID/PID unknown** until I run lsusb (Phase 1, STOP POINT A). Don't hardcode a
  guess.
- **Checksum algorithm** is inferred from sample packets; validate hard in Phase 3 before
  trusting it for scan commands.
- **Single-tester hardware**: only the model(s) I physically have can be validated; mark
  everything else "theoretical" in the support matrix.

## What I (the human) must provide, and when

- Phase 1: cold-boot VID/PID via lsusb; run firmware-load test.
- Phase 3: run the open-handshake replay; paste traces.
- Phase 4: perform Windows scan captures; commit them.
- Phase 5–6: run scan replays and `scanimage`; paste output / share image files.
- Throughout: a physical scanner, a Windows environment (real or VM w/ USB passthrough)
  for the reference captures, and both a Linux and a macOS machine for the two targets.
