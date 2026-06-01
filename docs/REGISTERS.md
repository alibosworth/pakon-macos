# Pakon F-X35 register map — toward a capture-free backend

Goal: replace verbatim capture-replay with a **driven** scan that *measures and
computes* its calibration each session (so the same negative scans the same way).
This file decodes the device registers the OEM writes during CONFIGURE/CALIBRATE,
so we can synthesize those writes instead of replaying frozen ones.

Source: cross-referencing our own scan captures (`docs/PROTOCOL.md` → CONFIGURE)
with the decompiled OEM `TLA.dll` (`re/out/TLA.c`). See PROVENANCE in
`docs/PROTOCOL.md`. Confidence is marked per row.

## Register-write frame format (CONFIRMED)

The OEM `WriteRegister` helper (`TLA FUN_1000e510`) builds a `type=0x02` frame:

```
02  <count>  <ADDR>  03  <bank>  <reg>  <vLo>  <vHi>
│    │        │       │   │       │      └────┴── 16-bit value, little-endian
│    │        │       │   │       └── register index within the bank
│    │        │       │   └── bank: 0x82 (CCD timing/exposure) or 0x84 (CCD AFE)
│    │        │       └── constant 0x03 (sub-command = "write reg")
│    │        └── subsystem address (see below)
│    └── count (= bytes after count; 6 for a 1-reg 16-bit write)
└── type 0x02 = write
```

`FUN_1000e510(commObj, ctx, bank, reg, value16, flags)` — only banks `0x82`/`0x84`
are accepted. It also reads the value back (`FUN_1000d530(...,7,...)`) and errors
`0x3fc EC_DRV_PacketReadWriteMismatch` if the readback differs — i.e. **register
writes are verified**.

### Address reconciliation (IMPORTANT)

The decompiled TLA builds these frames with `ADDR = 0xF0` (its `AD_CCD1`). TLA
targets a different address scheme (F-235/335-Plus subsystem layout,
`AD_CCD1=0xf0`/`AD_MOTOR=0xf4`/`AD_LAMP=0xf6`), but the **bank/reg/value semantics
are identical** to the F-135 wire.

⚠️ **CORRECTED (2026-05-31, from capture mining):** on the F-135 wire, the
single-register `WriteRegister` frames for **both** bank `0x82` (timing/exposure)
and bank `0x84` (gain/offset) target **`ADDR = 0x24` (PICM)** — NOT `0x20`. Verified
across two scan captures (`resources/pakon_scan.pcapng` dev13, `pakon_fullroll.pcapng`
dev16): bank-82/84 `02 06 24 03 …` writes number 30+17 and 18+15 respectively, with
**zero** at `0x20`. The motor/advance write (`02 05 24 02 …`) is also at `0x24`. So
**PICM (`0x24`) is the CCD-AFE + timing + motor controller** — this is the address
the driven CALIBRATE/CONFIGURE backend must use. (The earlier "`0x20` for CCD writes"
note was mistaken.)

PICL (`0x20`) is a *second* PIC, initialized identically at open (both PICs report
ASCII `"12345"` to `01 0e <addr> 08 …`). PICL carries a **different** command family:
the scan-time block exposure streaming `02 0f 20 0c 82 <6×16-bit>` (the per-frame
"housekeeping" exposure writes) and `02 07 20 04 8b/8c/8d/8f <2×16-bit>` coordinate/
timing pairs (LED/CCD-line geometry — exact meaning TBD). PICL likely owns
illumination/timing; PICM owns the CCD registers we calibrate.

## Lamp / LED subsystem (TLA addr `0xf6`) — CONFIRMED in TLA, F-135 wire addr TBD

The lamp is a **separate subsystem**, not banks 0x82/0x84. The OEM lamp setter
`FUN_10033c70` (strings `iFilmColor | uiLampLevel`) writes two registers at TLA
address `0xf6`:

| addr.reg | parameter | encoding | confidence |
|----------|-----------|----------|-----------|
| 0xf6.0x80 | LampLevel (PWM/DAC) | 16-bit, written via a `type=0x02` write (`FUN_1000dda0`) | CONFIRMED (TLA) |
| 0xf6.0x81 | Lamp on/off | write `0x00` via `type=0x04` (`FUN_1000dab0`) = off | CONFIRMED (TLA) |

