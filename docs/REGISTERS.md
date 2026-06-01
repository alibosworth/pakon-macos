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

The decompiled TLA builds these frames with `ADDR = 0xF0` (its `AD_CCD1`). Our
real **F-135 wire captures use `ADDR = 0x20`** (PICL) for the same CCD writes
(`02 06 20 03 …`) and `0x24` (PICM) for motor. So TLA targets a different address
scheme (F-235/335-Plus subsystem layout, `AD_CCD1=0xf0`/`AD_MOTOR=0xf4`), but the
**bank/reg/value semantics are identical**. ⇒ Use our captured F-135 address bytes
(`0x20` CCD/PICL, `0x24` motor/PICM), with the bank/reg map below. Confirm each
register against a capture before trusting it on hardware.

TLA subsystem address scheme (full): `0xf0` = CCD, `0xf4` = motor + filter-wheel,
`0xf6` = lamp. Mapping to F-135 wire: `0xf0→0x20` (PICL), `0xf4→0x24` (PICM);
**`0xf6` (lamp) F-135 wire address is not yet captured — TBD** (see Lamp below).

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

⚠️ The **F-135 wire address** for the lamp is unconfirmed — `0xf0/0xf4` map to
`0x20/0x24`, but `0xf6` has no captured F-135 equivalent yet. Grab it from a scan
capture's CONFIGURE phase (look for a non-0x20/0x24 `02`/`04` write carrying a
~11500–14900 value) before driving the lamp from our backend.

## Filter-wheel / gate position (TLA addr `0xf4`) — CONFIRMED in TLA

`FUN_10033250(this, param_1, uiPosition, sync)` moves the filter wheel / gate by
writing a single byte command (`type=0x04`) to address `0xf4`. It maps the film
format to a position code, then waits for the move:

| film format | position code | meaning |
|-------------|---------------|---------|
| 1 (color-neg), 8 (IR) | `0xe3` | visible pass A |
| 2 (color-pos), 4 (B&W) | `0xe4` | visible pass B |
| 0x1000 / 0x2000 | `0xe5` | **opaque / dark** (used for dark-offset measurement) |

Calibration drives the wheel to `0xe5` (opaque) for the dark-offset pass, then to
a visible position for the gain/exposure passes.

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
    converged if 64000 ≤ peak ≤ 64512    # FUN_100215a0(peak, 64000, 0, 0x800)
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
`[64000, 64512]` (no lower slack). Multiplicative (ratio) control, 4 iters max.
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
| gain | 0x84.2/3/4 | 64000 ADU peak | `[64000,64512]` | ratio `factor·64000/peak` | 4 |
| exposure | 0x82.1/2/3 | (regression fit) | — | OLS slope, 6-pt sweep | 2 outer |
| lamp trim | 0xf6.0x80 | coverage ≥ 0x6e | — | `level -= deficit·50` | retry ×2 |

Order is **offset (dark) → gain (open white) → exposure (regression) → lamp trim**.
Stop/abort flag `*piVar18` (checked at 24892/24898/24950/…) bails the loops early.

Open constants (in `.data`, bit patterns not recoverable from the C dump alone):
`_DAT_1006f268` ≈ 64000.0 (gain-ratio numerator; matches the convergence target);
`_DAT_100665a8` = 1.0; the gain-linearization `k` in `FUN_100227e0` ≈ 1/63 (6-bit
gain). Treat as ~64000 and verify on hardware.

### What this means for the driven C backend

The driven `CALIBRATE` state (replacing frozen replay) is:

1. Gate → opaque; gain 0; loop ≤8: write offset (0x84.5/6/7), grab 32 lines,
   mean→300±32, step `(300−mean)/38.4`.
2. Gate → visible; exposure nominal; loop ≤4: write gain (0x84.2/3/4), grab 32
   lines averaged, peak→64000, `gain = round(factor·64000/peak)`, update
   `factor = 1/(1−gain·k)`.
3. 6-point exposure sweep + per-channel OLS → write exposure (0x82.1/2/3), clamp
   `[0xd, 0xfff]`.
4. If coverage thin, trim lamp (0xf6.0x80) and repeat (≤2).

We already own the CCD line read (Phase 5 `0x86` stream) and the register-write
frame, so each step is implementable. **Open items before hardware iteration:**
the lamp F-135 wire address (above), the exact `.data` target constants, and the
filter-wheel/gate codes on the F-135 wire (TLA uses 0xf4/`0xe3..0xe5`).

## Still TBD (next steps toward the driven backend)

1. **Lamp F-135 wire address** — TLA uses `0xf6`; capture the F-135 equivalent
   (see Lamp section). Then the full driven CALIBRATE is implementable end to end.
2. **Motor/geometry registers** on PICM (`0x24`) — speed, stepper, advance.
3. **Remaining bank 0x82 timing regs** (4,5,10,0xb) and bank 0x84 reg 0/1.
4. **Confirm the open `.data` constants** (`_DAT_1006f268` target, gain `k`) by
   instrumenting on hardware — calibrate to known targets and read back.
5. Verify every "CONFIRMED" register against a real capture before relying on it
   (address-scheme caveat above).
