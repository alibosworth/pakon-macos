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

## Still TBD (next steps toward the driven backend)

1. **LampLevel register** — goes to the LAMP subsystem (separate address, not bank
   0x82/0x84). Find its address/reg (likely `AD_LAMP`-class write).
2. **Motor/geometry registers** on PICM (`0x24`) — speed, stepper, advance.
3. **Remaining bank 0x82 timing regs** (4,5,10,0xb) and bank 0x84 reg 0/1.
4. **The CALIBRATE feedback algorithm** (`EventScanCalibrate` thread) — how
   open-gate CCD readings drive Gain/Offset/Exposure to target white. This is the
   make-or-break piece; needs decompiling + on-hardware iteration.
5. Verify every "CONFIRMED" register against a real capture before relying on it
   (address-scheme caveat above).
