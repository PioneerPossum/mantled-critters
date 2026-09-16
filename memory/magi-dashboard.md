---
name: magi-dashboard
description: "The magi-cluster web dashboard (remote status/control UI) — where it runs, how to access/start/stop it, what it shows, known limitations"
metadata: 
  node_type: memory
  type: reference
  modified: 2026-09-16T20:39:40.827Z
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
---

Built 2026-09-15 so Gil (who travels and isn't always on-site) can check the magi-cluster's live status and activity remotely. See [[magi-cluster-status]] for the underlying infrastructure this displays.

## Where it runs

Two small components, split by where each has the most direct access to what it needs:

- **Main dashboard app — `audire-videre`** (`/home/sinewave/magi_dashboard/app.py`, Flask, port **8090**). Hosted here because this box already has passwordless SSH configured to melchior/balthazar/repositorium/edgerunner (needed for node health + detection-service control) and physically hosts the D555 camera (needed for the live preview, zero network hop). Serves the frontend (`static/index.html`) and a small JSON API.
- **Data API — `repositorium`** (`~/magi/dashboard/data_api.py`, Flask, port **8801**, systemd unit `magi-dashboard-data.service`, enabled). Hosted here because it's the only box with local (`127.0.0.1`) Postgres/Redis access under the current `pg_hba.conf` (which only allows melchior/balthazar/localhost — deliberately **not** widened to include audire-videre, to avoid touching Postgres's access control for this). Reads `~/.config/magi/worker.env` for credentials (same file `magi_worker.py` already uses) and exposes read-only aggregate JSON only — no raw DB access, no credentials, no write endpoints. The main dashboard app calls this over plain HTTP on the LAN.

Why not host it all on repositorium: SSH from repositorium out to melchior/balthazar/edgerunner isn't set up (repositorium currently only has an `authorized_keys` for *inbound* SSH, no outbound keys/config to the other nodes), and this environment's permission system blocks adding new SSH keys/authorized_keys as an "Unauthorized Persistence" action — so audire-videre's already-existing SSH trust was the right foundation to build node-health/control on instead of creating new standing access.

## Access

- **LAN URL (works now):** `http://192.168.8.136:8090`
- **Tailnet URL: not yet set up.** audire-videre and repositorium are not on Gil's Tailscale tailnet (`tail944f9e.ts.net`); only **edgerunner** is, and it already runs a `tailscale serve` reverse proxy for the Netgear switch UI (see [[gs308epp-switch-api]]) — the same pattern would work here. **edgerunner was unreachable all session** (`ssh: connect to host 10.0.0.137 port 22: No route to host`, checked repeatedly over several hours), so this couldn't be set up or verified. Once edgerunner is back up, run on edgerunner:
  ```
  sudo tailscale serve --https=8090 http://192.168.8.136:8090
  ```
  That should expose the dashboard at `https://edgerunner.tail944f9e.ts.net:8090` on the tailnet, without opening anything to the public internet (do **not** use `tailscale funnel`). Until then, remote access requires a VPN/tunnel into the home LAN some other way, or on-site/LAN access only.
- Not exposed to the open internet anywhere. No auth layer on the dashboard itself — acceptable only because it's LAN/tailnet-only; do not put it behind a public-facing proxy without adding one.

## Start / stop / restart

**Main app (audire-videre)** — plain background process (nohup), *not* systemd, because audire-videre's `sinewave` user has no passwordless sudo (confirmed earlier in [[magi-cluster-status]]'s D555-bridge section) so `sudo systemctl`/`/etc/systemd/system` isn't scriptable here, and this box also has no working user-lingering systemd setup verified for this app (unlike `magi-d555-stream.service`, which a concurrent session did set up as a user unit for the *streamer* — see below). Promoting the dashboard itself to a user unit the same way would be a reasonable next step but wasn't done this session (budget).
```
/home/sinewave/magi_dashboard/start_dashboard.sh   # kills any old copy by exact path, starts fresh
/home/sinewave/magi_dashboard/stop_dashboard.sh
```
Log: `/home/sinewave/magi_dashboard.log`. **Gotcha already hit and fixed once:** the start script must invoke `app.py` by absolute path — a `cd ... && python3 app.py` launch shows up in `ps` as just `python3 app.py` with no distinguishing path, so a path-based `pkill -f` silently matches nothing on "restart," leaving a stale pre-restart process serving forever while the new one dies on the port conflict. Both scripts now use the absolute path consistently; don't regress this.

