# magi-cluster

Code for Gil's home AI processing cluster ("magi-cluster") — on-prem inference/reasoning
infrastructure supporting the broader Protogen Fursuit HUD project (melchior/balthazar/D555
are the fursuit's on-board AI vision components). See the root [`memory/magi-cluster-status.md`](../memory/magi-cluster-status.md)
(plus its siblings `magi-dashboard.md`, `magi-failover.md`, `magi-github-sync.md`) for full
build history, architecture decisions, and verification evidence — this README is just a map.

## Layout (mirrors physical topology)

Each top-level directory here is one physical/logical node:

- **`melchior/`** — Raspberry Pi 5 + Hailo-10H AI HAT. Runs real-time YOLO object detection
  (`magi_detect.py`) and a local LLM chat API (`magi_llm_api.py`, Qwen2.5-Coder-1.5B-Instruct
  on-device), plus heartbeat/failover daemons. melchior runs both vision and language as
  permanent duty. (Not included: `agent.py`, an earlier ZMQ-based scaffolding script that
  predates this architecture and isn't part of the current running system — kept out of the
  publish for now, but worth knowing it existed if this history ever matters.)
- **`balthazar/`** — a second Raspberry Pi 5 + Hailo-10H AI HAT, same base capability as
  melchior. Its everyday job is `hailo-ollama`/Open WebUI (`ollama_shim.py` patches around a
  strict-parser incompatibility there); `magi_detect.py`/`magi_llm_api.py` are installed but
  only started by the failover watchdog when melchior goes down (see `magi-failover.md`).
- **`repositorium/`** — the x86 storage/memory backend (Postgres + pgvector, Redis). Runs
  `magi_worker.py` (persists the detection stream into Postgres) and, under `dashboard/`, the
  two small read-only/read-write HTTP APIs the dashboard depends on for data
  (`data_api.py`) and LLM conversation history (`conversation_api.py`).
- **`dashboard/`** — the operator-facing pieces hosted on **audire-videre** (the x86 host this
  repo lives on): the Flask dashboard app (`app.py` + `static/index.html`) that ties the whole
  cluster's status/controls together, and the D555 camera bridge (`magi_stream_d555.py` +
  start/stop scripts) that streams the RealSense D555's color feed to melchior over RTP/H264
  for live inference — grouped here rather than a separate `audire-videre/` directory since
  both are audire-videre-hosted services feeding the same dashboard experience.
- **`shared/`** — `magi_publish.py`, the Redis-stream publish helper used identically by both
  melchior and balthazar (diffed byte-for-byte identical before being placed here).

All credentials (Redis/Postgres passwords, etc.) are loaded at runtime from
`~/.config/magi/*.env` files on each node — never hardcoded in this source.
