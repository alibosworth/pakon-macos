# Pakon F-X35 protocol — living document

This file records what we actually know/decode about the wire protocol, updated
phase by phase. Anything not confirmed from documentation or our own captures is
marked **TBD** or **inferred** — do not treat inferred items as ground truth.

## Identities

- **Warm (post-firmware):** `0F05:Fxxx` where the PID encodes the model —
  `F135` (F-135), `F235` (F-235), `F335` (F-335). Class `0xff` (vendor-specific).
  **Verified on hardware:** an F-235 enumerates as `0F05:F235`. *(documented +
  observed)*
- **Cold (FX2 bootloader):** **TBD** — confirm via `lsusb` (Linux) or
  `pakon_probe --list` / `system_profiler SPUSBDataType` (macOS) on a freshly
  powered scanner before any driver loads (Phase 1, STOP POINT A). Not guessed:
  `PAKON_COLD_VID/PID` in `include/pakon_usb.h` are 0 until confirmed, and the
  firmware-download path hard-refuses to run while they are 0.

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

## Open handshake (documented sequence, to replay in Phase 3)

```
host -> 04 03 10 00 85        ; open
dev  <- 07 02 10 00           ; expect
host -> 02 04 10 01 8f 00     ; (next exchange)
...                           ; drive to Idle
```

## Endpoints — **TBD (Phase 2)**

The 3 warm endpoints must be classified into command channel vs image/bulk-IN.
Initial guess from descriptor directions; confirmed empirically in Phase 2.

## Scan path — **UNKNOWN (Phase 4-5)**

Calibration, frame detection, and the bulk image-stream format are undocumented
and must be discovered from our own Windows captures (Phase 4) and modeled as a
state machine (Phase 5): `OPEN → CONFIGURE → CALIBRATE → SCAN_FRAMES →
READ_IMAGE → DONE`, with `CANCEL` transitions.
