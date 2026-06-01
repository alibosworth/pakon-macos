---
name: project-phase6-web
description: Phase 6 is a Python web service (web/), not the Swift app (MacPakon/) which was abandoned
metadata:
  type: project
---

The Swift macOS app (`MacPakon/`) was abandoned — scalar Swift decode pipeline too slow (minutes vs seconds for Python/numpy), hard to distribute.

Phase 6 replaced by `web/` — FastAPI + SSE + single-page HTML UI. Completed 2026-05-31.

**Start:** `uvicorn web.app:app --host 0.0.0.0 --port 8000` from repo root.

**Why:** Web service runs on the Linux box where the scanner lives; any device on the LAN can use it. Reuses existing C tools and pakon_image.py — no new decode logic needed.

**How to apply:** Direct any UI or client work to `web/`. MacPakon/ still exists in the repo but is not the active path.
