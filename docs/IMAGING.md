# Pakon F-X35 imaging pipeline — "the look"

How the raw `0x86` image stream becomes a finished scan, and how our tools
reproduce it. Wire/transport is in `docs/PROTOCOL.md`; this is the image side.

See **PROVENANCE** at the end: the OEM details come from reverse engineering the
original Windows software for interoperability. OEM binaries/data are **not**
committed.

## TL;DR (current)

- The C-41 negative→positive **inversion** is the OEM **"ColNeg" path**
  (`PIColorCorrectColNegPlanarScan`), a single shared **log-density LUT** plus a
  3×3+offset crosstalk matrix — **not** the SCP stage (earlier guess), and not a
  naive `max − raw`. Recovered exactly.
- The vibrant **JPEG "look"** is the inverted image run through Kodak's **`rpd.pf`
  ICC rendering profile** + a scene-balance/tone pass. The plain **TIFF** is the
  scene-referred positive (no render).
- `tools/pakon_image.py` now implements **both**: `--invert-c41` (faithful
  positive) and `--jpeg` (rpd.pf render). It also auto-detects per-zone channel
  order, registers the trilinear lines, autocrops, and detects frames.
- The web service is a **two-stage minilab flow**: prescan → operator confirms
  crops in the browser → high-res export.

## The recovered inversion (the important bit)

Source: decompiled `PakonIMAu.dll` (`re/out/PakonIMAu.c`) + `Config/ColorCorrection/`.
The OEM exposes export toggles (PSI "Other Options", classes `CiColorCorrection`
vs `CiColorCorrectionKodak`):

1. **Color Correction (12-bit RPD)** — `rpd.pf`, a real Kodak KCMS ICC profile
   (a `RPD_dls_3` + `yellow5` cascade). The Kodak colour science.
2. **Color Scene Balance (8-bit sRGB)** — SBA/DSBA, per-scene auto colour+density
   balance (brightness/neutralise/pop).
3. **Color Adjustments** — brightness/contrast/green/blue sliders; `Defaults.ini`
   shows these default to **neutral** for nearly all film products.

### ColNeg LUT + matrix — `Config/ColorCorrection/_ClientColNeg*.txt`

- **`_ClientColNegLut.txt`** — a single shared 14-bit curve, fit *exactly* (0 error
  over all 16384 entries):

  ```
  out = 3500 · log10(16383 / in)        [in = 0 → 16383]
  ```

  i.e. `density = log10(reference / signal)` scaled at 3500 code-values per
  density decade. THIS is the tonal flip. (`pakon_image.py:_c41_lut`.)
- **`_ClientColNegMat.txt`** — a 3×3 + offset dye-crosstalk / orange-mask matrix
  (diag ≈1.1, small negative off-diagonals, offsets [-82.6, -586.9, -707.8]).

**Correction to the old note:** the **SCP** stage (`AnsSCPLut`, `FUN_10287eb0`) is
NOT the inversion — it's a per-channel **affine** LUT (`out = i·slope − offset`),
a balance/mask-normalisation step inside the separate Ansel "premium" cascade.
A positive-slope linear map can't flip a negative. `filmLut` ships as identity, so
no characterization curve is involved.

## What `tools/pakon_image.py` does today

Deinterleave 16-bit LE → **fixed per-zone channel order** → trilinear R/G/B line
registration → wrap-order de-interleave (whole-roll IR-band case) → autocrop →
detect frames → per-frame crop → invert/render → TIFF/JPEG.

- **`--invert-c41`** (the faithful positive): per-channel **Dmin** normalisation
  (orange-mask removal / white balance — the film base is measured robustly as a
  median of local high-percentile bands, never a single whole-roll percentile) →
  the ColNeg log LUT → sRGB encode. **Matrix and gray-world WB are OFF by default**
  — the matrix offsets over-subtract G/B in our scale (red cast), and gray-world
  strips intentional scene warmth (blue lean). Dmin normalisation alone matches
  the OEM. Verified against the OEM reference scans of two rolls.
- **`--jpeg`** (the vibrant render): the sRGB positive → `rpd.pf` via littleCMS →
  an SBA surrogate (per-channel auto-levels + midtone gamma) + a **soft highlight
  shoulder** that, unlike the OEM, does *not* blow out highlights. Flags
  `--rpd-profile`, `--jpeg-gamma`, `--jpeg-quality`. Profiles live in `profiles/`
  (committed; Kodak © — see `profiles/README.md`).

