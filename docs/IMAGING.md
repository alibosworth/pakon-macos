# Pakon F-X35 imaging pipeline — "the look"

This document records how the original Kodak/Pakon software turns the raw `0x86`
image stream into the finished, characteristically-rendered scans the F-135 is
known for — and how that maps onto our tools. The wire/transport side is in
`docs/PROTOCOL.md`; this file is the image-processing side.

See the **PROVENANCE** note at the end: the OEM details here come from reverse
engineering the original Windows software for interoperability. The OEM binaries
and their data files are **not** committed to this repo.

## TL;DR

- The famous "Pakon look" is **not** the negative→positive inversion. The
  inversion is a single early step; the look is Kodak's **Ansel photofinishing
  ("minilab") pipeline** — a cascade of ~48 data-driven stages — stacked on top.
- The C-41 **orange-mask removal / inversion** is the **SCP (Scan Color
  Processing)** stage, via per-channel **Dmin** subtraction (`modifyDmin = true`),
  **not** a naive `max − raw`. This answers the long-standing "Q2" question.
- Our `tools/pakon_image.py` currently does **neither** inversion nor rendering —
  it outputs registered, autocropped **raw negatives** (orange mask intact) and
  defers inversion to external film software (Negative Lab Pro, darktable
  negadoctor). That is *a* look, not *the* Pakon look, because those tools use a
  different inversion + rendering philosophy.

## What our pipeline does today (for contrast)

`tools/pakon_image.py` (see `docs/PROTOCOL.md` → "Image format"):
deinterleave 16-bit LE samples → trilinear R/G/B line registration → wrap-order
de-interleave (whole-roll IR-band case) → autocrop → rotate → optional resample.
Output: 16-bit RGB TIFF **raw negative**. No mask removal, no scene balance, no
tone rendering.

## The OEM imaging architecture (Kodak "Ansel")

`PakonIMAu.dll` ("Pakon Image Acquisition") is built on Kodak's **Ansel** image
library — the same color science used in Kodak photofinishing minilabs. Evidence
from the decompile (`re/out/PakonIMAu.c`): build paths `\Atc\ansel\src\…`,
classes `CiColorCorrectionAnsel`, `AnsLut`, `AnsImaBuilder`, and an
`anselinstalldir/` data tree containing `minilab.txt` / `minilab.reg`.

The pipeline is **data-driven**: each stage reads ASCII LUT/param files from
`anselinstalldir/dataPathItems/<stage>/` (48 stages) and `Config/ColorCorrection/`.
The shipped install has 333 such files (220 `.dpi` params, 69 `.pf` profiles,
23 `.lut`, 21 `.map`).

### Stage order (the "look" is the whole cascade)

| # | Stage (dir) | Role |
|---|-------------|------|
| 1 | `filmLut/`  | film density → scene image (per-film characterization curve) |
| 2 | `SCPLut/`   | **Scan Color Processing — the C-41 orange-mask removal / inversion** |
| 3 | `dsba/`, `sba/` | **Digital Scene Balance Algorithm** — auto color + density balance per scene across the roll (the minilab magic) |
| 4 | `flesh/`    | flesh-tone-aware correction (the skin-tone rendering people rave about) |
| 5 | `toneHelper/` (+ decision trees), `contrast/`, `lighting/` | tonal rendering, per processing **path** |
| 6 | `fugc/`, `deRender/`, `reRender/` | gamut compression + RIMM/ROMM/ERIMM scene-referred colorspace management |
| 7 | `Config/ColorCorrection/*.pf` | final user-facing choice: `srgb.pf`, `romm.pf`, saturation `satminus15…satplus15`, B&W `cold_bw`/`warm_bw`/`sepia`, `ColRevLut*` |

Plus many supporting stages: `flare`, `falloff`, `dyefade`, `exposure`,
`gainOffset`, `blackPrinting`, `neutralGammaAdjust`, `noiseFiltering`,
`adaptSharp`/`SharpenAdjust`, etc.

Named **processing paths** select a whole rendering personality:
`CN-Enhanced`, `CN-Premium`, `DC-Premium`, `CP-Balance` (`CiColorCorrectionAnsel::bStartNewRoll`).

### File formats (parseable — ASCII)

**filmLut** (`.lut`) — a 1-D LUT, plain text:

```
LUT_NAME = filmLut-scanner-prod-gen-default-default-default.lut
NUM_LUT  = 4096        # 12-bit input domain
NUM_BANDS = 3          # R, G, B output
LUT_DATA = 0   0   0   0      # index  R  G  B  (tab-separated)
           1   1   1   1
           …
           4095 4095 4095 4095
```

The shipped default filmLut is an **identity** map ("Full default identity lut"),
and **no film-type-specific variants ship in this install**. So the OEM does the
film characterization at scan time (calibration + SCP Dmin auto-detection + DSBA),
not via a library of per-stock LUTs.

**SCPLut** (`.dpi`) — the inversion/mask parameters:

```
offsetOption          = ANS_SCPLUT_ZERO_PIVOT
modifyDmin            = true     # per-channel Dmin (film base) subtraction = mask removal
useSCPLut            = true
visualWeighting      = true
runSCPAfterLut       = true
proportionalCorrection = 0.7
slopeDeltaThreshold  = 0.30
```

So the **C-41 inversion is a mask-aware, density-space operation**: detect the
per-channel film base (Dmin), subtract/normalize it (`modifyDmin`), pivot at zero,
apply with 0.7 proportional correction — then the rest of the pipeline renders.

## Implications for our tools

- A faithful-to-Pakon positive needs **at minimum** the SCP-style Dmin inversion
  (per-channel base detection + density-space invert), which is well-specified
  above and implementable in `pakon_image.py` as an optional mode — a big step up
  from handing raw negatives to external software.
- The *full* look additionally needs DSBA (scene balance) + tone/contrast/colorspace
  rendering. Those are larger reverse-engineering efforts (the `.dpi` parameter
  formats + the algorithms); the LUTs being ASCII makes it feasible but not small.
- Keeping the current "output raw negative, invert externally" path is still
  valid and is the safest default; a Pakon-style mode would be additive.

## PROVENANCE

The OEM imaging details above come from reverse engineering the original
Kodak/Pakon Windows software (Ghidra decompilation of `PakonIMAu.dll` +
inspection of its `anselinstalldir/` data files), done **for interoperability**.
The OEM binaries, their LUT/profile data, and the decompilation output are
**third-party copyrighted material and are NOT committed to this repository**
(working copies live outside the tree under `pakon-scanning-software/` and `re/`,
both git-ignored). Only factual descriptions needed to understand and
interoperate with the format are recorded here.
