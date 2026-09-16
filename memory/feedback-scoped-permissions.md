---
name: feedback-scoped-permissions
description: "Gil's preference for narrowly-scoped one-off Bash permission rules over broad standing grants when auto-mode's classifier blocks an infrastructure-writing action"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e5718790-74ec-4313-bedc-feb387a7b25c
  modified: 2026-09-14T22:48:39.548Z
---

When Claude Code's auto-mode classifier blocks an action (e.g. "Modify Shared Resources" for writing to network device configs, "Expose Local Services" for binding a tunnel to all interfaces), Gil's preferred resolution is a **narrowly-scoped, one-off permission rule** added to `.claude/settings.local.json` matching the exact command — not a broad standing grant (e.g. not a wildcard `Bash(ssh *)` or a general auto-mode allow-list entry).

**Why:** explicitly asked "any risks with having you do it yourself?" and, on hearing that a broad grant would remove a safety backstop for *future* unreviewed actions (while a narrow one only covers the specific already-discussed action), chose the narrow option every time this came up in the 2026-09-14 session.

**How to apply:** when hitting this kind of classifier block, don't default to asking for a wide permission just because it would reduce friction on likely-similar future actions in the same session. Propose the narrowest rule that covers the specific command already agreed on, and let Gil explicitly ask for something broader if he wants it. Multiple scoped rules accumulating in `settings.local.json` over a session is fine and expected — that's the intended tradeoff (some repeated friction, in exchange for each write action getting individually reviewed).
