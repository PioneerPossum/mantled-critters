---
name: cybrdek-device
description: "cybrdek: Gil's Clockwork Pi (CM4 Lite) with its own Claude Code session; how audire-videre reaches it, what was seeded there on 2026-10-15, what is still open"
metadata:
  type: project
---

## 2026-10-15: first contact and seeding
- **Identity (verified):** hostname `cybrdek`, aarch64, Debian 12 (bookworm), user `sinewave`, 29 GB root with 22 GB free, `get_throttled` 0x0. It sits on the Mudi LAN at 192.168.10.176 (DHCP-assigned, may drift; re-verify by host key, not IP). ED25519 host key fingerprint `SHA256:Kg+wv9TppkCFHOL+cgT1g/aLIkpg+8/GA+D9cSAU+Z4`, checked by Gil on the device before trusting. See [[magi-net-8x-migration-status]] for the Mudi LAN.
- **Access:** dedicated key `~/.ssh/id_ed25519_clockwork` on audire-videre (public half installed by Gil with ssh-copy-id). Password login on cybrdek is still enabled; key-only is a one-liner for Gil to run on the device (sshd changes are denied for me, see [[secret-handoff-method]]). No ssh config alias was added.
- **Claude Code there:** v2.1.300 at `~/.local/bin/claude`, a session was already running (`claude resume`) with working dir `~/claude-projects` and its own history. No node/npm installed.
- **Seeded:** `~/claude-projects/CLAUDE.md` = `~/Projects/claude-projects/new-device-bootstrap/CLAUDE.md` (no secrets), and all 46 memory notes rsynced (no `--delete`) into `~/.claude/projects/-home-sinewave-claude-projects/memory/`; checksums of MEMORY.md and lessons-learned.md match. The running session reads CLAUDE.md only when it (re)starts, so it needs a restart or `/memory`; auto-memory is read at start too.
- **Open:** purpose of the device (Gil to say); key-only sshd; the live memory copy there will drift from audire-videre (refresh command is in the bootstrap file); no board CLI on cybrdek, so board updates for work done there go through a session on audire-videre.
- Related: [[magi-github-sync]] (repo pushed to repositorium as 0e51cde, github not pushed), [[lessons-learned]].

## 2026-10-15 (later): key-only login live, GitHub confirmed
- Gil ran the sshd drop-in on cybrdek (`/etc/ssh/sshd_config.d/20-keyonly.conf`: PasswordAuthentication no, KbdInteractiveAuthentication no; the pre-existing `10-access.conf` keeps PermitRootLogin no, MaxAuthTries 3, LoginGraceTime 30). The first attempt (name `10-keyonly.conf`) did nothing: the terminal wrapped the pasted line, so `tee` wrote only to the screen and the `sshd -t && reload` part never ran. Verified after the real run: `sshd -T` reports passwordauthentication no, key login works, a password attempt answers `Permission denied (publickey)`. Rule: long one-liners handed to Gil should stay under the terminal width, and I verify with `sshd -T` instead of trusting pasted output.
- Passwordless sudo works for `sinewave` on cybrdek (`sudo -n` succeeded).
- Correction to [[magi-github-sync]]: Gil pushed `github main` himself; verified `github/main` = `origin/main` = local HEAD = 0e51cde (2026-10-15). The "github not pushed" lines above are superseded. Untracked `magi-cluster/shared/__pycache__/` is still left out.
- Still open: what cybrdek is for; refreshing its copy of the memory when audire-videre's notes change.
