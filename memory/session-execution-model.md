---
name: session-execution-model
description: this Claude Code session runs persistently ON audire-videre itself (not an ephemeral cloud container) — what that does and doesn't imply for continuity
metadata:
  type: project
---

Confirmed by Gil (2026-09-22): this session is a **persistent, on-device session running directly on audire-videre**, not the ephemeral-cloud-container model described generically elsewhere (that boilerplate — "isolated container, reclaimed after inactivity" — does not apply here). Evidence already consistent with this before it was confirmed: direct hardware state (ThinkStation, real NICs), files/NetworkManager profiles created via polkit that must survive audire-videre's own reboots, and the session picking back up after the [[audire-videre-boot-lvm]] reboot rather than starting fresh.

**What this changes:** the real disappearance risks are audire-videre rebooting or losing power, or whatever process hosts this session being restarted — not a timer-based container reclaim. `/tmp` and any plain background process are real and persist across turns (not a fresh container each time), but still do NOT survive an audire-videre reboot unless made into a proper systemd unit.

**What this does NOT change:** the durability approach used throughout this project was already built for the stricter assumption (state might not survive a host reboot or this session ending), so nothing needs to be redone:
- Long-running work that must survive reboots/session gaps → a systemd service (user unit + `loginctl enable-linger`, as done for [[audire-videre-monitor]] and the D555 streamer), not a bare `nohup`/nohup'd loop.
- Findings/decisions worth keeping → written to a memory file as they're learned, not held only in conversation context (multi-hour idle gaps during this project showed conversation-only state is the real continuity risk, not the host).
- Anything genuinely ephemeral (scratch test files, `/tmp` output) → fine as-is, cleared on a real reboot.

The multi-hour "session froze while Gil was AFK" gaps observed 2026-09-20/21 were the session going idle, not the environment disappearing — the underlying host and its systemd services ran the whole time; only re-arm-on-wake tooling (the `Monitor` tail) needed catching up, which is exactly why that tail was demoted to a convenience wrapper around a service that doesn't need it.

- 2026-10-03 UPDATE: /list-agents showed no other Claude sessions on 2026-10-02 and three idle ones on 2026-10-03 in /home/sinewave/Projects/claude-projects (claude-projects-43, -f3, -2e); this session is named "context window visualization mod".
