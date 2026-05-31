# Pakon F-X35 protocol — living document

This file records what we actually know/decode about the wire protocol, updated
phase by phase. Anything not confirmed from documentation or our own captures is
marked **TBD** or **inferred** — do not treat inferred items as ground truth.

## Identities

**Two firmware stages (observed on the F-135 unit):**

1. **Bootstrap / "cold": `0F05:F235`**, class `0xff`, **no string descriptors**.
   Auto-loaded from the onboard EEPROM on power-up. Its job is to receive a
   second-stage firmware download. It does **not** implement the application
   protocol — every bulk-OUT NAKs (see Phase 2 result below).
2. **Operational / "warm": `0F05:F135` "Pakon F135-USB Film Scanner"** (has a
   product string), reached after the working (Windows) driver downloads
   stage-2 firmware. This matches the physical model and the documented PID.

So the correct mapping is **cold = `0F05:F235`, warm = `0F05:F135`**, and the
`F235 → F135` firmware download IS needed for standalone operation (it is not
optional — the EEPROM only gets us to the bootstrap). The mechanism of that
download (standard FX2 `0xA0` to RAM vs something else) is to be confirmed from
the Phase 4 capture. PID still must not be used as a model indicator.
- **Cold (FX2 bootloader):** **TBD** — confirm via `lsusb` (Linux) or
  `pakon_probe --list` / `system_profiler SPUSBDataType` (macOS) on a freshly
  powered scanner before any driver loads (Phase 1, STOP POINT A). Not guessed:
  `PAKON_COLD_VID/PID` in `include/pakon_usb.h` are 0 until confirmed, and the
  firmware-download path hard-refuses to run while they are 0.

## Firmware download (f235 → f135) — decoded from capture

Confirmed from a real capture (`tools/analyze_capture.py`) of the working
driver loading the bootstrap `0F05:F235` device. Standard two-stage EZ-USB
FX2 fxload, all over EP0 vendor control transfers:

- `0xA0 wValue=0xE600 data=01` — write CPUCS, hold the 8051 in reset.
- `0xA0 wValue=0x7F92 data=01` — a firmware-specific byte poked before reset.
- **391× `0xA0`** writes — to internal 8051 RAM.
- **711× `0xA3`** writes — to external RAM (the second-stage loader path used
  for the bulk of the firmware; `0xA3` is implemented by the first-stage code).
- `0xA4 wValue=0x00A1 wLength=0` — finalize / renumerate trigger; the device
  then re-enumerates as operational `0F05:F135`.
- `0xA9 wLength=8` (IN) — an 8-byte readback right after (status/version?).

Implication: standalone firmware load is feasible with the FX35Package `.hex`
blobs + this sequence (extends our current `0xA0`-only stub to add `0xA3` and
the `0xA4` finalize). Still TODO: the actual `.hex` provenance/mapping.

## Command frame (documented)

36-byte fixed frame:

| Offset | Field | Notes |
|-------:|-------|-------|
| 0      | `type`  | packet type byte (see below) |
| 1      | `count` | length of `data`, max 34 |
| 2..35  | `data`  | 34 bytes; `data[0]` is an address byte |

### Address byte (`data[0]`) — documented

| Name                | Value |
|---------------------|------:|
| AD_HOST             | 0x10 |
| AD_PICL             | 0x20 |
| AD_BOOT_PICL        | 0x22 |
| AD_PICM             | 0x24 |
| AD_BOOT_PICM        | 0x26 |
| AD_PICL_PLUS        | 0x40 |
| AD_BOOT_PICL_PLUS   | 0x42 |
| AD_PICM_PLUS        | 0x44 |
| AD_BOOT_PICM_PLUS   | 0x46 |

### Status byte (scanner→host) — documented

`0` success, `1` not acked, `2` invalid packet, `3` bad checksum, `4`–`6`
USB-related, `7` host algorithm error, `8` success, `9` bus error.