LampLevel clamps (in `FUN_10033c70`): values `< 0x2CEC` (11500) → treated as off;
`> 0x3A33` (14899) → clamped to `0x3A34` (14900); config ceiling at
`DAT_10080f94+0x4a4` (color-neg/B&W) or `+0x4a8` (color-pos). The working window is
≈ 11500–14900 (~43.5–56% of a 16-bit range). `LampLevel` lives in the CiScanner
param struct at `+0x50`.

⚠️ **F-135 lamp is NOT this register.** Mining both scan captures found **no**
16-bit value in the 11500–14900 range written anywhere, and no separate lamp
address (only `0x10/0x20/0x24/0x44/0x46` appear). So the F-235/335 analog
`LampLevel` DAC (`0xf6.0x80`) does **not** apply to the F-135 as-is. The F-135
illumination is most likely an LED that is on/off or PWM-timed via the **PICL
(`0x20`) `02 07 20 04 8b/8c/8d/8f`** coordinate/timing writes (those carry
LED-ish two-value pairs), or set once at firmware init. **Pinning the F-135 lamp
control is the one genuine remaining unknown** — best chased by decompiling the
F-135-specific illumination path (not in TLA's F-235/335 `FUN_10033c70`), or a
capture where lamp brightness is deliberately changed. For milestone 3, try
driving CALIBRATE with the lamp left as the device defaults it.

## Filter-wheel / gate — N/A on F-135 (TLA addr `0xf4`)

TLA's `FUN_10033250` moves a filter wheel by writing position codes `0xe3`/`0xe4`
(visible) / `0xe5` (opaque/dark) to address `0xf4`. **The F-135 has no filter
wheel** — those gate codes are never written on the wire (across both captures
`0xe5` appears only as an incidental *data* byte inside a block exposure write,
never as a gate command). The F-135 is a roll-film scanner with a trilinear RGB
CCD and fixed optics. **Consequence for CALIBRATE:** the dark-offset phase cannot
use an opaque gate as TLA does — on the F-135 it must measure dark either with the
lamp off, or by reading the **dark leader region** of the scan (the captured scan
shows a dark leader at rows 0–513 before the blank pre-load light region; see
STATUS.md "Anatomy of scan.raw"). Verify on hardware.

## Bank 0x84 — CCD analog front-end (gain/offset) — CONFIRMED

Callers `FUN_1002f9c0` (gain) and `FUN_1002fad0` (offset) read these straight from
the `CiScanner` param struct (offsets verified: Gain_R@+0x38, Offset_R@+0x44).

| bank.reg | parameter | encoding | confidence |
|----------|-----------|----------|-----------|
| 0x84.2 | Gain_R | 6-bit, 0–63 (clamp 0x3f) | CONFIRMED |
| 0x84.3 | Gain_G | 6-bit | CONFIRMED |
| 0x84.4 | Gain_B | 6-bit | CONFIRMED |
| 0x84.5 | Offset_R | sign-magnitude: abs ≤255, bit 0x100 = negative | CONFIRMED |
| 0x84.6 | Offset_G | sign-magnitude | CONFIRMED |
| 0x84.7 | Offset_B | sign-magnitude | CONFIRMED |
| 0x84.0 | (AFE config?) | — | TBD |
| 0x84.1 | (AFE config?) | — | TBD |

## Bank 0x82 — CCD timing/exposure/control — mostly CONFIRMED

Exposure setter `FUN_10032680` writes regs 1/2/3 (+7) from CcdExposure params;
integration/height setter `FUN_10032d20` writes reg 6.

| bank.reg | parameter | encoding | confidence |
|----------|-----------|----------|-----------|
| 0x82.0 | control bitmask (enable/strobe bits; set/clear via `FUN_1002f880`) | 10-bit | CONFIRMED |
| 0x82.1 | CcdExposure_R | 12-bit, ≤0xfff | CONFIRMED |
| 0x82.2 | CcdExposure_G | 12-bit | CONFIRMED |
| 0x82.3 | CcdExposure_B | 12-bit | CONFIRMED |
| 0x82.6 | Height / integration lines | ≤0xff0 | CONFIRMED |
| 0x82.7 | CcdExposure_Ir | 12-bit | likely |
| 0x82.8 | timing constant `0x860` (one-time init) | const | CONFIRMED |
| 0x82.9 | timing constant `0x860` (one-time init) | const | CONFIRMED |
| 0x82.4 | (timing) | — | TBD |
| 0x82.5 | (timing) | — | TBD |
| 0x82.10 (0xa) | (timing, from integration fn) | — | TBD |
| 0x82.0xb | written 0 by integration fn | — | TBD |

Control-bit helpers: `FUN_1002f920` toggles bit `0x1`, `FUN_1002f970` toggles bit
`0x100`, exposure setter sets bit `0x8` (reg-0 mask) after programming exposure.

## CiScanner parameter struct (offsets, from `FUN_10010e50` loads)

| offset | param | | offset | param |
|--------|-------|-|--------|-------|
| +0x38 | Gain_R | | +0x54 | CcdExposure_R |
| +0x3c | Gain_G | | +0x58 | CcdExposure_G |
| +0x40 | Gain_B | | +0x5c | CcdExposure_B |
| +0x44 | Offset_R | | +0x68 | CcdExposureOpenGate_R |
| +0x48 | Offset_G | | +0x6c | CcdExposureOpenGate_G |
| +0x4c | Offset_B | | +0x70 | CcdExposureOpenGate_B |
| +0x50 | LampLevel | | | (Ir/SpliceDarkness/DetectWhite also present) |

Note the **OpenGate** exposure variants: the OEM uses different exposures for the
open-gate (calibration) pass vs. the film pass.

## The CALIBRATE feedback algorithm (milestone 2) — reversed from TLA.c

This is the make-or-break piece: how the OEM reads CCD output with **no film in
the gate** and drives Offset → Gain → Exposure to fixed targets each session, so
the same negative scans the same way regardless of lamp/CCD drift. Reversed from
`re/out/TLA.c`; line numbers below are into that decompile. Cross-corroborated by
three independent passes; the load-bearing constants were re-read from source.

### Orchestration

- The session arms a request and `SetEvent`s `m_hEventScanCalibrate` (handle at
  scanner-object `+0x278`); signalers `FUN_10042eb0`/`FUN_1004cad0`/`FUN_1004d000`
  (lines ~46181 / 48059 / 48241). `FUN_1004cad0` carries the per-session geometry
  config (iResolution, iHeight, iOffset, iMotorSpeed, iStepperCCD) and branches on
  `ScannerVersionHw`.
- The scan thread `FUN_1003fd10` calls the **calibrate orchestrator**
  `FUN_10033dd0 @ 0x10033dd0`, which calls the **coordinator** `FUN_10026990 @
  0x10026990`. The coordinator dispatches sub-steps by enum (logged via the
  name table at lines 19960–21000) gated on a flag bitmask (`this+0x14c`):
  open-gate warm-up (0x40) → CalibrateChange (0x20) → **dark offset** →
  FindLiteAndDark (0x4|0x8) → **per-film-type gain/exposure** (0x1–0x3) →
  smear/fixed-pattern correction (separate threads). Cached calibration is reused
  when the recalibrate flag is clear.

### The core feedback loop: `FUN_10022f80 @ 0x10022f80` (lines 24739–25373)

One function runs three sequential phases. The measurement primitive is
`FUN_10021bd0(this, p, nCh, 0, cols, divisor, doAvg, buf)` which accumulates
`divisor` scan lines per channel into `buf` (the CCD ring buffer drained by
`FUN_100217d0`); then per-channel column reducers `FUN_10021b00` (mean over a
column window) and `FUN_100214d0` (peak over the active window) read it out.
Convergence predicate `FUN_100215a0(meas, target, tolLo, tolHi)` =
`target - tolLo ≤ meas ≤ target + tolHi` (verified, line 23736).

**Phase 1 — Dark offset** (lines 24862–24921). Gate to opaque (`FUN_10033250`
→ pos `0xe5`), gain = 0 (`FUN_1002f9c0(…,0,0,0)`). Iterate per channel:

```
offset[R,G,B] = 100                      # initial guess
for iter in 0..7 (max 8):                # break when all 3 converged
    write Offset registers (bank 0x84.5/6/7)   via FUN_1002fad0
    acquire 3ch × 32 lines               via FUN_10021bd0(nCh=3, divisor=0x20)
    m = mean(channel)                    via FUN_10021b00
    converged if 268 ≤ m ≤ 332           # FUN_100215a0(m, 300, 0x20, 0x20)
    offset += (300 - m) * 0x1400 / 0x30000   # proportional; step ≈ (300-m)/38.4
store offsets → calib struct +0x44/+0x48/+0x4c
```

Target = **300 ADU** dark pedestal (NOT zero). Proportional control, 8 iters max.

**Phase 2 — Gain** (lines 24927–24988). Move to a visible gate, set exposure to a
nominal 100 lines. Iterate per channel toward a near-saturation white:

```
gain[R,G,B] = 0;  factor[R,G,B] = 1.0
for iter in 0..3 (max 4):                # break when all 3 converged
    converged if 64000 ≤ peak ≤ 66048    # FUN_100215a0(peak, 64000, 0, 0x800)
    write Gain registers (bank 0x84.2/3/4)     via FUN_1002f9c0
    write integration/exposure                 via FUN_10032d20 (exp=100, 1 line)
    acquire 4ch × 32 lines (averaged)    via FUN_10021bd0(nCh=4, divisor=0x20, avg=1)
    peak = peak(channel)                 via FUN_100214d0
    gain = round( factor * TARGET / peak )     # TARGET = _DAT_1006f268 ≈ 64000.0
    gain = 0 if gain wrapped negative          # >0x80000000 guard
    factor = 1.0 / (1.0 - gain * k)            # FUN_100227e0: gain linearization,
                                               # gain clamped 0..0x3f, k ≈ const
```

Target = **64000 ADU** peak (~98% of 16-bit full scale), asymmetric tolerance
`[64000, 66048]` (0x800=2048 upper slack, no lower). Multiplicative (ratio) control, 4 iters max.
The `factor = 1/(1 - gain·k)` term linearizes the 6-bit gain register so the next
ratio estimate accounts for the gain already applied.

**Phase 3 — Fine exposure by linear regression** (lines ~24999–25347). A 6-point
exposure sweep (`+0,+10,…,+50` from the calibrated exposure) collects per-channel
peaks, fits an OLS line (peak vs. exposure) per channel, then sets
`exposure = (1/slope) · measured · gainCorrection`, clamped to a minimum of
`0xd` (13) and the 12-bit max `0xfff`. The whole `FUN_10022f80` outer body runs up
to **2 passes** (`FUN_10026990` retries when the lamp-level adjust fires). Result
exposures → calib struct `+0x54/+0x58/+0x5c`.

**Lamp trim** (lines ~25019–25032). If the converged window is too narrow
(coverage `< 0x6e`), `LampLevel` (`*param8`, struct `+0x50`) is reduced by
`deficit·50`, clamped to `≥ (max − 500)`, written via `FUN_10033c70` (addr 0xf6
reg 0x80), then `Sleep(2000)` and the outer pass retries.

### Targets & constants (verified against source)

| stage | register(s) | target | tolerance | control | max iters |
|-------|-------------|--------|-----------|---------|-----------|
| dark offset | 0x84.5/6/7 | 300 ADU mean | ±32 | proportional `(300-m)/38.4` | 8 |
| gain | 0x84.2/3/4 | 64000 ADU peak | `[64000,66048]` | ratio `factor·64000/peak` | 4 |
| exposure | 0x82.1/2/3 | (regression fit) | — | OLS slope, 6-pt sweep | 2 outer |
| lamp trim | 0xf6.0x80 | coverage ≥ 0x6e | — | `level -= deficit·50` | retry ×2 |

Order is **offset (dark) → gain (open white) → exposure (regression) → lamp trim**.
Stop/abort flag `*piVar18` (checked at 24892/24898/24950/…) bails the loops early.

Open constants (in `.data`, bit patterns not recoverable from the C dump alone):
`_DAT_1006f268` ≈ 64000.0 (gain-ratio numerator; matches the convergence target);
`_DAT_100665a8` = 1.0; the gain-linearization `k` in `FUN_100227e0` ≈ 1/63 (6-bit
gain). Treat as ~64000 and verify on hardware.

### What this means for the driven C backend

The driven `CALIBRATE` state (replacing frozen replay), adapted to the F-135 wire
(all CCD writes → **addr `0x24`**, frame `02 06 24 03 <bank> <reg> <vLo> <vHi>`):

1. **Dark offset.** F-135 has no opaque gate — measure dark with the lamp off OR
   from the dark-leader rows. Gain 0; loop ≤8: write offset (`0x24` bank 0x84
   reg 5/6/7), grab 32 lines, mean→300±32, step `(300−mean)/38.4`.
2. **Gain.** Illuminated, exposure nominal; loop ≤4: write gain (`0x24` bank 0x84
   reg 2/3/4), grab 32 lines averaged, peak→64000, `gain = round(factor·64000/peak)`,
   update `factor = 1/(1−gain·k)`.
3. **Exposure.** 6-point sweep + per-channel OLS → write exposure (`0x24` bank 0x82
   reg 1/2/3), clamp `[0xd, 0xfff]`.
4. Lamp trim is F-235/335 only (no F-135 LampLevel DAC found) — skip until the
   F-135 illumination control is pinned.

We already own the CCD line read (Phase 5 `0x86` stream), the register-write frame,
and (now) the confirmed target address `0x24`, so steps 1–3 are implementable.

**Sanity-check seeds — the OEM's own converged values** (from `pakon_scan.pcapng`,
the numbers a correct driven CALIBRATE should land near):

