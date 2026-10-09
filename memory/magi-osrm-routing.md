---
name: magi-osrm-routing
description: "Self-hosted OSRM (OpenStreetMap routing engine) on repositorium, wired into the magi-cluster's local-LLM tool-calling system as a 'directions' tool alongside math/weather — built, verified end-to-end, and live"
metadata:
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-18T21:18:35.171Z
---

Built 2026-09-17 in response to [[magi-maps-tool-goal]]'s "self-hosted OSRM" option (ranked #1 fit for the project's local-first ethos). **Status: DONE. OSRM is running and verified with real routes, and a `directions` tool is live in the LLM tool-calling pipeline on balthazar (melchior's copy is deployed too but that node currently runs detection, not the LLM API — see "Node topology" below).** Session included a ~2 hour mid-task outage where repositorium and melchior both dropped off the network for unrelated reasons (recovered on their own before this doc was finalized — see "Outage note" at the bottom).

## OSRM itself — running on repositorium (`192.168.8.184`)

- **Docker installed** via plain `apt-get install -y docker.io`. `sinewave` added to the `docker` group. Docker enabled+running.
- **Region: Illinois** (Geofabrik `north-america/us/illinois-latest.osm.pbf`, ~343MB) — covers the Chicago-area references from prior sessions. Downloaded to `/home/sinewave/512_VideoStick/osrm/illinois-latest.osm.pbf`. **Gotcha:** Geofabrik's "latest" URL 302-redirects to a dated filename — must use `curl -L`, plain `curl -O` silently saves the tiny redirect-page HTML instead of the real file.
- **Disk:** 436GB free on the NVMe before starting; extract + processed `.osrm*` files total ~2GB, trivial fraction.
- **Docker image gotcha:** the official `osrm/osrm-backend` Docker Hub image is **amd64-only** (every tag checked via Docker Hub's API) and repositorium is ARM64 (Pi5) — fails with `exec format error`. Used **`kradenko/osrm-backend:arm64`** instead, a community multi-arch build (real trust anchor is the end-to-end route verification below, not the image's provenance — it held up).
- **`osrm-extract`** (car profile, bundled `car.lua`): ~2m18s, peak 3.1GB RAM. **`osrm-contract`** (classic CH, not MLD): ~38 minutes, peak 2.3GB RAM, clean exit. Both logs showed normal, sane OSM processing throughout.
- **Serving:** `osrm-routed --algorithm ch` (must match `ch`, since contract not partition/customize was used) as a Docker container managed by systemd, following the `magi-worker`-style convention:

  `/etc/systemd/system/osrm-backend.service`:
  ```ini
  [Unit]
  Description=Magi cluster OSRM routing engine (Illinois car routing)
  After=network.target docker.service
  Requires=docker.service

  [Service]
  Type=simple
  User=sinewave
  ExecStartPre=-/usr/bin/docker rm -f osrm-backend
  ExecStart=/usr/bin/docker run --rm --name osrm-backend -p 192.168.8.184:5000:5000 -v /home/sinewave/512_VideoStick/osrm:/data kradenko/osrm-backend:arm64 osrm-routed --algorithm ch /data/illinois-latest.osrm
  ExecStop=/usr/bin/docker stop osrm-backend
  Restart=on-failure
  RestartSec=5

  [Install]
  WantedBy=multi-user.target
  ```
  Enabled + running (`sudo systemctl enable --now osrm-backend`). **Important:** the `-p 192.168.8.184:5000:5000` binds to the LAN IP specifically, NOT `127.0.0.1` — anything calling OSRM from the same box (e.g. the tools API, see below) must use `192.168.8.184:5000`, not `localhost:5000`, or it gets connection-refused. Cost about an hour of debugging in this exact spot, worth remembering.

- **Verified with real queries**, both via raw OSRM and through the full tool: a raw coordinate query for Willis Tower → O'Hare returned `code: "Ok"`, distance 31.78km (~19.75mi), duration 2592s (~43.2min), snapped correctly to real streets ("South Dearborn Street") — geographically sane for that pair. The geocoded, address-resolved version (via the `directions` tool, see below) returned 22.0mi / 35.4km, ~56min with sensible turn-by-turn ("turn right onto South Lower Wacker Service Drive..."), also sane (differs slightly from the raw-coordinate test because Nominatim resolved to the actual building/terminal entrances, not my hand-picked approximate coordinates).

## `directions` LLM tool — wired in, verified end-to-end 2026-09-17

The concurrent `magi_llm_api.py` edit mentioned as a blocker earlier in this session had, by the time OSRM was ready to wire in, already landed as a completed architecture change: **`magi-tools-api.service`** on repositorium (port 8804, `~/magi/dashboard/tools_api.py`) is now the single source of truth for tool *logic* (math, weather, and now directions) — melchior/balthazar's `magi_llm_api.py` only imports the vendored `tools/{math,weather,directions}` modules for their `name`/`description`/`TOOLS_SCHEMA` metadata (what's shown to the model), and calls the tool via a thin HTTP POST to `tools_api.py` for actual execution. This tool followed that exact existing pattern rather than the older one described in [[magi-maps-tool-goal]] (each node running its own vendored `run()` locally) — the centralization happened between sessions and is the right pattern to build against now.