### Packet `type` byte — **inferred / TBD**

The names `PH_CMD`, `PH_READ_STATUS`, `PH_INVALID` are documented but their
numeric values are **not** confirmed. Observed on the wire (open handshake):
host command frames begin `0x04`, the reply begins `0x07`. The name→value
mapping is to be nailed down in Phase 3 from real traffic.

### Checksum — **inferred / TBD (Phase 3)**

Algorithm not yet derived. Will be reverse-derived and hard-validated against
known-good sample packets, e.g. the open packet `04 03 10 00 85`, before it is
trusted for scan commands.

## Endpoints — CONFIRMED from scan capture (operational `0F05:F135`)

The operational `f135` device exposes interface 0, **single setting, 3 bulk
endpoints** (this is the "3 endpoints" the original note referred to):

| Endpoint | Dir | Role (confirmed) |
|----------|-----|------------------|
| `0x01`   | OUT | **command** channel (host → device) |
| `0x81`   | IN  | **command reply / status** (device → host) |
| `0x86`   | IN  | **image stream** (bulk image data) |

From the 4-frame scan capture (device 13): `BULK OUT 0x01` and `BULK IN 0x81`
= **2218 exchanges** each (one reply per command); `BULK IN 0x86` = **11719**
transfers (the image). So a command is: write a frame on `0x01`, read the reply
on `0x81`; image bytes stream from `0x86`.

> The earlier 6-endpoint / 4-alt-setting map was the **`f235` bootstrap**
> descriptor (and is why `--probe-open` NAK'd — the bootstrap implements no app
> protocol). The operational `f135` is the simple 3-endpoint device above.

## Command frame — CONFIRMED wire format

`[type][count][count data bytes]` — the on-wire length is **`2 + count`**, NOT
padded to 36 (the 36 is only the max in-memory struct). `data[0]` is the
address byte. Examples from the capture:

```
04 03 10 00 85       type=04 count=3 data=[10(AD_HOST) 00 85]
07 02 10 00          type=07 count=2 data=[10 00]  (reply; data[1]=status)
04 03 44 00 00       query AD_PICM_PLUS  -> 07 02 44 01  (present)
04 03 24 00 00       query AD_PICM       -> 07 02 24 00
```

Note the `0x85` in the open packet is a **command/parameter byte, not a
checksum** (the analogous `04 03 44 00 00` ends in `00`). Checksum (if any) is
TBD from the full 2218-command sample set in the capture.

## Open handshake — CONFIRMED (replay verbatim in Phase 3)

Observed on EP1, exactly matching the documentation:

```
host(0x01) -> 04 03 10 00 85       dev(0x81) <- 07 02 10 00
host(0x01) -> 02 04 10 01 8f 00    dev(0x81) <- 07 02 10 00
host(0x01) -> 04 03 44 00 00       dev(0x81) <- 07 02 44 01   ; probe PICs...
... (probes AD_PICM_PLUS 0x44, AD_BOOT_PICM_PLUS 0x46, AD_PICM 0x24, etc.)
```

## Parameter/calibration read — control `0xA4`/`0xA9` (Phase 5)

After the EP1 probe, the driver reads a structured block via EP0 vendor control:
`0xA4` (OUT trigger, `wValue=0x00A5`, `wIndex=0x1234`, no data) paired with
`0xA9` (IN read) pulling **32-byte chunks at increasing offsets** (0x00, 0x08,
0x28, 0x48, …). Looks like a calibration/parameter table. 16 such pairs in the
capture. To be decoded in Phase 5.

## Scan path — partially mapped (Phase 5)

Open + PIC-probe + parameter read are now known (above). Still to decode from
the capture: the configure/scan-start commands on EP1, and the **image-stream
format** on `0x86` (geometry, bit depth, frame boundaries). Model as a state
machine: `OPEN → CONFIGURE → CALIBRATE → SCAN_FRAMES → READ_IMAGE → DONE`.
