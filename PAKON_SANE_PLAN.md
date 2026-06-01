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

## Phase 6 — Python web service

**Goal:** A web service running on the machine with the scanner plugged in, accessible
from any browser on the local network — no native app, no Homebrew, no Xcode. This
replaces the abandoned Swift macOS app. The existing C tools (`pakon_probe`,
`pakon_replay`) and Python image pipeline (`pakon_image.py`) do all the heavy lifting;
the web layer is a thin wrapper.

**Architecture:** FastAPI server (`web/`) calling out to the compiled binaries via
subprocess for hardware control and running the image pipeline in a thread pool.
A minimal HTML/JS frontend (no build step, single `index.html`) talks to a REST + SSE API.

Tasks:
1. `web/app.py` — FastAPI application with the following endpoints:
   - `GET /api/status` — poll scanner state (`disconnected` / `cold` / `warm`) via
     subprocess to `pakon_probe --list` or `pyusb` VID/PID check.
   - `POST /api/firmware` — run `pakon_probe --load-firmware resources/f135.pakfw`;
     stream stdout progress via Server-Sent Events.
   - `POST /api/scan` — run `pakon_replay --scan resources/36frames.pakscan --image
     <tmpfile>`; stream bytes-written (file size poll) as SSE progress events.
   - `POST /api/process` — accept a `.raw` path or multipart upload plus `frames`,
     `rotate`, `resample` params; run decode in a `ProcessPoolExecutor`; stream step
     progress via SSE.
   - `GET /api/frames/{n}` — return the n-th decoded frame as a TIFF download.
   - `GET /api/export` — return all decoded frames as a zip.
2. `web/static/index.html` — single-page UI:
   - Status badge (colour-coded disconnected / cold / warm), auto-polls `/api/status`.
   - Firmware load button (visible when cold), progress bar.
   - Scan button (visible when warm), live bytes-received progress bar.
   - Frame count stepper (1–72) and "Process" button; accepts a dropped or uploaded
     `.raw` file as an alternative to a just-completed scan.
   - Scrollable thumbnail grid; click to download individual TIFF.
   - "Export all" button to download zip.
3. `web/image.py` — thin wrapper importing `tools/pakon_image.py` decode logic;
   exposes a single `decode(path, frames, rotate, resample, progress_cb)` function
   so the FastAPI worker can call it without subprocess overhead.
4. `web/README.md` — setup and launch instructions (pip install, how to point it at
   the built C tools, running behind a LAN reverse-proxy if desired).

**Exit criteria:** `uvicorn web.app:app` running on the Linux box; open the URL on a
separate laptop; plug in scanner, load firmware, run a scan, and download TIFFs —
without touching a terminal on the scanning machine after startup.

---

## Phase 7 — SANE backend shim (Linux / power users)

**Goal:** Wrap the working protocol layer in SANE so standard frontends (`scanimage`,
`gscan2pdf`, etc.) drive it on Linux. Lower priority than the Mac app — Linux users can
already use `pakon_replay` directly.

Tasks:
1. Implement entry points in `backend/pakon.c`, each delegating to lower layers:
   - `sane_init`/`sane_exit` → libusb ctx lifecycle.
   - `sane_get_devices` → enumerate; firmware-load cold devices first.
   - `sane_open`/`sane_close` → claim + open handshake to Idle.
   - `sane_get_option_descriptor`/`sane_control_option` → minimal options first.
   - `sane_start` → run CONFIGURE→SCAN.
   - `sane_read` → stream image bytes into the frontend's buffer.
   - `sane_cancel` → drive the CANCEL transitions safely.
2. Ship `pakon.conf` (USB IDs) and a `dll.conf` fragment.
3. Build as a proper SANE dynamic backend against installed sane-backends.

**Exit criteria (hardware):** `scanimage -d pakon > out.pnm` yields a correct image on Linux.

---

## Phase 8 — Hardening, options, and reach

**Goal:** Make both the Mac app and the SANE backend robust for real-world use.

Tasks:
1. Scan modes: whole roll, fixed frame count (4/5/6), preview.
2. Robust error recovery: device unplug mid-scan, timeouts, re-init, film jam handling
   (use the advance command to clear).
3. Support the variants: F-235/F-335 and the "Plus" models — per-model firmware selection,
   address enum `_PLUS` values. Gate on available hardware.
4. README: supported models matrix (tested vs theoretical), install instructions,
   firmware provenance/legal note, "reverse-engineered, unofficial" disclaimer.

**Exit criteria:** A tagged 0.1 release usable by a third party on macOS from the README alone.

---

## Standing risks to keep visible

- ~~**Image-stream format is undocumented** (Phase 5).~~ **Resolved.** Format fully
  decoded: 16-bit LE, B,R,G interleaved, trilinear CCD co-registered. Raw negative
  output; C-41 inversion left to dedicated tools.
- ~~**macOS USB claiming**.~~ **Resolved.** macOS works without sudo and without any
  Info.plist or driver detach — libusb claims the interface cleanly (tested 2026-05-31).
- ~~**Cold-boot VID/PID unknown**.~~ **Resolved.** Cold = `0F05:F235`, warm = `0F05:F135`.
- ~~**Checksum algorithm** inferred.~~ **Resolved.** Short frames carry no checksum; the
  trailing byte is a command param. Confirmed from the full 2218-command capture set.
- **Single-tester hardware**: only the F-135 has been validated; F-235/F-335/Plus remain
  theoretical. Mark clearly in the support matrix.
- **subprocess latency for status polling**: calling `pakon_probe --list` on every
  `/api/status` poll adds ~100 ms per call. If too slow, switch to a `pyusb` VID/PID
  check in-process, or keep a background thread that polls and caches the state.
- **Dual-tap CCD colour seam (full-roll scan mode)**: The whole-roll scan mode uses
  a wider CCD readout that includes a Digital ICE IR channel (~658 px, neutral
  grayscale, constant down the roll). The IR band sits mid-line and the two visible
  halves come from opposite CCD taps with different per-channel gain (L/R ratio ≈
  R:0.60, G:1.33, B:1.15), producing a hard colour seam. Spatial de-wrap and
  per-zone trilinear registration are implemented; colour calibration across the seam
  is **not yet implemented** — needs a new scan for clean verification.

## What I (the human) must provide, and when

- ~~Phase 1: cold-boot VID/PID; firmware-load test.~~ Done.
- ~~Phase 3: open-handshake replay trace.~~ Done.
- ~~Phase 4: Windows scan captures.~~ Done.
- ~~Phase 5: scan replay + image decode.~~ Done (Linux + macOS).
- **Phase 6**: start the web service on the Linux box; open it in a browser; plug in scanner, load firmware, scan, get TIFFs.
- **Phase 7**: run `scanimage -d pakon` end-to-end on Linux.
- Throughout: a physical scanner and both a Linux and a macOS machine.
