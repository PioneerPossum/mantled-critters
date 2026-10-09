---
name: magi-router
description: magi-router on audire-videre (:8810) — local-first OpenAI-compatible LLM router with Anthropic API fallback; built + verified 2026-09-27 (step 1 of the worker plan)
metadata:
  node_type: memory
  type: project
  originSessionId: 7c56e99d-afba-524d-bb78-a1dfbc1c104d
  modified: 2026-09-28T20:35:18.239Z
---

Built 2026-09-27 as step 1 of [[magi-remote-worker-plan]]. `~/magi_router/magi_router.py` (own venv `~/magi_router/venv`: flask, requests, anthropic 1.8.0), **user** systemd unit `magi-router.service` (no sudo on audire-videre; Linger=yes so it survives logout). Port 8810, bound to 0.0.0.0.

- **Auth:** bearer token auto-generated at `~/.config/magi-router/token` (0600). Everything except `/health` needs it.
- **Endpoints:** `POST /v1/chat/completions` (OpenAI shape; `stream:true` is faked as one SSE chunk), `GET /v1/models`, `POST /router/announce` (opportunistic gpu workers, 60s TTL, `available:false` removes), `GET /router/status|history|stats`.
- **Models:** `magi-auto` (default), `magi-local` (never cloud), `magi-cloud`, or a backend name.
- **Tiers:** npu (balthazar .133:8802 default; melchior .175 only up during failover, so the health check skips it on its own) → gpu (announced workers) → cloud-light `claude-haiku-4-5` → cloud-heavy `claude-sonnet-5`.
- **Classification:** heuristics, not the 1.5B model. light/code/heavy; npu only for prompts ≤1500 chars.
- **Cloud:** DISABLED until an API key is placed at `~/.config/magi-router/anthropic_api_key` (0600) or `ANTHROPIC_API_KEY` is set. Needs a restart after adding.
- **Logging:** SQLite `~/magi_router/router.db`, table `requests`: per-attempt chain, tier, latency, tokens, cost, 200-char previews.
- **Verified:** math via balthazar's math tool; a concurrent request waits for the busy Hailo chip (BUSY_WAIT_S=60); a 429 race is handled; heavy requests get a clean local-only 503; announce/remove works.
- **Known model problems (balthazar's 1.5B behind the tool-agent prompt):** a code request invented a `reverse` tool (now caught as degenerate → escalates); "capital of Illinois?" came back as a weather report (wrong tool, NOT caught). Plain Q&A and code on the npu tier are unreliable. Consider sending non-tool prompts to a plain-chat mode.
- **Not done yet:** dashboard chat still goes straight to balthazar (not through the router); not on the tailnet (Tailscale is stopped on audire-videre); magi backends only get the last user message (no multi-turn context unless `magi.session_id` is passed).

**2026-09-28 end-to-end with the desktop worker (VERIFIED):**
- The desktop announces every ~22 s. Announces arrive with source IP 192.168.8.1, i.e. via the GL router rather than directly.
- 2.5 KB code-explain: 630 tokens in 11.5 s. Heavy local-only debug question: 6.5 s. Math still goes to balthazar.
- Change: short code prompts skip the npu tier whenever a gpu worker is up; the reverse-string test dropped from 23 s (failed Hailo attempt, then desktop) to 1.9 s.
- The Mac was not announcing at test time; status was requested from its session.
- Workers use tok_s ordering within the gpu tier.

**2026-09-28 dashboard wiring (backend only; the UI revamp is pending Claude Design mockups):**
- `app.py` gained `/api/router/{status,history,stats}` (GET) and `/api/router/chat` (POST {prompt, model, session_id}). The router token is attached server-side and never reaches the browser.
- `/api/stream` is an SSE feed from one shared background poller: router every 3 s, nodes every 15 s. Events: nodes, failover, router_status, router_stats, router_history.
- The old `/api/llm/chat` (direct to balthazar) is unchanged.
- Design brief: `~/magi_dashboard/design/BRIEF.md` + `sample-data/*.json`. Backup: `app.py.bak.<timestamp>`.

**2026-09-28 (later) — prep pass before starting the Claude Design session:**
- `app.py`'s `/api/topology` was stale (still described the D555 as USB-direct and had no mention of the desktop/macbook GPU-worker tier). Fixed in place: D555 edge now says PoE/bridged/no-live-feed, added a third topology segment for the opportunistic GPU workers with real edges to `audire-videre`. Live-verified via `~/magi_dashboard/start_dashboard.sh` restart + `curl :8090/api/topology`.
- `design/sample-data/topology.json` and `router-status.json`/`router-stats.json` updated to match the live router (`macbook` now present as a `dormant` (up:false, "not announced") backend rather than missing entirely; desktop's numbers refreshed).
- `design/BRIEF.md` got a "Current status" section added at the bottom: flags that the MacBook/D555 "offline" states are normal/expected (not errors, need distinct visual treatment from a real failure), that escalation chains in the sample history are real (balthazar tool-call failure -> desktop success, and a full-chain-down failure case), and that the backend endpoints are already real/live, not placeholders — so the Claude Design session should treat this as skinning real data, not inventing a UI shape.
- Not done: didn't touch `nodes.json`/`failover-status.json`/`data-stats.json` — checked against live endpoints and they were already representative (edgerunner unreachable, balthazar/melchior/repositorium up), no edit needed.

**2026-09-29 tool-misfire fix** (handoff from the dashboard session, `~/magi_router/HANDOFF_balthazar_tool_misroute.md`):
- `TOOL_RE` per-tool prompt patterns; new `tool` classification → npu.
- `light` → gpu when up (npu otherwise).
- npu replies that used an off-topic or unknown tool are raised as failed attempts and escalate.
- Verified: Illinois capital → desktop "Springfield".
- Node-side "retry without tools" patch proposed, not deployed.
- A NERV dashboard front end now exists (`static/nerv.js`, built by another session).
- **2026-09-30:** the node-side "regenerate without tools" patch is written and committed locally in mantled-critters (b1f9c61, NOT pushed). Deploying it to balthazar/melchior via ssh/scp was DENIED by the auto-mode classifier ("Remote Shell Writes"), so Gil has to deploy it himself or add a narrow permission rule. Do not work around that.
- **Outlier scan, same day:** only 24 router requests so far. Every misfire predates the fixes. The dashboard still shows edgerunner offline because `app.py` NODES / ssh alias use the stale 10.0.0.137; edgerunner is now 192.168.8.119 (see [[magi-net-8x-migration-status]]). balthazar detection shows "running" in /api/nodes but vision "stopped" in failover, because balthazar's detect runs by script, not systemd, and the heartbeat only counts systemd units.

## 2026-10-03 UPDATE: melchior's npu backend is now always up (enabled 2026-10-02 23:42) + edgerunner address
- melchior's `magi-llm-api` (:8802) was `disabled` (never started at boot, no journal, not a crash). 2026-10-02 23:42 it was `systemctl enable --now`'d. It uses Hailo GenAI with VDevice group "SHARED" and was verified coexisting with `magi-live-detect` on `/dev/hailo0` (no live-detect errors). Real test: cold first request 36.8 s and answered 17*23 WRONG (321) without a tool; a second request asking for the math tool: 10.7 s, correct 391, `tool_used=math`. One request at a time, 429 when busy.
- Router impact: the tier note above says melchior is "only up during failover, so the health check skips it". That is no longer true while the unit stays enabled; the router may now route npu traffic to melchior too. This also departs from the failover design (melchior = vision only, see [[magi-failover]]); confirm with Gil.
- Lesson for routing: the 1.5B model guesses arithmetic without tools and gets it wrong, so the `tool` classification (TOOL_RE) must keep math prompts on a tool-calling path; never trust a no-tool numeric answer from the npu tier.
- The "edgerunner is now 192.168.8.119" note above: verified 2026-10-02, edgerunner eth0 is 192.168.8.139 (same MAC also answers on .119); the `edgerunner` ssh alias now points to .139. See [[magi-net-8x-migration-status]].