**Data API (repositorium)** — real systemd unit:
```
sudo systemctl status|restart|stop magi-dashboard-data   # on repositorium
sudo journalctl -u magi-dashboard-data -f
```

**Camera streamer (audire-videre)** — `magi-d555-stream.service`, a **user** systemd unit (`systemctl --user ...`), set up by a concurrent session this same day — see the "Systemd promotion" section in [[magi-cluster-status]] for why it's user-level (no passwordless sudo on this box) and how `loginctl enable-linger` makes it survive without an interactive login. The dashboard's MJPEG preview code lives *inside* `magi_stream_d555.py` (same process, same capture loop — see below), so this is the same service; don't run a second copy manually with `nohup`, it'll fight the systemd-managed one for the RealSense device handle and the preview port. Manage with `systemctl --user status|restart|stop magi-d555-stream`.

**melchior's detect service** — also promoted to systemd (`magi-live-detect`, system-level, melchior has passwordless sudo) by that same concurrent session. The dashboard's start/stop/restart buttons for melchior now call `sudo systemctl start|stop|restart magi-live-detect` over SSH (**not** the old raw `start_live_detect.sh`/`stop_live_detect.sh` scripts) — calling the raw scripts directly would just get fought/undone by systemd's `Restart=on-failure`. Verified working: a restart via the dashboard button brought detection back within ~10s with a fresh PID.

**balthazar's detect service** — **no systemd unit yet** (out of scope for the concurrent systemd-promotion session). Dashboard control still uses the original raw scripts, added this session: `~/magi/balthazar/start_detect.sh` / `stop_detect.sh`, mirroring melchior's pre-systemd pattern (kill by `fuser /dev/hailo0`, not by process/script name — `hailo_apps` calls `setproctitle()` so a plain `pkill -f magi_detect.py` silently matches nothing). If balthazar ever gets promoted to systemd too, **update `NODES["balthazar"]` in `app.py` to add a `restart_cmd` using `systemctl`**, same as melchior, or the dashboard's control buttons will start fighting it.

## What it shows

