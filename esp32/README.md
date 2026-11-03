# esp32: firmware and receivers for the MAGI sensing/AV work

Source only. No credentials, build output or flash dumps are in this repo; each project has a `.gitignore` for `include/secrets.h` and `.pio/`.

| Folder | What it is | Status |
|---|---|---|
| `mesh/` | PlatformIO project, envs `cam` (MJPEG camera), `lcd`, `mesh` (BLE crowd-density beacons: aggregates only, hashed with a per-boot salt, nothing stored), `pair`, `probe`. | Built and flashed on the author's boards (2026-10-09). |
| `wifi-canary/` | ESP32 Wi-Fi link canary firmware plus the receiver (`rx/`) and secrets-escape test. | Built; BSSID in `src/main.cpp` is a placeholder here. |
| `crowd-sense/` + `crowd-rx/` | XIAO ESP32S3 Sense camera-motion + mic-level firmware, and the UDP receiver `crowd_rx.py` (with tests). | **Proposal, never flashed.** |

## Secrets
`mesh/` and `wifi-canary/` generate `include/secrets.h` at build time from private files (`scripts/secrets.py`, 3-digit OCTAL C escapes on purpose: hex escapes silently corrupt some passwords; check with `wifi-canary/tests/test_secrets_escape.py scripts/secrets.py`). `crowd-sense/` uses `crowd-rx/gen_secrets.sh`. The `secrets.h.example` files are placeholders.

## Privacy rule for the BLE mesh
Aggregates only (counts, movement index); no addresses are printed, sent or stored. Say plainly at events that ambient radio activity is counted anonymously.
