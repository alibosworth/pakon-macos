# Pakon F-X35 protocol — living document

This file records what we actually know/decode about the wire protocol, updated
phase by phase. Anything not confirmed from documentation or our own captures is
marked **TBD** or **inferred** — do not treat inferred items as ground truth.

## Identities

- **Warm (post-firmware):** `0F05:Fx35`, class `0xff` (vendor-specific), 3
  endpoints. The PID (`F135`/`F235`/`F335`) is set by the firmware the FX2
  booted and is **not** a reliable model indicator: a physical **F-135** unit
  was observed enumerating as `0F05:F235`. Treat any `0F05:Fx35` as warm;
  identify the actual model via the protocol layer (not the USB PID).
- **Note on boot state:** at least one unit comes up *already warm* on
  power-on (no host firmware download performed). That means either the board
  auto-loads firmware from an onboard EEPROM, or a udev/fxload rule on the host
  loaded it on plug. If firmware auto-loads, the host-side FX2 download
  (Phase 1 task 2) is a fallback, not on the critical path for that unit.
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

## Endpoints — observed (warm `0F05:F235`, an F-135 unit)

Interface 0 with **4 alternate settings** (alt 0 is the empty FX2 default).
Physical endpoints are the standard Cypress FX2 set: EP1, EP2, EP4, EP6, EP8.
Full map (from `pakon_probe`):

| Alt | Endpoints |
|----:|-----------|
| 0   | (none — default empty alt setting) |
| 1   | 0x01 OUT bulk/512, 0x81 IN bulk/512, 0x02 OUT bulk/512, 0x04 OUT bulk/512, 0x86 IN bulk/512, 0x88 IN bulk/512 |
| 2   | 0x01 OUT int/64, 0x81 IN int/64, 0x02 OUT int/512, 0x04 OUT bulk/512, 0x86 IN int/512, 0x88 IN bulk/512 |
| 3   | 0x01 OUT int/64, 0x81 IN int/64, 0x02 OUT iso/512, 0x04 OUT bulk/512 |

Note: this contradicts the older "3 endpoints" note — that was approximate.

### Working hypothesis (NOT yet confirmed — confirm in Phase 2/3)

- **EP1 `0x01`/`0x81`** = command/status channel. It is 64-byte in alts 2/3,
  comfortably holding the documented 36-byte frame, and matches the Windows
  36-byte IOCTL packet exchange.
- **EP2/EP4 OUT** = host→device bulk (commands / scan setup / bulk out).
- **EP6/EP8 IN** = device→host, i.e. the **image stream** (high-volume bulk).
- **Which alt setting the driver selects is unknown.** alt 1 (all bulk) is the
  simplest candidate; alts 2/3 add interrupt/iso variants.

### Empirical result (`pakon_probe --probe-open`, warm F-235)

Sending the documented 36-byte open packet to **every OUT endpoint in every alt
setting (1–3) NAKs** — `libusb_bulk/interrupt_transfer` times out with 0 bytes
moved, every time. This is device-side (run as root; claim + set-alt both
succeed; a permission fault would be ACCESS, not a timeout). Conclusions:

- The bulk/interrupt OUT FIFOs are **not armed** by raw writes — the device
  needs an initialization step first.
- The 36-byte command protocol is therefore **not raw bulk**. Most likely it is
  carried over **EP0 vendor control transfers** (consistent with the original
  Windows driver using an IOCTL to exchange 36-byte structs), and/or a control
  "start"/arm precedes any bulk image traffic.
- The required request codes / init sequence are **undocumented and must not be
  guessed** (project rule). **→ Discover them from a real capture (Phase 4).**

## Scan path — **UNKNOWN (Phase 4-5)**

Calibration, frame detection, and the bulk image-stream format are undocumented
and must be discovered from our own Windows captures (Phase 4) and modeled as a
state machine (Phase 5): `OPEN → CONFIGURE → CALIBRATE → SCAN_FRAMES →
READ_IMAGE → DONE`, with `CANCEL` transitions.