| register | wire frame | value |
|----------|-----------|-------|
| Gain_R (0x84.2) | `02 06 24 03 84 02 0d 00` | 0x0d = 13 |
| Gain_G/B (0x84.3/4) | `… 84 03 0d 00` / `… 84 04 0d 00` | 13 / 13 |
| Offset_R (0x84.5) | `02 06 24 03 84 05 33 01` | 0x0133 → −51 (sign-mag) |
| Offset_G (0x84.6) | `… 84 06 2a 01` | 0x012a → −42 |
| Offset_B (0x84.7) | `… 84 07 2b 01` | 0x012b → −43 |
| Height (0x82.6) | `02 06 24 03 82 06 1a 0c` | 0x0c1a = 3098 lines |

(Earlier in the session the OEM writes a flat `0x0a` to gains and `0x000a` to
offsets — the pre-calibration reset — then converges to the values above. Seeing
our driven loop arrive near gain≈13 / offset≈−45 is the on-hardware success signal.)

**Full converged CONFIGURE register set** (OEM FINAL values = last write to each
register in the capture; shipped as `pakon_calib_default_config()`, written by
`pakon_calib_configure()` to addr `0x24`, hardware-accepted via `--configure`):

| bank.reg | field | value | notes |
|----------|-------|-------|-------|
| 0x82.0 | control bitmask | `0x0160` | |
| 0x82.1/2/3 | CcdExposure R/G/B | `0x0000` | **0 — integration is NOT here** |
| 0x82.4 | timing | `0x002b` | |
| 0x82.5 | timing | `0x07fb` | integration-related |
| 0x82.6 | Height | `0x0c1a` | 3098 |
| 0x82.9 | timing | `0x001f` | |
| 0x82.0a | timing | `0x0400` | |
| 0x84.0 | AFE cfg | `0x0078` | |
| 0x84.1 | AFE cfg | `0x0080` | |
| 0x84.2/3/4 | Gain R/G/B | `0x000d` (13) | |
| 0x84.5/6/7 | Offset R/G/B | `0x0126`/`0x011f`/`0x011f` | sign-mag = **−38/−31/−31** |