**What was added:**
1. `tools/directions/tool.py` + `__init__.py`, vendored identically onto both melchior and balthazar's `~/hailo-apps` checkouts (`hailo_apps/python/gen_ai_apps/agent_tools_example/tools/directions/`) — provides `name`, `description`, `schema`, `TOOLS_SCHEMA`, and a real standalone `run()` (geocodes via Nominatim, calls OSRM at `192.168.8.184:5000` directly) for parity with math/weather's structure, even though `magi_llm_api.py` never calls this `run()` in practice.
2. A `run_directions()` + `POST /tools/directions` endpoint added to repositorium's `tools_api.py` (the actual execution path used in production) — same geocode-then-route logic, calling OSRM at `192.168.8.184:5000` (not `127.0.0.1`, see the port-binding gotcha above).
3. `magi_llm_api.py` on both melchior and balthazar (byte-identical files, confirmed via diff before editing) updated to import `directions_tool` and register it in the `tools` list passed to `create_system_prompt()`, alongside math/weather, with `_remote_tool_runner("directions")` as its runner.

**Regression hit and fixed during this session:** the directions tool's first draft of `description`/`schema` text was too verbose — adding it to the system prompt pushed total context to 2236/2048 tokens, **over the model's hard context limit**, which caused every tool (not just directions) to degenerate to near-empty `"<"` responses (`[HailoRT] Context exceeds capacity` in the logs). Trimmed the description down to ~5 sentences (matching the more concise style `math`'s tool uses rather than weather's more verbose one) and the schema's per-field descriptions to one line each. Confirmed no recurrence: `sudo journalctl -u magi-llm-api` on balthazar showed zero "Context exceeds capacity" lines after the trim, across 3 follow-up test requests. **Lesson for future tool additions here: budget system-prompt tokens carefully — this model's context is small (2048) and every registered tool's full description/schema counts against it for every single request, tool-relevant or not.**