- **Cluster node health** — melchior/balthazar/repositorium/edgerunner: reachable y/n, uptime, load average, SSH round-trip latency. Polled live via SSH each request (no caching), ~5s refresh in the UI.
- **HAT interaction** — per Hailo node: whether the detect process is genuinely running (via `fuser /dev/hailo0`, **not** `pgrep -f "Hailo Detection App"` — see the bug note below, that approach is broken), whether the Hailo device itself is visible (`hailortcli scan`), and Start/Restart/Stop buttons. No chip-temperature readout — `hailortcli measure-power`/temp commands need exclusive device access and fail while detection is running; skipped per the task's own "don't spend excessive effort here" guidance.
- **Camera** — live MJPEG preview at `/api/camera/mjpeg` (also a single-frame `/api/camera/snapshot.jpg`), proxied through the main app from a small HTTP server added directly inside `magi_stream_d555.py` (threaded, shares the same capture loop/frame buffer as the RTP-to-melchior path — no second RealSense pipeline opened, the device doesn't like concurrent opens). ~12fps preview cap, independent of the 30fps capture/encode rate to melchior.
- **Data flow** (Redis `magi:detections` stream + Postgres `events`) — stream length, consumer-group lag (`XINFO GROUPS`), events in the last 60s/5m, rate/min, a bar chart of detection counts by class (last 10 min), and a live table of the most recent ~15 detections (device/class/confidence/time-ago). This is served by repositorium's data API and polled every 3s.
- **Pi network topology** — static description (from [[magi-cluster-status]]'s network-topology section) of the two physical LAN segments and how data flows between nodes; not live-probed, just documentation rendered in the UI.
- **Failover status (added 2026-09-16, see [[magi-failover]])** — per-node/per-role (vision/language) heartbeat health (healthy/stale/stopped + age), whether each node is currently in "failover mode," and Force start/Force stop override buttons per role. Read from repositorium's Redis `magi:heartbeat:*` keys via a new `data_api.py` endpoint, polled every 5s.

Frontend follows the `dataviz` skill's method: dark-mode-selected theme using its validated reference palette (status colors for node up/down and detect running/stopped, single-hue blue for the class-count bar ranking, direct labels instead of a legend since each bar/card is already named).

## Local LLM chat — "offload to melchior" (added 2026-09-16)

A third component: **`magi-llm-api`** on melchior (`~/magi/melchior/magi_llm_api.py`, Flask, port **8802**, systemd unit `magi-llm-api.service`, enabled). Wraps melchior's Hailo-10H local LLM (Qwen2.5-Coder-1.5B-Instruct via `hailo_platform.genai.LLM`) — see [[magi-local-llm-feasibility]] Session 4/5 for the full story of how this was proven and built. `POST /chat {"prompt": "..."}` → `{"response": "...", "elapsed_s": ...}`, synchronous, requests serialized (single non-blocking lock — a second concurrent request gets `429 busy` immediately, no queueing). Same systemd pattern as `magi-live-detect` (`source ~/hailo-apps/setup_env.sh` in ExecStart, `User=sinewave`, `Restart=on-failure`).

The main dashboard app proxies to it at `POST /api/llm/chat` (90s timeout, relays melchior's status/body as-is including `429`), and the frontend (`static/index.html`) has a "Local LLM — offload to melchior" panel: textarea, Send button, response box with a thinking/spinner state, elapsed-time readout. General Q&A answers in a few seconds; code-generation requests can take ~40-60s — this is expected and shown in the UI copy, not a bug.

**Balthazar now has `magi_llm_api.py` too (added 2026-09-16, see [[magi-failover]])** — but installed as **failover-only**, `magi-llm-api.service` present but disabled/inactive by default, never the dashboard's default chat target. Testing found it cannot coexist with balthazar's actual day-to-day language service, `hailo-ollama`/Open WebUI (crashes hailo-ollama outright under concurrent load) — see [[magi-failover]] for the full finding. The dashboard's chat panel above still always talks to melchior; balthazar's copy only gets started automatically by the new failover watchdog (or a manual override) if melchior goes down.

**Verified end-to-end 2026-09-16**: real (non-mocked) responses confirmed both via direct curl to melchior:8802 and through the full dashboard proxy path at `/api/llm/chat`, for both a general-knowledge question and a code-generation request. Confirmed `magi-live-detect.service` stayed active and detections kept landing in repositorium's Postgres throughout, including during a 39.6s code-gen request — no crash, some throughput sharing expected per Hailo's own docs.

**Update (2026-09-16, same day, follow-up session) — tool-calling + conversation history added.** Full details/verification evidence in [[magi-local-llm-feasibility]]'s Session 6; summary here for the dashboard-specific pieces:

- **Tool calling**: `magi_llm_api.py` on melchior now embeds math + weather tool schemas (from `agent_tools_example`, reused verbatim) into the system prompt and actually executes them when the model emits a `<tool_call>`. Verified through the dashboard: "what's 23 times 17?" → real tool execution → `391`; weather-in-Chicago → real live Open-Meteo data. Hardware tools (LED/servo/elevator) are not imported into this process at all, so there's no path to real GPIO from the dashboard chat. The chat response meta line now shows `tool used: <name>` when applicable, and the `/chat` JSON has `tool_used`/`tool_result` fields.
- **Conversation history**: new session concept — the browser generates a `session_id` (stored in `localStorage`, key `magi_llm_session_id`) and sends it with every `/api/llm/chat` call. History is stored in a new Postgres table (`llm_conversations`) via a new service, **`magi-conversation-api`** on **repositorium** (systemd, port **8803**, `~/magi/dashboard/conversation_api.py`) — melchior has no direct Postgres access itself, so it calls this service over plain HTTP to load/save turns. A **"New conversation" button** next to Send clears the current session's history (`DELETE /api/llm/session/<id>` on the dashboard → melchior → repositorium) and rotates to a fresh session id. Verified end-to-end: told it a name + topic in turn 1, asked "what's my name and what am I working on?" in turn 2 (same session), got the correct answer back — proves history round-tripped through Postgres and was actually used, not just a stateless coincidence.
- **A dashboard-specific bug fixed this session**: `app.py`'s `/api/llm/chat` proxy originally forwarded only `{"prompt": ...}` to melchior and silently dropped `session_id` — history looked broken end-to-end even though melchior's side worked fine when hit directly. Fixed by forwarding `session_id` through the proxy. Worth remembering if this proxy is touched again: fields added to melchior's API don't reach the dashboard (or vice versa) automatically, they're two separate files.
- **Safety note**: a transient live-active under-voltage/throttle bit (`0x50005`, not just the historical `0x50000`) was observed once during this session's testing, self-cleared within seconds. Testing was stopped at that point per the standing power-safety protocol; the plain one-shot Q&A/code-gen path (this section's original feature) was *not* re-verified after this session's code changes as a result — recommend a fresh check once the power situation is more stable.

## Known limitations

1. **Tailnet access not yet live** — see Access section above; blocked on edgerunner being reachable.
2. **Dashboard app itself isn't systemd-managed** — a crash or audire-videre reboot won't auto-restart it; needs a manual `start_dashboard.sh` run (or promote it to a user unit like the streamer, later).
3. **No auth** — fine for LAN/tailnet-only, would need one before any wider exposure.
4. **balthazar detect control still script-based, not systemd, for the main HAT panel** — the original detect-control buttons (`NODES["balthazar"]` in `app.py`) still use the raw `start_detect.sh`/`stop_detect.sh` scripts, unchanged. Note there IS now a separate `magi-live-detect.service` systemd unit on balthazar (added 2026-09-16 for [[magi-failover]]), but it's `disabled`/inactive by default and managed only by the failover watchdog/manual-override path, not by these HAT panel buttons — the two control paths coexist but are intentionally separate; don't assume promoting one reconciles the other. **Caution**: clicking the old HAT panel's "Start" button for balthazar while the failover system has separately started `magi-live-detect.service` (or vice versa) would race two processes for `/dev/hailo0` — not expected in normal operation (failover mode is rare/short-lived) but worth knowing if detect ever behaves oddly on balthazar during/near a failover window.
5. Node health / hailo-device-present checks add a few hundred ms of SSH round-trip per node per poll (~5s interval) — fine at this scale, wouldn't scale to many more nodes without batching.
6. A real bug was hit and fixed during build: an early version checked Hailo detect status with `pgrep -f "Hailo Detection App"` run over SSH — this **self-matches the wrapping SSH command's own argv** (which contains that same literal string), so it always reported "running" regardless of truth. Fixed to use `fuser /dev/hailo0` instead (same technique the existing stop scripts already used, for a related but distinct reason). If extending node-health checks later, be wary of `pgrep -f`/`ps grep` patterns run remotely for the same self-match trap.

## Verification (2026-09-15)

All endpoints returned real data, checked directly: `/`, `/api/nodes`, `/api/data/stats`, `/api/data/recent`, `/api/data/stream-info`, `/api/topology`, `/api/camera/snapshot.jpg` all HTTP 200 from both `127.0.0.1:8090` and the LAN IP `192.168.8.136:8090`. Live data flow confirmed multiple times by polling `/api/data/recent` a few seconds apart and observing the latest event ID advance (e.g. +28 events in 4 seconds during active detection). Detect-control buttons verified round-trip on both melchior (systemctl path) and balthazar (script path): stop → status flips to `stopped` within ~2s, restart → status flips back to `running` with a fresh PID and detections resume within ~10s, confirmed independently via `journalctl`/`ssh` on the node itself, not just the dashboard's own report of success.
