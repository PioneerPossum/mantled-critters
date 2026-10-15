---
name: board-handoff-method
description: "How memory findings reach the dashboard board without colliding with the board session: fragment.json in ~/Projects/claude-projects/board-handoff, validated and imported by the board session; public side never carries addresses/personal info"
metadata:
  node_type: memory
  type: reference
---

**2026-10-09:** Gil asked for the dashboard board to carry all memory findings, fixes, projects, blockers, resolutions and suggestions, with sensitive details only behind login. A separate session (claude-projects-59) owns the board (`~/Projects/claude-projects/magi-board/`, CLI `~/bin/magi-board`, cards PRIVATE unless a card has an explicit `public` block that its scanner accepts). It already held 94 cards from memory, so this session only handed over a delta.
- **Method:** never edit `magi-board/` or `~/magi_dashboard/static/board.*`; write `~/Projects/claude-projects/board-handoff/fragment.json` ({"cards","log"}), check with `magi-board validate FILE` (read-only), message the board session; it runs `magi-board import` itself. First fragment: 12 cards + 7 log entries (VJ sign-in lock, weighted/crowd feedback, staged tempo/beat update, ESP32 cam2 link, ramiel key-only SSH, SSH key migration, venue time source, lessons, resolutions for d555-dds / github-sync / overflow-storage / mudi backup). Same folder has `private.json` (reference, holds addresses, login-gated only), `build_dataset.py`, README.
- **Rule:** the public side NEVER carries IPs, MACs, serials, ports, paths, tailnet/VPN/router/WAN details, secret names, personal or location info (partners, other sites, workplace), or security weaknesses. No passwords/keys/tokens anywhere. After finishing any MAGI task, update the board via `magi-board card|log` (its own hook may do it).
- Pending sources for the board sync: memory files `magi-https.md` and `magi-live-page.md` (written by other forks 2026-10-09).
Related: [[secret-handoff-method]], [[magi-github-sync]], [[magi-dashboard]].
