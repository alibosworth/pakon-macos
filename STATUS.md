# Project status — feed this to Claude at the start of next session

Working spec is `PAKON_SANE_PLAN.md`; living protocol notes in `docs/PROTOCOL.md`;
the project skill `.claude/skills/pakon-scanner/SKILL.md` has the operational
guide. This file is the short "where we left off" snapshot.

_Last updated: 2026-05-31. Last commit on `main`: `bc1d2d5`._

## Phase status

- **Phase 0 (scaffold):** done. CMake build, libpakon, tools, unit tests.
- **Phase 1 (transport / enumerate / firmware-load):** done. Real enumeration,
  warm open, endpoint map, Intel HEX parser (unit-tested), and our own
  `f235→f135` firmware load via captured `.pakfw` replay (awaiting hardware test).
- **Phase 2 (raw bulk I/O):** done. `pakon_usb_claim`/`_release`,
  `pakon_usb_send/recv(dev, ep, …)` dispatching bulk vs interrupt, with
  timeout + stall recovery + tracing.
- **Phase 3 (framing + command primitive + open handshake):** DONE and
  **VALIDATED ON HARDWARE** (2026-05-31). `pakon_replay --open` reached Idle —
  all 5 steps OK, replies byte-for-byte matching the capture. `pakon_proto`
  build/serialize/parse (wire = 2+count); `pakon_cmd.[ch]` over EP1.
  End-to-end proven from our code: load-firmware → enumerate → claim → open.
- **Phase 4 (capture):** essentially done for the command path. `tools/
  analyze_capture.py` parses pcapng natively. Captures live on the Linux box;
  the scan capture was copied to this Mac at `/Volumes/Video/pakon_scan.pcapng`
  (the firmware-only one at `/Volumes/Video/pakon_full.pcapng`).
- **Phase 5 (scan state machine + image):** NOT started. Blocked on an in-VM
  USBPcap capture for image bytes (see hardware facts).
- **Phase 6 (SANE backend), 7 (hardening):** not started.

## Confirmed hardware/protocol facts (from real captures)

- **Two firmware stages.** Physical unit is an **F-135**. It EEPROM-boots to a
  bootstrap **`0F05:F235`** (no strings), then the driver downloads stage-2
  firmware (standard FX2 fxload: `0xA0` internal RAM + `0xA3` external RAM +
  CPUCS `0xE600` reset + `0xA4 wValue=0x00A1` renumerate) and it re-enumerates
  as operational **`0F05:F135` "Pakon F135-USB Film Scanner"**. So
  **cold = f235, warm = f135**. The full firmware byte stream (1102 writes,
  ~14.8 KB) IS present in `pakon_scan.pcapng` (device 12) and is extractable.
- **Operational `f135` endpoints** (interface 0, single setting, 3 bulk eps):
  `0x01` OUT = command, `0x81` IN = reply/status, `0x86` IN = image stream.
- **Command frame wire format:** `[type][count][count data bytes]`, on-wire
  length = **2 + count** (NOT padded to 36). `data[0]` = address byte; in a
  reply `data[1]` = status. The trailing byte is a param, **not a checksum**
  (`04 03 10 00 85` vs `04 03 44 00 00`). No checksum found for short frames.
- **Open handshake (verbatim, on EP1):** `04 03 10 00 85`→`07 02 10 00`;
  `02 04 10 01 8f 00`→`07 02 10 00`; then PIC presence probes
  `04 03 <addr> 00 00`→`07 02 <addr> <present>` (AD_PICM_PLUS 0x44,
  AD_BOOT_PICM_PLUS 0x46, AD_PICM 0x24, …).
- **Command vocabulary** (2218 exchanges in a 4-frame scan): `03 01 10` poll
  (1605×), `01 03 20/24 01 02` PIC writes, `03 01 20`/`03 01 24` polls, etc.
- **Calibration/param read:** EP0 vendor control `0xA4` (OUT trigger,
  wValue=0x00A5, wIndex=0x1234) + `0xA9` (IN) reading 32-byte chunks at
  increasing offsets. To decode in Phase 5.
- **Image data NOT captured by host usbmon** under VirtualBox usbfs passthrough
  (large bulk-IN payloads aren't snapshotted; `0x86` chunks are 20480 bytes).
  Phase 5 image format needs an **in-VM USBPcap** capture.

## Environment / workflow

- Repo: forgejo `origin/main`. Code on this Mac; scanner on a Linux box over
  SSH; sync by commit + push, pull on the box.
- Scanner is passed to a **VirtualBox Windows VM** (USB filter vendor 0f05).
  VirtualBox retains it across the f235→f135 re-enumeration; scans work there.
- Linux needs `sudo` for libusb / usbmon. dumpcap drops privileges → capture to
  `/tmp` then `chown`.

## Firmware load — DONE and VALIDATED on hardware

cold=`0F05:F235`/warm=`0F05:F135`; `analyze_capture.py --extract-firmware
f135.pakfw` extracts the captured FX2 sequence; `pakon_usb_load_firmware`
replays it and waits for re-enumeration. Confirmed working on the box:
`--load-firmware f135.pakfw` brought f235→f135, then `--open` reached Idle.
(`.pakfw` gitignored — regenerate from a capture. Free the device from the VM
first: shut down the VM / drop the 0f05 USB filter, replug, `lsusb`→0f05:f235.)

To repeat the working test:
```sh
python3 tools/analyze_capture.py /tmp/pakon_scan.pcapng --extract-firmware f135.pakfw
sudo ./build/pakon_probe --load-firmware f135.pakfw
sudo ./build/pakon_probe            # warm f135 + endpoints 0x01/0x81/0x86
sudo ./build/pakon_replay --open    # reaches Idle
```

## NEXT: Phase 5 (reproduce a scan, read the image)

Scan flow is now mapped (see docs/PROTOCOL.md): OPEN → PARAM READ (0xA4/0xA9) →
CONFIGURE (PICL/PICM register writes + polls) → SCAN (interleaved status polls +
`0x86` 20480-byte image reads; ~240 MB / 4 frames). Crucially, **commands are
interleaved with image reads** (2070 EP1 cmds during streaming), so it's an
ordered operation loop, not "start then drain".

Recommended implementation — **operation replay**:
1. Add `analyze_capture.py --extract-scan OUT.pakscan`: emit the full ordered
   device-13 operation list — EP1 OUT (bytes), EP1 IN (read+expect), `0x86`
   IN (read N), `0xA4`/`0xA9` control — like the firmware extractor but for the
   whole session.
2. Add `pakon_replay --scan FILE`: replay each operation in order against the
   device, writing `0x86` payloads to a raw image file. (We can now drive the
   device, so we get the real image bytes even though the capture lacked them.)
3. Decode geometry/bit-depth/frame boundaries from the bytes we read.

Caution: film is motor-fed whole rolls → **load film before --scan**, and
design CANCEL to let the feed finish (don't hard-abort mid-roll). This step is
big and hardware-risky; give it a dedicated pass.

Alternatively: build the proper state machine (poll-until-ready instead of
verbatim poll counts) rather than pure replay — more robust, more work.

## Handy commands

```sh
# build + test (Mac or Linux)
cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure

# analyze a capture (native pcapng, no tshark needed)
python3 tools/analyze_capture.py <cap.pcapng> --bus 1               # inventory + timeline
python3 tools/analyze_capture.py <cap.pcapng> --bus 1 --device N --commands

# on the scanner box
sudo ./build/pakon_probe                 # classify + endpoint map (needs sudo)
sudo ./build/pakon_replay --open         # needs operational f135 on the host
```
