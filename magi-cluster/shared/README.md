# shared/

Scripts deployed byte-identically to both melchior and balthazar (dedup
refactor, 2026-09-16 — previously these were four near-duplicate per-node
copies differing only by hardcoded device-identity constants).

- `magi_publish.py` — Redis publish helper (already shared before this refactor).
- `magi_detect.py` — Hailo YOLO detection publisher (`magi-live-detect.service`).
- `magi_llm_api.py` — local-LLM chat/tool-calling HTTP API (`magi-llm-api.service`).
  No per-node config needed at all — it has no device-specific behavior.
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
  (see [[magi-local-llm-feasibility]]).

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