**Verified live, full pipeline** (`POST /chat` on balthazar, `192.168.8.133:8802`, which is this cluster's currently-active LLM-API node — see "Node topology" below):
```
curl -X POST http://192.168.8.133:8802/chat -H 'Content-Type: application/json' \
  -d '{"prompt": "Give me directions from Willis Tower in Chicago to OHare International Airport.", "session_id": "..."}'
```
Response: `tool_used: "directions"`, `tool_result.ok: true`, real result text — *"Driving from Willis Tower, 233, South Wacker Drive... to O'Hare International Airport... 22.0 mi (35.4 km), about 56 min. Key turns: turn right onto South Lower Wacker Service Drive; turn left onto West Lower Adams Street; turn left onto South Lower Wacker Drive..."* — the model correctly selected the tool, geocoding + OSRM routing both worked, and turn-by-turn came through. Also re-verified math (`47 * 89 = 4183`, correct) and weather (real Chicago conditions) still dispatch correctly post-fix — no regression from adding the third tool.

## Node topology (as of this session)

melchior currently runs `magi-live-detect.service` (Hailo chip busy with YOLO detection) and has **no `magi-llm-api` systemd service active** — its `magi_llm_api.py` file was updated with the same directions-tool registration for consistency/parity, but isn't the node actually serving chat right now. balthazar runs `magi-llm-api.service` live and is what this session's verification queries hit. A `magi-failover.service`/`magi-heartbeat.service` pair exists on melchior (see [[magi-failover]] if it exists, or magi-cluster-status.md) suggesting some active/standby relationship between the two for LLM duty — not investigated further this session, just noted so whoever picks this up doesn't assume melchior is idle-broken.

## API reference

- Raw OSRM: `http://192.168.8.184:5000/route/v1/driving/{lon1},{lat1};{lon2},{lat2}` (longitude first). Params: `overview=full/simplified`, `steps=true`, `alternatives=true`.
- Central tools API: `http://192.168.8.184:8804/tools/directions` — `POST {"origin": "...", "destination": "..."}` → `{"ok": true, "result": "human-readable summary"}` or `{"ok": false, "error": "..."}`. Also `/tools/math`, `/tools/weather`, `/health`.
- LLM chat (balthazar): `http://192.168.8.133:8802/chat` — `POST {"prompt": "...", "session_id": "..."}`.
- Coverage: Illinois only (car profile). A query touching a place outside Illinois returns a clear `NoRoute`/error rather than nonsense — both OSRM and the `directions` tool surface this as a real error, not a silent wrong answer.

## Two dashboard features built on top of this (2026-09-17) — see [[magi-dashboard]] for full detail

1. **"Open in Maps" deep-links on `directions` tool responses** — `run_directions()` in `tools_api.py` now also returns `apple_maps_url`/`google_maps_url` (public URL schemes, no API key), built from the same Nominatim-geocoded coordinates already used for the OSRM call. Rendered as buttons in the dashboard chat thread under a `directions` tool response.
2. **Live Navigation panel** — a new, separate (non-chat, non-LLM-orchestrated) dashboard UI using the browser's `navigator.geolocation` (opt-in only) to periodically call a new `POST /nav/route` endpoint (same `tools_api.py`/port 8804 process) for live ETA/distance/next-turn text, re-routing automatically on every poll since each call is computed fresh from the client's current position. **Traffic-data extension point**: all of this routing goes through one function, `compute_route()` in `tools_api.py` — that's where a future commercial traffic-aware provider (a separate step Gil will do himself once he has an account) would get swapped in, without touching the endpoint or frontend. No traffic-provider code exists yet, on purpose — this instance remains static-road-network-only (no traffic data at all, per the "Coverage" note above).

## Updating the map data later

Re-download the same Geofabrik URL (continuously updated upstream, no account/key needed), re-run `osrm-extract` then `osrm-contract` against the new file (can build under a different filename, then swap + `sudo systemctl restart osrm-backend` to avoid downtime).

## Widening coverage beyond Illinois

If Gil ever wants routes crossing state lines, redo the extract with a bigger Geofabrik region (`north-america/us-midwest-latest.osm.pbf` or `north-america/us-latest.osm.pbf`) — same pipeline, bigger/slower extract+contract (the whole US would take meaningfully longer than 38 minutes on this hardware) and proportionally more disk (still trivial against 436GB free).

## `find_nearby` LLM tool — "find me directions to the nearest X" (added 2026-09-18)

Built on top of everything above, per [[magi-maps-tool-goal]]'s reuse-the-existing-directions-tool
framing. A 4th tool, alongside math/weather/directions, following the exact same centralized
architecture: real execution lives on repositorium (`run_find_nearby()` / `POST /tools/find_nearby`
in `~/magi/dashboard/tools_api.py`), both nodes' `magi_llm_api.py` only import a vendored copy
(`~/hailo-apps/.../agent_tools_example/tools/find_nearby/`) for tool schema/description metadata.

**What it does**: takes a free-text `query` (brand or category — "Best Buy", "coffee", "pharmacy")
plus the searcher's `lat`/`lon`, searches OpenStreetMap via the public **Overpass API**
(`https://overpass-api.de/api/interpreter`, no key/account needed) for nodes whose `name` or `brand`
tag matches within 15km (one-time 45km retry if nothing found), computes real haversine distance to
each match, picks the nearest, then reuses `directions`' own OSRM-routing + Apple/Google-Maps-link
logic (factored out of `run_directions()` into a new shared `_drive_route_result()` helper in
`tools_api.py`, so both tools call the identical routing code) to route to it. Radius is fixed
server-side, not something the model has to specify.

**Overpass query syntax, verified by hand via curl before writing any code** (the public instance's
real behavior didn't fully match assumptions from documentation alone): `["name"~"query",i]` needs
the tag name **quoted** — the unquoted `[name~"query",i]` form silently matched zero results in
testing, while `["name"~"query",i]`/`["brand"~"query",i]` worked correctly. Real single-clause node
queries against a 15km-radius Chicago point took ~15-25s; adding a third broad tag-value clause
(tried for wider category matching, e.g. `amenity`/`shop` regex) measurably increased the odds of a
full timeout, and a `way` clause (for POIs mapped as ways, not nodes) also slowed the very first test
query down. Final query stays to exactly two OR'd node-only clauses (`name~query` OR `brand~query`)
for reliability — handles brand queries well ("Best Buy" → 4 real matches near downtown Chicago,
nearest 0.97mi/1.57km away) and category queries reasonably when the term appears in the place's OSM
name (e.g. "pharmacy" matches "CVS Pharmacy"/"Walgreens Pharmacy") but won't find, say, a "coffee"
search matching a place literally named just "Starbucks" — a real, documented scope limit, not a bug.
**The public instance genuinely rate-limits/times out under real load** (hit several `504 "server
too busy"` responses and internal-timeout `remark` fields during this session's own testing, likely
partly self-inflicted from rapid iterative curl testing) — `run_find_nearby()` treats both as a clean
`{"ok": false, "error": "..."}` telling the user to try again in a moment, never a crash or a silent
wrong answer; this was observed live during real verification, not just written defensively.

**Location handling — the reliability-critical design decision.** The tool's schema shown to the
model asks for `query` only — lat/lon are deliberately **not** something the model is ever asked to
supply, learned directly from [[magi-local-llm-feasibility]] Session 10's weather-tool
location-hallucination bug (never trust a small on-device model to correctly produce structured
location data it wasn't explicitly given — it will invent a plausible-looking one). Instead: the
dashboard frontend does a **one-time** `navigator.geolocation.getCurrentPosition()` read (a new
`getOneShotLocation()` helper in `static/index.html`, 4s timeout, resolves to `null` on any
failure/denial) when the user sends a chat message — deliberately **not** `watchPosition()`, which
the separate Live Navigation panel already uses for continuous tracking; this is a distinct, simpler
"where am I right now, for this one message" read. If successful, `lat`/`lon` ride along as
top-level fields on `POST /api/llm/chat`, forwarded as-is by `app.py`'s proxy to whichever node
handles the request. `magi_llm_api.py`'s `chat()` stores them in `_state["request_geo"]` (reset to
`None` in the request's `finally` block, so a request without location can never reuse a stale
previous one) — a dedicated `_find_nearby_runner()` (not the generic `_remote_tool_runner()` factory
the other three tools use) reads that at call time and **overwrites** whatever the model put in its
own tool-call arguments with the real coordinates before calling `tools_api.py`. Safe as shared
mutable state only because the service's existing per-node request lock already serializes to one
`/chat` in flight at a time — flagged inline in the code for anyone who later relaxes that. If no
location was captured this turn (denied, unsupported, or any future caller — e.g. a Telegram bot —
that doesn't send one), the runner returns "I don't have your location — try enabling location
access, or tell me a location to search near." without ever calling out to `tools_api.py` at all —
same ask-don't-guess spirit as the weather fix, never a silent wrong-location answer.

**Reusable-by-a-future-Telegram-bot requirement, satisfied by construction**: `find_nearby` lives
entirely as a clean HTTP endpoint on `tools_api.py` (`POST /tools/find_nearby {"query", "lat",
"lon"}` → same `{"ok", "result", ...}` shape every other tool uses) — it has no dashboard-specific
code path and no dependency on the browser at all. The location-injection responsibility lives
entirely in `magi_llm_api.py`'s runner (reads whatever `lat`/`lon` arrived on the `/chat` request
body, from *any* caller), not in `tools_api.py` itself — a future Telegram bot supplying its own
location (e.g. from a Telegram location-share message) through the same `/chat` `lat`/`lon` fields
would get identical behavior with zero changes to `tools_api.py` or the tool itself.

**Token-budget regression hit and fixed, same session** — adding this 4th tool's schema pushed the
small on-device model's system prompt to 2141/2048 tokens, over its hard context limit (the exact
failure class Session 12 hit when `directions` became the 3rd tool — `[HailoRT] Context exceeds
capacity`, which degrades ALL tools' responses, not just the newest one). Trimmed `find_nearby`'s
own description/schema to the bare minimum (~140-char description, terser than `math`'s let alone
`weather`'s) — still 20-37 tokens over, because the budget was already maxed out by the 3 *existing*
tools before this one was even added (confirmed by dumping the actual rendered system prompt: 8120
chars / ~2030 tokens with only math+weather+directions registered). Root-fixed by trimming `math`'s
own vendored description (the single largest at 1359 chars) — it had a `TOOL CALL FORMAT`/`Examples`
block that fully duplicated content `system_prompt.create_system_prompt()` already states once,
generically, for every registered tool. Cut to 866 chars with no loss of any instruction the model
doesn't already see elsewhere (both live `~/hailo-apps/.../tools/math/tool.py` on each node and a
new `shared/math_tool/tool.py` reference copy in the repo, mirroring the existing `weather_tool/`
pattern). Confirmed fixed: zero "Context exceeds capacity" log lines across repeated real `/chat`
calls (math/weather/directions/find_nearby) on both nodes after the trim, versus reproducing the
exact error twice (2141/2048, then 2085/2048) before it.

**Verified end-to-end, real (non-mocked) results, melchior first then balthazar then the live
dashboard, per standing protocol:**
1. Melchior direct `/chat` (temporarily started, its normal steady state is
   disabled/failover-only): "find me directions to the nearest Best Buy" with
   `lat=41.8781, lon=-87.6298` (downtown Chicago) → real Overpass search found 4 real Best Buy
   locations; nearest picked (0.97mi/1.57km); real OSRM route — *"Nearest Best Buy match: Best Buy.
   Driving from your current location to Best Buy: 1.4 mi (2.3 km), about 4 min. Key turns: turn
   right onto West Van Buren Street; turn left onto South Clark Street; continue slight right onto
   South Clark Street..."* — plus real, correctly-coordinated Apple/Google Maps deep links. Restored
   to its normal stopped/idle state afterward, `vcgencmd get_throttled` clean (`0x50000`, historical
   only) throughout.
2. Same query, same coordinates, on **balthazar direct `/chat`** (the live default node) — identical
   real result.
3. No-location path verified separately on melchior: same phrasing with no `lat`/`lon` on the
   request → *"I don't have your location — try enabling location access, or tell me a location to
   search near."* — exact match to the intended wording, no hallucinated location, no crash.
4. **Through the actual live dashboard** (`POST http://127.0.0.1:8090/api/llm/chat`, simulating
   exactly what the frontend sends once `getOneShotLocation()` succeeds): "find me directions to the
   nearest Best Buy" with `lat`/`lon` in the body → `served_by: "balthazar"`, identical real result,
   confirming the full chain (browser payload shape → dashboard proxy → node → tools_api → Overpass
   → OSRM → back) works, not just the node-direct shortcut.
5. Regression checks, all passing post-deploy on both nodes and through the dashboard: plain no-tool
   chat ("describe the color blue" → correct, no tool), `weather` (real Chicago/Seattle data, still
   correct after `find_nearby`'s addition), `directions` (Willis Tower → O'Hare, still correct,
   including the new `distance_mi`/`duration_str` fields that came along for free when
   `_drive_route_result()` was factored out for reuse). The separate Live Navigation panel's
   `POST /api/nav/route` (same `tools_api.py` process) re-tested directly — unaffected, still returns
   correct live ETA/distance/geometry.
6. Frontend JS sanity-checked in headless Chromium (`--headless=new --dump-dom` and a separate
   `--enable-logging` pass): `getOneShotLocation`/`chatBody.lat`/`chatBody.lon` all present in the
   served script, zero console errors, full DOM renders (an actual `getCurrentPosition()` permission
   grant can't be exercised headlessly, same limitation noted for the Live Navigation panel's own
   verification in [[magi-dashboard]]).

## Outage note (context for anyone confused by session gaps)

Mid-session, right after `osrm-contract` finished successfully, repositorium and melchior both became unreachable (ping/SSH) for roughly 2 hours while balthazar/gateway/switch stayed up — confirmed by the coordinator afterward to be a real outage (included a router reboot partway through that did NOT bring the two nodes back on its own — a separate recovery happened on them). Not something this session's commands caused (the heavy job had already exited 0 before the disconnect) and not something fixable remotely — work paused, then resumed cleanly once connectivity returned, with all on-disk state intact.
