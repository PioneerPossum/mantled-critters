# shared/

Scripts deployed byte-identically to both melchior and balthazar (dedup
refactor, 2026-09-16 — previously these were four near-duplicate per-node
copies differing only by hardcoded device-identity constants).

- `magi_publish.py` — Redis publish helper (already shared before this refactor).
- `magi_detect.py` — Hailo YOLO detection publisher (`magi-live-detect.service`).
- `magi_llm_api.py` — local-LLM chat/tool-calling HTTP API (`magi-llm-api.service`).
  No per-node config needed at all — it has no device-specific behavior.
  **2026-09-17: tool execution moved off-node.** `math`/`weather` (and, as of
  the same day, `directions` — see below) tool *execution* now happens via a
  thin HTTP call (`_remote_tool_runner()`) to a new central tool-execution
  API on repositorium (`magi-tools-api.service`, port 8804,
  `repositorium/dashboard/tools_api.py` in this repo) instead of running each
  vendored tool's own local `run()` function in-process. Reason: melchior and
  balthazar each have their own separate `~/hailo-apps` checkout, and the
  weather tool specifically had already needed two in-place hotfixes there
  (the location-hallucination fix and the `include_rain` fix documented
  below) that lived outside this repo and would be silently lost on a future
  `git pull`/reinstall of either node's `hailo-apps` checkout — the same
  drift risk this repo's `shared/` directory itself exists to close for
  `magi_detect.py`/etc. Tool *schemas* (name/description/parameters shown to
  the model in the system prompt) are unchanged and still sourced from the
  vendored `tools/{math,weather,directions}` modules on each node — only
  what a tool call actually *does* moved to repositorium. `TOOLS_API =
  "http://192.168.8.184:8804"` is hardcoded the same way `CONV_API` already
  was (no per-node config needed, repositorium's address doesn't vary by
  which node is calling it).
- `magi_heartbeat.py` — failover liveness beacon (`magi-heartbeat.service`).
- `magi_failover.py` — failover watchdog + manual `ctl start|stop <role>` override
  (`magi-failover.service`).
- `weather_tool/tool.py`, `weather_tool/api.py` — mirror of the weather tool
  `magi_llm_api.py` wires in (`hailo_apps.python.gen_ai_apps.agent_tools_example
  .tools.weather`, vendored upstream in `~/hailo-apps` on each node, NOT part
  of this repo's own import path — this is a synced reference copy, not the
  live code path). Live originals: `~/hailo-apps/hailo_apps/python/gen_ai_apps
  /agent_tools_example/tools/weather/{tool,api}.py` on melchior and balthazar
  (identical on both). 2026-09-17 fix: the tool used to expose a
  model-controlled `include_rain` boolean; the model called it for the same
  location ~18 minutes apart, once with `include_rain: True` (got precipitation)
  and once with `include_rain: False` (precipitation silently omitted, no
  error) — read as the model contradicting itself about rain when really the
  tool just wasn't asked for it. Fix: `include_rain` removed entirely from the
  schema/validation; `get_weather_forecast()` now always fetches and returns
  precipitation (amount + probability) on every successful call, and result
  text now separates "Current conditions" from "Today's forecast" /
  "Forecast for ... on <date>" so two different-timeframe answers read back
  to back can't be misread as one contradictory "now" statement. Verified via
  real `/chat` calls on both nodes and via the dashboard's `/api/llm/chat`
  (see [[magi-local-llm-feasibility]]). **This logic's live execution copy
  has since moved to `repositorium/dashboard/tools_api.py`** (2026-09-17,
  same session as the tool-execution-centralization change above) — this
  `weather_tool/` directory is now a second-order reference copy (the
  vendored `~/hailo-apps/.../tools/weather/{tool,api}.py` on each node is
  still the source for the tool's *schema*; `tools_api.py` is the source for
  its *execution*; this directory exists so the logic isn't only trackable
  in one non-obvious place). Verified after the move: two differently-worded
  weather queries for the same location still return identical precipitation
  data both ways, through both nodes' `/chat` and through the dashboard.

  **A third tool, `directions` (OSRM-based routing), was added to
  `magi_llm_api.py`/`tools_api.py` by a separate, concurrent session on
  2026-09-17** — see `magi-osrm-routing.md` for that work's own detail; not
  authored or verified by the tool-execution-centralization session, noted
  here only because it now lives in the same shared files. One real
  regression was observed as a side effect: with all three tools' schemas in
  the system prompt, the small on-device model became noticeably more prone
  to misidentifying plain math questions as `weather` tool calls (reproduced
  3/3 times across both nodes, different phrasings) — this didn't happen
  with only math+weather registered. Not fixed by this session (out of
  scope — the regression is in tool-selection prompting, triggered by
  someone else's concurrent, unrelated change, not by the execution-backend
  migration itself), but flagged here for whoever picks up `directions` or
  revisits tool-selection reliability next.

