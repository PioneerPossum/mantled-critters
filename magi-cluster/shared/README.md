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
