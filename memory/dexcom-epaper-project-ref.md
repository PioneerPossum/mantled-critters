---
name: dexcom-epaper-project-ref
description: "Reference for Gil's Dexcom CGM e-paper display project (separate hardware from magi-cluster) — architecture, credential pattern, and possible \"oculus\" hostname overlap with repositorium"
metadata: 
  node_type: memory
  type: reference
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-17T17:32:05.176Z
---

Gil's existing project (imported 2026-09-17 from Claude.ai project memory as context for a future Telegram-bot/notification bridge from magi-cluster — see [[magi-cluster-status]] for that discussion): a real-time Dexcom CGM (continuous glucose monitor) display using a Raspberry Pi + RP2040-Plus Pico driving a Waveshare 4.2" tri-color e-paper panel.

**Architecture:** `dexcom_host.py` (CPython, on the Pi) authenticates to Dexcom's Share API via `pydexcom` (needs `account_id`, a UUID from uam1.dexcom.com — not a phone-number username) and pushes newline-delimited JSON over USB serial to the Pico (MicroPython, `main.py`), which renders to the e-paper panel. Split deliberately because the RP2040-Plus has no Wi-Fi. Project dir: `/home/sinewave/waveshare-dexcom/`.

**Credential pattern — different from magi-cluster's `.env`-file approach:** uses `systemd-creds encrypt`/`LoadCredentialEncrypted` (OS-level encrypted credential injection) rather than plaintext env files, with a `_read_secret()` helper that checks `CREDENTIALS_DIRECTORY` first, falls back to env vars. Worth considering for magi-cluster's own credential handling if it ever needs hardening beyond the current `.env`-file convention.

**Runs on hostname `oculus`, user `sinewave`** (Pi 5, swapped in from an earlier Pi 2B). **Resolved 2026-09-17 — no overlap with repositorium:** confirmed by Gil these are two unrelated `oculus` references. This Dexcom project's `oculus` Pi is physically at Gil's **partner's house**, plugged into their home network, entirely separate hardware from magi-cluster — **explicitly out of scope for now**. Repositorium's own "oculus" history (leftover Dicaffeine NDI/VJ `dserver`) is a *different*, unrelated leftover project — see [[magi-repositorium-ndi-wip]]. Gil will likely rename this Pi's hostname (or upgrade it to a "2040W") once he can get it back on-site — worth checking whether that's happened before assuming `oculus` still refers to this device in any future session.

**Current state (as of the export): functional end-to-end.** Notable engineering details, mostly not directly relevant to a magi-cluster bridge but useful for understanding the existing system: e-paper vendor driver bugs fixed via subclassing (`epd_fixed.py`), dynamic layout positioning to avoid hardcoded-coordinate overlap, 180s Waveshare refresh-rate limit, 20hr anti-burn-in forced refresh, `ModemManager` disabled so it doesn't steal `/dev/ttyACM0`.

**Gil's stated working style on this project** (useful signal for how to approach the eventual bridge work too): wants to understand *why*, not just run commands; always uses venvs even without third-party deps; prioritizes security by default (encrypted creds over plaintext); subclasses vendor code rather than editing it; interrogates driver/API behavior empirically rather than trusting docs.

**How to apply:** this is reference-only until Gil confirms scope for the Telegram bot/notification bridge (see the magi-cluster Telegram integration discussion) — don't design or build glucose-data integration until that scope conversation happens and the oculus/repositorium question above is resolved.
