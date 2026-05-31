---
name: feedback_session_end
description: When user says "finish the session", update STATUS.md, PROTOCOL.md, SKILL.md, AND README.md
metadata:
  type: feedback
---

When the user says "finish the session" (or similar end-of-session instruction), always update **all four** of these files:

1. `STATUS.md` — short "where we left off" snapshot
2. `docs/PROTOCOL.md` — living protocol notes
3. `.claude/skills/pakon-scanner/SKILL.md` — the skill guide loaded at session start
4. `README.md` — user-facing docs; must stay in sync with what the tools actually do

**Why:** The user explicitly asked that README be included at session end (2026-05-31). It was being omitted. All four files serve different audiences but must stay consistent.

**How to apply:** Whenever a session-end summary/commit is requested, treat README as mandatory, not optional.
