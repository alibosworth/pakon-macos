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
- **Phase 3 (framing + command primitive + open handshake):** IMPLEMENTED,
  **not yet tested on hardware**. `pakon_proto` build/serialize/parse (wire =
  2+count); `pakon_cmd.[ch]` over EP1; `pakon_replay --open` replays the
  captured open sequence and verifies replies. Framing unit-tested vs real bytes.
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

## Firmware load — DONE (our own f235→f135), awaiting hardware test

Implemented: identities reclassified cold=`0F05:F235`/warm=`0F05:F135`;
`analyze_capture.py --extract-firmware f135.pakfw` extracts the captured FX2
control-transfer sequence; `pakon_usb_load_firmware` replays it and waits for
re-enumeration. `.pakfw` is gitignored (Kodak bytes) — regenerate from a capture.

### NEXT: run the hardware test (needs the cold device on the HOST, not the VM)

```sh
# on this Mac (or wherever the scan capture is): generate the firmware script
python3 tools/analyze_capture.py /Volumes/Video/pakon_scan.pcapng \
  --extract-firmware f135.pakfw           # copy f135.pakfw to the scanner box

# on the scanner box: free the device from the VM first!
#   - shut down the Windows VM, or remove the VirtualBox USB filter, then replug
#   - confirm the host sees the bootstrap:  lsusb | grep 0f05   -> 0f05:f235
sudo ./build/pakon_probe --load-firmware f135.pakfw   # f235 -> f135
sudo ./build/pakon_probe                              # should now show warm f135 + endpoints
sudo ./build/pakon_replay --open                      # replay+verify open handshake
```

If `--load-firmware` can't open `0f05:f235`, the VM still owns it. If
`--open` mismatches, paste the trace.

## Other open directions (after the test)

- **Phase 5 image stream:** need an in-VM USBPcap capture of a scan (host usbmon
  can't see image bytes); decode `0x86` format + scan-start commands.
- **Decode more protocol:** mine the 2218 command exchanges for the full
  configure/calibrate/scan-start sequence.

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
