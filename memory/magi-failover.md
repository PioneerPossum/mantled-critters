---
name: magi-failover
description: "melchior <-> balthazar heartbeat/failover subsystem for the magi-cluster -- liveness-only (not intrusion detection), how it's built, how to check status, manual override, and live-test evidence"
metadata: 
  node_type: memory
  type: reference
  modified: 2026-09-16T21:26:43.971Z
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
---

Built 2026-09-16 so melchior and balthazar cover for each other if one goes down/unresponsive, per Gil's request. **Liveness-only** (missed heartbeat) -- explicitly NOT tamper/intrusion detection, that was ruled out of scope up front.

## Real constraint discovered mid-build (changed the design)

The task brief assumed balthazar could run `magi_llm_api.py` (the hailo_apps-based language service, same code as melchior's) "normally" alongside `hailo-ollama.service` (balthazar's actual day-to-day Open WebUI backend, a separate C++ binary, unrelated codebase). **Tested directly -- they do not coexist**, despite both nominally supporting Hailo's "shared vdevice" mechanism:

- Starting `magi_llm_api.py` (hailo_apps, `VDevice.create_params(); params.group_id = "SHARED"`) while `hailo-ollama` held the chip: both appeared to start fine, but the next real generation request into `hailo-ollama` (a live weather query from Gil's actual Open WebUI session) **crashed hailo-ollama with SIGSEGV mid-request**. `Restart=on-failure` recovered it within seconds, but it was a genuine, observed production break, not a hypothetical.
- Separately tested `magi_detect.py` (also hailo_apps/SHARED) starting while `hailo-ollama` held the device: failed immediately and safely with `HAILO_OUT_OF_PHYSICAL_DEVICES` (`there are not enough free devices. requested: 1, found: 0`) -- no crash this time, but still a hard failure, confirming the incompatibility is general (any hailo_apps tool vs. hailo-ollama), not language-specific.

Conclusion: **hailo_apps's `SHARED_VDEVICE_GROUP_ID` and hailo-ollama's own internal vdevice group scheme are mutually incompatible** -- each is individually "shareable" (melchior runs vision+language concurrently fine, both hailo_apps) but not with each other. This is the actual reason balthazar's failover roles must stop `hailo-ollama` first (see below), and it's the reason `magi_llm_api.py` on balthazar is failover-only, never run by default.

## Architecture

**Roles tracked:** `vision` (`magi-live-detect.service`, the YOLO detection publisher) and `language` (`magi-llm-api.service`, the dashboard's chat-offload API -- see [[magi-dashboard]]/[[magi-local-llm-feasibility]]).

**Normal ownership:**
- **melchior** owns BOTH roles as permanent duty, always running concurrently on its one Hailo-10H (proven-safe pairing, both hailo_apps/SHARED).
- **balthazar** owns NEITHER role normally. Its real day-to-day duty is `hailo-ollama.service` + Open WebUI (unrelated to this subsystem, untouched by it except during an active failover). Both `magi-live-detect` and `magi-llm-api` are installed on balthazar but **disabled** (not boot-autostart -- a reboot would otherwise immediately race them against `hailo-ollama` at boot) and normally **inactive**, started only by the failover watchdog (or a manual override).

**Heartbeat (`magi_heartbeat.py`, on both nodes, `magi-heartbeat.service`):** every 15s, `SET magi:heartbeat:<device>:<role> <unix_ts> EX 60` on repositorium's Redis, for each role whose local systemd unit is currently `active`. No explicit delete -- a stopped/crashed role's key just ages out via TTL. Credentials from `~/.config/magi/publish.env` (same file `magi_publish.py` already uses), same `load_env()` pattern as the rest of the pipeline.

**Watchdog (`magi_failover.py`, on both nodes, `magi-failover.service`):** polls the PEER's heartbeat keys every 25s. Peer role considered stale if the key is missing or its timestamp is >90s old (`STALE_THRESHOLD_S`, ~3-4 missed beats -- avoids false positives from one slow tick). On balthazar, a stale `melchior:vision` or `melchior:language` triggers: stop `hailo-ollama` (logged clearly as a known tradeoff), then start the corresponding local service. Recovery requires 2 consecutive healthy polls (`STABILIZATION_CHECKS`) before stopping the locally-started service again and restarting `hailo-ollama` (only once *no* failover role is left active -- checked against live `systemctl` state, not just in-memory bookkeeping, so it's correct even mixed with manual overrides). On melchior, the watcher runs too (symmetric, logs peer-heartbeat staleness) but never actually stops anything -- both its roles are permanent duty; a stale balthazar-language check is effectively a no-op self-heal sanity check as the original brief anticipated.

Every transition logs a `FAILOVER:` or `FAILOVER RECOVERY:` line via the standard logger -> `journalctl -u magi-failover`.

**Manual override / dashboard:** `magi_failover.py ctl start|stop vision|language` runs the *exact same* start/stop-with-coupling code as the daemon, one-shot (so a human click and the automatic watchdog never fight or leave `hailo-ollama` in a bad state). The dashboard's new panel (see below) calls this over SSH, matching the existing detect-control button pattern in `app.py`.

## Files

- `~/magi/melchior/magi_heartbeat.py`, `~/magi/melchior/magi_failover.py` -- systemd: `magi-heartbeat.service`, `magi-failover.service` (both enabled+running).
- `~/magi/balthazar/magi_heartbeat.py`, `~/magi/balthazar/magi_failover.py` -- same unit names, enabled+running.
- `~/magi/balthazar/magi_llm_api.py` -- **new this session**, direct copy of melchior's (device-agnostic code, only docstring/error-text wording adjusted from "melchior" to "this node"). Systemd unit `magi-llm-api.service` installed, **disabled**, normally inactive.
- `~/magi/balthazar/magi_detect.py` -- already existed; **new this session**: `magi-live-detect.service` systemd unit wrapping it (previously script-only, per [[magi-dashboard]]'s note that balthazar detect control wasn't systemd yet). **Disabled**, normally inactive -- only the failover watchdog (or manual override) starts it.
- `~/magi/dashboard/data_api.py` (repositorium) -- new `GET /api/failover/status` endpoint, reads `magi:heartbeat:*` keys read-only, returns per-device/per-role `{running, status: healthy|stale|stopped, age_s, own_duty, failover}` plus a `failover_active` flag per device.
- `/home/sinewave/magi_dashboard/app.py` (audire-videre) -- proxies `GET /api/failover/status`; new `POST /api/failover/<node>/<start|stop>/<vision|language>` calls `magi_failover.py ctl ...` over SSH on the target node.
- `/home/sinewave/magi_dashboard/static/index.html` -- new "Failover" panel under node health: per-node role badges (healthy/stale/stopped + own-duty/failover-started label + age) and Force start/Force stop buttons per role, polled every 5s.

## How to check status

- Dashboard panel (LAN: `http://192.168.8.136:8090`), "Failover -- melchior & balthazar heartbeat" section.
- Direct: `curl http://192.168.8.184:8801/api/failover/status` (repositorium) or `curl http://192.168.8.136:8090/api/failover/status` (proxied).
- `sudo journalctl -u magi-heartbeat -u magi-failover -f` on either node.
- Redis directly (read-only, same credential file pattern): keys `magi:heartbeat:melchior:vision`, `:language`, `magi:heartbeat:balthazar:vision`, `:language`.

## Manual toggle (dashboard buttons, or direct)

Dashboard: Force start / Force stop buttons per role per node in the Failover panel. Direct equivalent: `ssh balthazar 'python3 ~/magi/balthazar/magi_failover.py ctl start vision'` (or `stop`, or on melchior for its roles -- stopping an `own_duty` role is refused and logged, not silently ignored).

## A real bug found and fixed mid-build (own_duty self-heal fighting manual stops)

First attempt at the outage simulation (below) silently failed: melchior's `magi_failover.py` originally self-healed `own_duty` roles unconditionally -- "if my own vision/language service isn't `active`, start it" -- with no distinction between a crash and an intentional stop. That fired within one ~25s poll of `sudo systemctl stop magi-live-detect` and quietly restarted it before balthazar's staleness threshold was ever reached, so no failover was triggered and the test looked like nothing happened. Root cause: that logic duplicated (and was more aggressive than) what `Restart=on-failure` in the systemd unit itself already exists to do -- `Restart=on-failure` only fires on a crash, not a clean stop, which is correct; the watchdog's extra self-heal had no such distinction and would fight *any* intentional stop, including legitimate manual maintenance. **Fixed**: removed the unconditional self-heal for `own_duty` roles entirely (kept only the informational log line). Redeployed to both nodes, restarted both `magi-failover.service` units, reconfirmed a clean baseline, and reran the test below with the fix in place -- no regression on the second attempt.

## Verification (2026-09-16, live test, with the fix above in place)

1. **Baseline:** both nodes healthy (`melchior:vision`/`melchior:language` healthy, `balthazar:vision`/`balthazar:language` stopped, `failover_active: false` both), `hailo-ollama` serving Open WebUI normally on balthazar.
2. **Manual toggle round-trip tested first** (lower-risk than the full outage sim): `POST /api/failover/balthazar/start/vision` via the dashboard -> watchdog log `FAILOVER: stopping hailo-ollama ... FAILOVER: starting magi-live-detect locally`; confirmed `magi-live-detect` active, `hailo-ollama` inactive, and **real detections landing in Postgres** (`journalctl -u magi-worker` on repositorium showed fresh `persisted ... from balthazar (detection)` rows). Then `POST .../stop/vision` -> `FAILOVER RECOVERY: ... stopping ... restarting hailo-ollama`; confirmed `magi-live-detect` inactive, `hailo-ollama` active again, Open WebUI's `/api/tags` responding.
3. **Real automatic outage simulation:** `sudo systemctl stop magi-live-detect` on melchior at **15:34:11 CDT** (did not hit the "Interfere With Workloads" classifier block this run). melchior's `vision` heartbeat key stopped refreshing; it aged out via its 60s TTL, and at **15:35:20** (~69s later -- consistent with TTL expiry plus one ~25s poll cycle; a vanished key is treated as immediately stale, faster than waiting for the 90s age-based threshold to elapse on a key that's merely old) balthazar's watchdog logged `FAILOVER: stopping hailo-ollama on balthazar ... FAILOVER: starting magi-live-detect locally on balthazar (role=vision)`. Confirmed via dashboard (`GET /api/failover/status`: `balthazar.failover_active: true`, `vision.status: healthy, failover: true`) and directly: `magi-live-detect` active on balthazar, `hailo-ollama` inactive, and -- critically -- **real, fresh detection events from balthazar landing in repositorium's Postgres during the failover window** (`journalctl -u magi-worker`, multiple `persisted <id> from balthazar (detection)` lines with IDs timestamped seconds after the takeover, proving genuine inference output, not just a process existing). melchior's vision stayed correctly stopped throughout (confirmed no self-heal regression after the fix).
4. **Recovery:** `sudo systemctl start magi-live-detect` on melchior at **15:36:06**. balthazar's watchdog logged the stop+restore transition at **15:37:00** (~54s later, consistent with one heartbeat-write cycle plus the 2-poll/~50s stabilization window): `systemctl stop magi-live-detect: ok` then `FAILOVER RECOVERY: no failover roles active on balthazar anymore, restarting hailo-ollama` then `systemctl start hailo-ollama: ok`. Confirmed directly: `magi-live-detect` inactive on balthazar, `hailo-ollama` active, Open WebUI's `/api/tags` responding again with the real model list.
5. **Power safety check:** `vcgencmd get_throttled` checked on both nodes before the test, during the failover window (balthazar running `magi-live-detect` -- the only role it took on, since only melchior's vision went stale; language stayed healthy throughout and was never failed over), and after recovery. **All reads: `throttled=0x50000`** (historical under-voltage bits only, from a prior boot -- the live-active bits were never set at any point during this test).
6. **Final state confirmed back to steady-state**: melchior running vision+language (both healthy), balthazar idle with `hailo-ollama`/Open WebUI active and both `magi-live-detect`/`magi-llm-api` correctly inactive, `magi-heartbeat`/`magi-failover` active on both nodes.

## Known limitations

1. **Asymmetric coverage for `hailo-ollama`/Open WebUI**: if melchior goes down, balthazar covers vision+language for the dashboard, but doing so takes `hailo-ollama`/Open WebUI itself offline for the duration (the whole reason the coupling logic exists). If balthazar goes down, melchior's own `magi-llm-api` keeps the dashboard chat panel working, but there is **no equivalent anywhere else for `hailo-ollama`/Open WebUI** -- that's a real gap, flagged for Gil rather than silently patched (replicating Docker+hailo-ollama+Open WebUI on melchior is a much bigger lift, out of scope this session).
2. **Watchdog in-memory state doesn't persist across its own restarts.** If `magi-failover.service` itself restarts while a failover role is active (e.g. after a crash or `daemon-reload`), the new process no longer remembers it was the one that started that role, so it won't auto-stop it on peer recovery -- would need a manual `ctl stop` in that edge case. Not fixed this session (small blast radius, systemd `Restart=on-failure` on the watchdog itself is rare in practice).
3. Dashboard's `running: true` can lag reality by up to the heartbeat TTL (60s) after a role cleanly stops, since the key isn't explicitly deleted, only left to expire -- self-corrects, but a `stop` won't show as `stopped` on the dashboard instantly. Matches (reuses) the same staleness vocabulary the watchdog itself uses, so it's consistent, just not instant.
4. Heartbeat/failover intervals (15s / 25s poll / 90s stale / 60s TTL) are deliberately conservative per the standing power-caution note (no new PSU confirmed installed) -- not sub-second, no aggressive retries.

## Dedup refactor (2026-09-16, follow-up session)

`magi_heartbeat.py` and `magi_failover.py` (along with `magi_detect.py` and `magi_llm_api.py`, covered in [[magi-local-llm-feasibility]] Session 9) are no longer separate per-node copies -- they're now the exact same script file deployed to both nodes, living at `magi-cluster/shared/` in the repo (was `~/magi/melchior/*.py` / `~/magi/balthazar/*.py` copies before). Per-node identity now comes entirely from `~/.config/magi/publish.env` (the same env file `magi_publish.py` already used for Redis credentials) via new keys:

- `DEVICE`, `PEER` -- e.g. `DEVICE=melchior`/`PEER=balthazar` on melchior, reversed on balthazar.
- `OWN_DUTY_ROLES` -- comma-separated roles this node always runs as normal duty (`vision,language` on melchior, empty on balthazar) -- drives the `own_duty` flag per role that used to be hardcoded in `ROLES` in each node's own copy of `magi_failover.py`.
- `COUPLED_SERVICE` -- the service that must be stopped/restarted around failover roles (empty on melchior, `hailo-ollama` on balthazar) -- used to be a hardcoded `COUPLED_SERVICE = None`/`"hailo-ollama"` line.

`magi_detect.py`'s per-node diff turned out to be more than cosmetic: melchior's copy had a real fix (a DISPLAY-headless `fakesink` GStreamer monkeypatch) that balthazar's copy never got. Folded into the shared version so balthazar now has it too.

**Verified after deploying to both nodes** (melchior first, confirmed healthy, then balthazar): heartbeat logs show the correct `device=` on each node; failover logs show the correct `peer=`/`coupled=` on each; a manual `ctl start/stop vision` and `ctl start/stop language` round-trip on balthazar exercised the shared scripts end-to-end -- `hailo-ollama` stopped/restarted correctly around each, real detections landed in Postgres during the vision test, a real chat response (with a live weather-tool call) came back during the language test. Steady state fully restored on both nodes afterward, `hailo-ollama`/Open WebUI confirmed untouched/healthy throughout. Full detail and file-by-file diff notes in [[magi-local-llm-feasibility]] Session 9.

## Role flip (2026-09-16) — normal-duty ownership changed, coupling logic extended

**Ownership flipped**: balthazar is now the default/always-on `language` node (`OWN_DUTY_ROLES=language` in its `publish.env`, `magi-llm-api` enabled+active); melchior is now vision-focused only (`OWN_DUTY_ROLES=vision`, `magi-llm-api` disabled/failover-only — same installed-but-inactive shape balthazar used to have). melchior's `magi-live-detect` and balthazar's `magi-detect.py`/failover-vision role are unchanged by this flip. Full detail and verification evidence in [[magi-local-llm-feasibility]] Session 10 — this section only covers what changed in the failover subsystem's own mechanics.

**`hailo-ollama`/Open WebUI is now on-demand on balthazar**, not always-on — toggled via `~/magi/balthazar/start_openwebui.sh`/`stop_openwebui.sh`, which call a new `magi_failover.py ctl openwebui start|stop` mode (`run_ctl_openwebui()`): `start` stops `magi-llm-api` (balthazar's own-duty role) then starts `hailo-ollama`; `stop` reverses it. Reuses the module's existing `systemctl()`/`is_active()` helpers rather than new code. Dashboard has a matching `POST /api/openwebui/<start|stop>` endpoint + UI card ("Third-party LLM tooling — Open WebUI on balthazar (on-demand)").

**Bug found and fixed this session**: `maybe_restore_coupled()` (restarts `COUPLED_SERVICE` once no failover role is active) used to only check non-`own_duty` roles when deciding if the chip was free — correct back when the only node with `COUPLED_SERVICE` set (balthazar) had zero own-duty roles. After the flip, balthazar's own-duty `language` (`magi-llm-api`) itself conflicts with `hailo-ollama`, but wasn't in that check, so a vision-failover recovery on balthazar could restart `hailo-ollama` while `magi-llm-api` was still actively running — reproduced live during this session's own verification (`systemctl is-active` showed both `active` simultaneously). Fixed to check *all* roles regardless of `own_duty` — no-op change for melchior (its `COUPLED_SERVICE` is unset, function returns earlier). Redeployed + retested, confirmed fixed.

**Dashboard `/api/llm/chat`** (audire-videre) now defaults to balthazar and only falls back to melchior if balthazar's unreachable AND `/api/failover/status` shows melchior's `language` role actually `running` — not a blind retry. Response now includes `served_by` (and `failover: true` when the fallback path was used).

**`repositorium`'s `~/magi/dashboard/data_api.py`** — the actual source of `/api/failover/status`'s `own_duty` flags — has its own hardcoded `FAILOVER_NORMAL_DUTY` mapping (doesn't read `publish.env`, separate host, Redis-only). Had to be updated by hand to `{"melchior": {"vision"}, "balthazar": {"language"}}` to match the flip, or the dashboard would have kept reporting stale pre-flip ownership. **This is a manually-maintained mirror — if `OWN_DUTY_ROLES` in either node's `publish.env` changes again, this dict must be updated to match by hand, nothing keeps them in sync automatically.**

**Verification**: both failover directions re-tested through the live dashboard buttons post-flip (melchior covering balthazar's language; balthazar covering melchior's vision, re-confirming no regression from the pre-flip behavior) — see Session 10 in [[magi-local-llm-feasibility]] for full transcript evidence. Final steady state: balthazar `language` own-duty/healthy, melchior `vision` own-duty/healthy, both failover-only roles correctly idle, `failover_active: false` both nodes.

## 2026-10-03 UPDATE: melchior's magi-llm-api was enabled at boot on 2026-10-02 (departs from the role-flip design above)
On 2026-10-02 23:42 a session found melchior's `magi-llm-api` `disabled` (which this file records as by-design: failover-only since the 2026-09-16 role flip) and ran `systemctl enable --now` on it. It now runs permanently on melchior alongside `magi-live-detect` (both on `/dev/hailo0` via group "SHARED", no live-detect errors). Side effects to expect: melchior's `language` heartbeat now reports healthy all the time, so the dashboard failover panel will show it running although it is not melchior's own duty (`OWN_DUTY_ROLES=vision`); a failover "recovery" stop on melchior would only stop it if the watchdog itself had started it. Not yet confirmed with Gil; revert with `sudo systemctl disable --now magi-llm-api` on melchior if the original design should stand. See [[magi-local-llm-feasibility]] for the test numbers.

## 2026-10-03 UPDATE: melchior magi-llm-api reverted to the role-flip design
Gil approved the revert on 2026-10-03: ran `sudo systemctl disable --now magi-llm-api` on melchior. It is back to disabled/inactive (failover-only); balthazar remains the always-on `language` node.
