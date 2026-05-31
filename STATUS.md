# Project status — feed this to Claude at the start of next session

Working spec is `PAKON_SANE_PLAN.md`; living protocol notes in `docs/PROTOCOL.md`;
the project skill `.claude/skills/pakon-scanner/SKILL.md` has the operational
guide. This file is the short "where we left off" snapshot.

_Last updated: 2026-05-31 (Mac test added)._

**Phase 5 WORKS on hardware:** `pakon_replay --scan` drove a full scan from our
code and pulled **239,984,640 image bytes** (4-frame COLOR strip, 11719 reads,
2 late errors). Sample under analysis: `/Volumes/Video/scan.raw`.

**Image layout — CONFIRMED interleaved (not planar):**
- **16-bit LE**, line stride **8000 samples / 16000 bytes** (autocorr peak +
  2x harmonic), ~**14999 lines**.
- **Per-pixel interleaved RGB** (`R,G,B,R,G,B…`), NOT plane-sequential. Proven
  on `scan.raw`: column autocorrelation peaks at lag 3/6/9 (interleave
  fingerprint, lag3 ≈ 1.58× lag1); deinterleave-then-correlate gives high,
  balanced channel correlation (R~G .59 / R~B .90 / G~B .56) while a planar
  split collapses (one "channel" anti-correlates, −0.08). This **supersedes the
  earlier planar hypothesis** in old commits / `raw2pnm.py --planar`.
- 8000 isn't a multiple of 3, so the working model is **2 padding samples per
  line and RGB restarts at R each line → 2666 px wide** (`(8000//3)` triples).
  ⚠️ see OPEN Q1 — this per-line phase reset is the suspected cause of ghosting.
- **Interleave order B,R,G** (position 0 = Blue, 1 = Red, 2 = Green), NOT RGB.
  Green is the middle trilinear line; red is the channel that passes most
  through the orange mask (rebate transmission), blue the most absorbed
  (darkest). Confirmed by natural skin tones across all 6 permutations of a real
  frame (the wrong orders give green or "lomography purple" skin).
- Ribbon comes out rotated 90°; 4 frames stacked along the long (line) axis.

**Anatomy of `scan.raw`** (rows, from `autocrop`): dark leader 0–513; **blank
pre-load scan** ~514–1950 (light through no film — captured before the strip
was loaded, bright + colour-neutral); **film / 4 frames 1951–14825**; blank
tail; uniform gate margin at cols ~2050–2666; orange-base sliver at col 0.

**`tools/pakon_image.py` (committed `ea51cc0`)** decodes interleaved RGB →
16-bit RGB TIFF(s). `--autocrop` (default) trims leader/blank-scan/tail/margin
(dark rows; bright+neutral rows; low-detail columns) → on this scan rows
1925–14868, cols 1–1999. `--frames N` splits along the ribbon **before** rotate;
`--rotate {90,180,270}` per frame; `--invert` is a LINEAR preview only.
Run: `pakon_image.py scan.raw --rotate 90 --frames 4` → four 3236×1999 negs.

**Q1 — RGB ghosting — SOLVED (2026-05-31).** NOT a phase/interleave problem:
the per-line "restart at R" model is correct (period-3 power in per-row channel
means = 0; every row's best phase = 0). The real cause is a **trilinear CCD** —
R/G/B sensor lines are spaced along the scan direction, so the channels are
offset in scan lines. Measured by inter-channel vertical cross-correlation:
sensor order along the scan is **B (0), G (+8), R (+16) lines** (positions
p0/p2/p1; 8-line spacing). Fix: co-register the three lines to a common position
→ edge fringing gone, residual offset 0. Implemented as `pakon_image.py
--register` (default on, auto-measures the leads; `--reg-leads G,B` to force,
`--no-register` to disable).

**Q2 — magenta / "black rebate" — ANSWERED (2026-05-31): stream is NOT
pre-inverted.** Photometric refs prove transmission-sense (more light → higher
value): dark leader ≈ 520, **no-film open gate ≈ 48900 (max)**, orange base
edge R≈11800/G≈5600/B≈4800 (the C-41 mask), film midtones R15223/G18723/B16000.
So it's a true **raw digital negative**; the black rebate the human saw is the
*correct* result of inverting a negative (clear/unexposed border = high
transmission → black), not double-inversion. The **magenta** is purely the
orange mask: naive `max-raw` leaves green lowest → magenta. A mask-aware invert
(per-channel density relative to the film base) fixes it but is **sensitive to
the base estimate** — sampling the bright scene instead of the true rebate
over-corrects to green. TODO (optional): proper C-41 inversion in-tool
(sample the real rebate / per-channel base, density space, gray-balance), OR
keep emitting the registered+cropped raw negative for dedicated film software
(Negative Lab Pro, darktable negadoctor, Grain2Pixel).

TODO after Q1/Q2: frame-boundary auto-detection (gaps); pixels may be non-square
(across-sensor oversampled vs motor step) — Pakon's own output is 3000×2000/
frame, so resampling may be wanted. Then Phase 6 (SANE backend).

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
- **Phase 5 (scan state machine + image):** DONE and **VALIDATED ON HARDWARE**
  (Linux + macOS). `pakon_replay --scan` drives a real scan; `pakon_image.py`
  decodes the output to 16-bit RGB TIFFs with registration and autocrop.
- **Phase 6 (Swift macOS app):** not started. **Current priority.**
- **Phase 7 (SANE backend / Linux), 8 (hardening):** not started.

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

## Phase 5 — scan replay DONE, validated on Linux and macOS

Scan flow: OPEN → PARAM READ (0xA4/0xA9) → CONFIGURE (PICL/PICM register
writes + polls) → SCAN (interleaved status polls + `0x86` 20480-byte image
reads; ~240 MB / 4 frames). Confirmed on both Linux and macOS: 239,984,640
bytes, 11719 image reads, 2 late transfer errors (normal, ignored).

- `analyze_capture.py --extract-scan OUT.pakscan` — emits the ordered op list:
  `O <hex>` (EP1 cmd+reply), `M <n>` (image read), `C ...` (0xA4/0xA9 control).
  Verified 2218 O / 11719 M / 32 C.
- `pakon_usb_control()` — generic EP0 control transfer (for 0xA4/0xA9).
- `pakon_replay --scan FILE [--image OUT]` — replays each op, writing `0x86`
  payloads to a raw image file. Image-read timeout 5 s.

To run a scan: load film, ensure device is operational `f135`, then:

```sh
sudo ./build/pakon_replay --scan scan.pakscan --image scan.raw  # Linux
./build/pakon_replay --scan scan.pakscan --image scan.raw       # macOS
```

## Handy commands

```sh
# build + test (Mac or Linux)
cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure

# analyze a capture (native pcapng, no tshark needed)
python3 tools/analyze_capture.py <cap.pcapng> --bus 1               # inventory + timeline
python3 tools/analyze_capture.py <cap.pcapng> --bus 1 --device N --commands

# on the scanner box (Linux needs sudo; macOS does not)
sudo ./build/pakon_probe                         # classify + endpoint map
sudo ./build/pakon_probe --load-firmware f135.pakfw   # cold f235 → warm f135
sudo ./build/pakon_replay --open                 # open handshake to Idle
sudo ./build/pakon_replay --scan scan.pakscan --image scan.raw  # full scan

# decode
python3 tools/pakon_image.py scan.raw --rotate 90 --frames 4
```