⚠️ **Integration is governed by timing regs 0x82.4/5/9/0xa, NOT CcdExposure
(0x82.1/2/3 = 0)** — a hardware sweep writing 0x82.1/2/3 had zero effect on the
open-gate level. The synthesized-CONFIGURE path writes this validated set from C and
uses the EEPROM table's checksum (`PAKON_CALIB_EEPROM_CKSUM_R1/R2`) to detect drift,
reaching a reproducible scan without a live open-gate gain measurement.

**Open items before hardware iteration:** the F-135 illumination/lamp control
(see Lamp section — the one real unknown) and the exact `.data` target constants
(`_DAT_1006f268`≈64000.0, gain `k`); both confirmable on hardware by reading back.

## Still TBD (next steps toward the driven backend)

1. **F-135 illumination/lamp control** — the one real unknown. The F-235/335
   `LampLevel` DAC (`0xf6.0x80`) does not exist on the F-135 wire. Chase via the
   F-135-specific decompile path or a brightness-changing capture; or proceed with
   device-default illumination for milestone 3 (see Lamp section).
2. **Confirm the open `.data` constants** (`_DAT_1006f268`≈64000 target, gain `k`)
   on hardware — calibrate to known targets and read back.
3. **Remaining bank 0x82 timing regs** (4,5,10,0xb) and bank 0x84 reg 0/1. The
   capture shows their OEM values (e.g. `82.4`=0x06/0x2b/0x3e, `82.5`=0x080e/0x07fb,
   `82.0a`=0x0400, `84.0`=0x78, `84.1`=0x80) — decode meanings if needed.
4. **PICL (`0x20`) command family** — block exposure streaming `02 0f 20 0c 82 …`
   and `02 07 20 04 8b/8c/8d/8f …` geometry/LED pairs; relevant if illumination
   turns out to live here.
5. Done: CALIBRATE register writes confirmed at **`0x24`** across two captures;
   no filter wheel on F-135. Still verify any remaining "CONFIRMED" register
   against a capture before relying on it.
