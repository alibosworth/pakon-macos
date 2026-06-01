---
name: project-resources-folder
description: Capture artifacts live in resources/ at repo root, not tools/
metadata:
  type: project
---

`.pakfw` and `.pakscan` files are in `resources/` at the repo root — not in `tools/`.

**Why:** Tools dir holds source code; resources holds binary/capture artifacts that are gitignored.

**How to apply:** When referencing firmware or scan scripts in code or docs, use `resources/f135.pakfw`, `resources/scan_fullroll.pakscan`, etc.
