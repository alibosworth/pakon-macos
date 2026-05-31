# Firmware blobs

The Pakon scanners are Cypress/Anchor **EZ-USB FX2** devices. On cold power-on
they come up in the FX2 bootloader state and must be handed a firmware image
over the standard FX2 download protocol (vendor request `0xA0` to internal
8051 RAM) before they re-enumerate as the scanner (`0F05:F135`).

The firmware images ship as **Intel HEX** files in the `FX35Package/` folder of
the [`ktkaufman03/FX35`](https://github.com/ktkaufman03/FX35) repository.

## Provenance / legal note

These `.hex` files are **device-bootstrapping artifacts**. They are used as-is
to bring the hardware up and are **not** reverse-engineered or modified. They
are not part of this project's clean-room work; this project only ships a copy
for convenience and documents where it came from. If redistribution terms are
unclear, the build can instead point at a user-supplied path.

## Model → file mapping (TBD)

Populate this table in Phase 1, when the correct `.hex` is copied in here and
verified to bring up the specific hardware on hand:

| Model            | HEX file   | Verified |
|------------------|------------|----------|
| Pakon F-135      | f135.hex   | no       |
| F-235 / F-335    | (TBD)      | no       |
| "Plus" variants  | (TBD)      | no       |

> No `.hex` is committed yet — it is added in Phase 1 alongside the firmware
> download implementation, with its exact source path recorded above.