- **A fourth tool, `find_nearby`, added 2026-09-18** — "find me directions to
  the nearest Best Buy" as one tool call: searches OpenStreetMap via the
  public Overpass API (`https://overpass-api.de/api/interpreter`, no API key)
  for places whose `name` or `brand` tag matches a free-text query within
  15km of a point (one-time 45km retry if nothing's found), picks the
  nearest by real haversine distance, then reuses `directions`' own OSRM
  routing + Apple/Google Maps deep-link logic (factored out into a shared
  `_drive_route_result()` helper in `tools_api.py`) to route there. Live
  execution: `run_find_nearby()` / `POST /tools/find_nearby` in
  `repositorium/dashboard/tools_api.py`. Vendored schema/metadata-only copy:
  `shared/find_nearby_tool/{tool.py,__init__.py}`, live path
  `~/hailo-apps/.../agent_tools_example/tools/find_nearby/` on both nodes
  (also has a real, working standalone `run()` for structural parity with
  the other tools, same as `directions`, even though `magi_llm_api.py` never
  calls it — the actual execution path is `tools_api.py`).

  **Location handling — the reliability-critical part.** The tool's schema
  shown to the model asks for `query` only; lat/lon are deliberately NOT
  something the model is asked to supply (learned from
  [[magi-local-llm-feasibility]] Session 10's weather-tool
  location-hallucination bug — never trust a small model to correctly
  produce structured location data it wasn't explicitly given). Instead: the
  dashboard frontend (`dashboard/static/index.html`) does a one-time
  `navigator.geolocation.getCurrentPosition()` call in `submitChat()` when
  the user sends a message (a new `getOneShotLocation()` helper, 4s timeout,
  resolves to `null` on any failure) — deliberately NOT `watchPosition()`,
  which the separate Live Navigation panel already uses for continuous
  tracking; this is a single "where am I right now, for this message" read.
  If successful, `lat`/`lon` ride along as top-level fields on the
  `POST /api/llm/chat` body, alongside `prompt`/`session_id`.
  `dashboard/app.py`'s proxy forwards them as-is to whichever node handles
  the request. In `magi_llm_api.py`, `chat()` reads `lat`/`lon` off the
  request body into `_state["request_geo"]` (reset to `None` at the end of
  every request in `finally`, so a request without location can never reuse
  a previous one's) — a new `_find_nearby_runner()` (NOT built via the
  generic `_remote_tool_runner()` factory the other three tools use) reads
  that at call time and merges the *real* coordinates into the args sent to
  `tools_api.py`, overwriting anything the model itself put there. This is
  safe as shared mutable state (rather than genuinely per-request-scoped) only
  because the existing `_lock` already serializes the whole service to one
  `/chat` request in flight at a time — noted inline in the code as a
  constraint for anyone who later relaxes that serialization. If the
  frontend never captured a location this turn (denied, unsupported, or a
  caller that doesn't send it at all — e.g. a future Telegram bot with its
  own location source, or none), the runner returns "I don't have your
  location — try enabling location access, or tell me a location to search
  near." without ever calling out to `tools_api.py` — same "ask, don't
  guess" spirit as the weather-tool fix, and the exact reason `find_nearby`
  is a clean, reusable HTTP endpoint on `tools_api.py` rather than tangled
  into dashboard-specific code: any future caller (this dashboard, or a
  Telegram bot with its own location source) gets the same tool, the
  location-injection responsibility lives entirely in `magi_llm_api.py`'s
  runner, not in `tools_api.py` itself.

  **Token-budget regression hit and fixed, same session** — adding a 4th
  tool's schema pushed the system prompt to 2141/2048 tokens, over the
  model's hard context limit (the exact failure class Session 12 hit when
  `directions` became the 3rd tool: `[HailoRT] Context exceeds capacity`,
  which degrades ALL tools' responses, not just the new one). Trimmed
  `find_nearby`'s own description/schema to the bare minimum (a 2-sentence,
  ~140-char description, terser than `math`'s let alone `weather`'s) — still
  20-37 tokens over. Root cause: the budget was already maxed out by the 3
  existing tools before `find_nearby` was even added (confirmed by dumping
  the actual rendered system prompt — 8120 chars / ~2030 tokens with 3
  tools). Fixed by trimming `math`'s own vendored description (the largest,
  1359 chars), removing a redundant `TOOL CALL FORMAT`/`Examples` block that
  fully duplicated content `system_prompt.create_system_prompt()` already
  states once, generically, for every registered tool — cut to 866 chars, no
  loss of any instruction the model doesn't already see elsewhere. Confirmed
  fixed: zero "Context exceeds capacity" log lines across repeated real
  `/chat` calls (math/weather/directions/find_nearby) on both nodes after
  the trim, versus reproducing the exact error twice before it. Synced
  reference copy: `shared/math_tool/tool.py` (mirrors the `weather_tool/`
  pattern above — live path is still
  `~/hailo-apps/.../agent_tools_example/tools/math/tool.py` on each node,
  not this repo's own import path).

  **Verified end-to-end, both nodes then the live dashboard, real (non-mocked)
  requests**: melchior direct `/chat` — "find me directions to the nearest
  Best Buy" with `lat=41.8781, lon=-87.6298` (downtown Chicago) → real
  Overpass search found 4 real Best Buy locations, nearest picked (0.97mi),
  real OSRM route ("1.4 mi (2.3 km), about 4 min", real turn-by-turn), real
  Apple/Google Maps links. Same query repeated on balthazar (the live
  default node) direct `/chat` — identical real result. No-location path
  verified separately: same phrasing with no `lat`/`lon` on the request →
  "I don't have your location — try enabling location access, or tell me a
  location to search near." (exact match to the task's suggested wording).
  Full path verified last: `POST http://127.0.0.1:8090/api/llm/chat` (the
  dashboard's own proxy, simulating exactly what the frontend sends once
  geolocation succeeds) → `served_by: "balthazar"`, same real Best Buy
  result. Regression checks: plain no-tool chat, `weather`, and `directions`
  (including its new `distance_mi`/`duration_str` fields, added when
  `_drive_route_result()` was factored out for reuse) all still work
  correctly post-deploy on both nodes and through the dashboard; the
  separate Live Navigation panel's `/api/nav/route` (same `tools_api.py`
  process) re-tested and unaffected. Power safety followed throughout —
  `vcgencmd get_throttled` checked before/after every heavy step on both
  nodes, only historical `0x50000` bits seen, no live-active throttling.
  **Overpass reliability note**: the public instance genuinely throttles/
  times out under real-world load — hit several `504`/timeout responses
  during development (both from this project's own repeated test traffic
  and, separately, from the service just being slow) — `run_find_nearby()`
  surfaces these as a clear, honest tool error ("Place search service is
  currently overloaded... try again in a moment") rather than crashing or
  hanging, and this was itself observed live during testing, not just
  written defensively.

## Per-node identity/config

`magi_detect.py`, `magi_heartbeat.py`, and `magi_failover.py` all read their
device identity from `~/.config/magi/publish.env` on each node (same
file/`load_env()` pattern `magi_publish.py` already used for Redis
credentials — extended rather than replaced with a new mechanism). Real
contents are private (not committed — this file lives outside git on each
node), but the shape is:

**melchior** (`~/.config/magi/publish.env`):
```
REDIS_HOST=...
REDIS_PASSWORD=...
DEVICE=melchior
PEER=balthazar
OWN_DUTY_ROLES=vision,language
COUPLED_SERVICE=
```

**balthazar** (`~/.config/magi/publish.env`):
```
REDIS_HOST=...
REDIS_PASSWORD=...
DEVICE=balthazar
PEER=melchior
OWN_DUTY_ROLES=
COUPLED_SERVICE=hailo-ollama
```

- `DEVICE`/`PEER` — this node's magi-cluster name and its failover peer's
  (real OS hostnames don't match: balthazar's is actually `dinky-pai`).
- `OWN_DUTY_ROLES` — comma-separated roles (`vision`, `language`) this node
  *always* runs as normal duty; the failover watchdog never stops these,
  only self-heals/monitors. Empty on balthazar (it owns neither role
  normally — see [[magi-failover]]).
- `COUPLED_SERVICE` — a local systemd unit that must be stopped before this
  node can start any failover role, and restarted once none are active
  (balthazar's `hailo-ollama`/Open WebUI backend, which cannot share the
  Hailo-10H's VDevice with `hailo_apps`-based tools — see
  [[magi-local-llm-feasibility]] Session 8 / [[magi-failover]]). Empty on
  melchior (no such conflict there).

Deploy the same script file verbatim to both nodes; only this config file's
contents differ.

## Not templatized (deliberately out of scope)

`melchior/start_live_detect.sh`, `melchior/stop_live_detect.sh`,
`balthazar/start_detect.sh`, `balthazar/stop_detect.sh` — simple per-node
control wrapper scripts, not diffed/merged this round. `balthazar/ollama_shim.py`
is balthazar-specific (Open WebUI/hailo-ollama backend glue) and was
explicitly out of scope for this refactor.