### Channel order (a fixed constant — was the "purple" bug)

The two CCD output taps emit R/G/B in a fixed, zone-specific interleave order. It
is a **hardware/replay-phase constant, NOT per-scan**:

| zone | interleave |
|------|-----------|
| zone0 (after-IR)  | pos0=R, pos1=G, pos2=B  (perm `{r:b, g:r, b:g}`) |
| zone1 (before-IR) | pos0=B, pos1=R, pos2=G  (identity) |

Verified against OEM refs on two different rolls. `--channel-order fixed`
(default). Orange-base **auto-detection** (`--channel-order auto`) was tried but is
unreliable on dark/red-dominant rolls (the bright percentile catches scene
highlights, not clean film base, and flips G/B → purple cast), so it is opt-in.

### Framing

`find_frame_grid`: never forces a count. Measures the true frame **pitch** by
autocorrelation of the per-row detail profile → count = `round(span/pitch)` →
phase-locked uniform comb → snap each tooth to its gap → drop leader/partial end
cells. The caller crops a **fixed 3000-px window centred between gaps** (the
leftover splits evenly as edge margin; per-gap rebate measurement was unreliable).
In the web flow the detected centres only *seed* the interactive confirmation.

## The OEM "Ansel" cascade (the full minilab look — context)

`PakonIMAu.dll` is built on Kodak's **Ansel** library (build paths `\Atc\ansel\src\`,
classes `CiColorCorrectionAnsel`, `AnsLut`, `AnsImaBuilder`; `anselinstalldir/`).
Data-driven: ~48 stages of ASCII LUT/param files (`dataPathItems/<stage>/`,
`Config/ColorCorrection/`; 333 files in the install). Stage order:

| # | Stage | Role |
|---|-------|------|
| 1 | `filmLut/` | film density → scene (ships identity) |
| 2 | `SCPLut/`  | Scan Color Processing — per-channel **balance** (not the inversion) |
| 3 | `dsba/`,`sba/` | Digital Scene Balance — per-scene auto colour+density balance |
| 4 | `flesh/` | flesh-tone correction |
| 5 | `toneHelper/`,`contrast/`,`lighting/` | tonal rendering per **path** (`CN-Enhanced`, `CN-Premium`, `DC-Premium`, `CP-Balance`) |
| 6 | `fugc/`,`deRender/`,`reRender/` | gamut + RIMM/ROMM colorspace mgmt |
| 7 | `Config/ColorCorrection/*.pf` | output colourspace (`srgb.pf`, `romm.pf`, `satminus15…satplus15`, B&W, `ColRevLut*`) |

Our `--jpeg` path reproduces the *practical* result (RPD profile + balance/tone)
without re-implementing the whole cascade.

## Digital ICE (dust/scratch removal) — NOT implemented

The scanner captures an **IR channel** (separate Ir lamp/exposure;
`WaitForLamp_Ir`, `Current_Ir`, `CcdExposure_Ir`; the IR is a defect map — IR
passes through dye but is blocked by physical dust/scratches). The OEM applies
Digital ICE via **`DMLDICELib.dll`** (loaded by `CN_CiDLLDigitalIce`; PSI "Use
Scratch Removal"; `IrChannelSavedInPlanarFile`, `IrCrossTalkFactor`).

**We do neither.** `find_ir_band` locates the IR band only to delimit the visible
zones (the IR lands mid-line and the image wraps around it), then **discards** it.
A clean-room ICE (use the IR plane → threshold to a defect mask → inpaint) is a
possible future feature; the IR data is captured but currently thrown away. Open
question: whether our ~658-col IR band is a full per-pixel-aligned IR image or a
narrower readout strip — verify before building on it.

## PROVENANCE

OEM imaging details come from reverse engineering the original Kodak/Pakon Windows
software (Ghidra decompilation of `PakonIMAu.dll`/`TLA`/`TLC` + inspection of the
`Config/ColorCorrection/` data) **for interoperability**. The OEM binaries and the
decompilation output are third-party copyrighted and are **NOT committed** (working
copies under `pakon-scanning-software/` and `re/`, git-ignored). The Kodak ICC
profiles + ColNeg data needed to reproduce the inversion are committed under
`profiles/` for personal/local use only (see `profiles/README.md`).
