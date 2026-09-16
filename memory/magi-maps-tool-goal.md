---
name: magi-maps-tool-goal
description: "Gil's future \"someday\" goal to add a GPS/directions tool to the magi-cluster LLM agent's tool-calling system, similar to the existing math/weather tools"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-16T19:15:29.780Z
---

Mentioned 2026-09-16, alongside a comparison of the two LLM integration paths ([[magi-local-llm-feasibility]] Option A `magi_llm_api.py` vs. Option B `hailo-ollama`+Open WebUI). Gil wants to eventually add a directions/route-planning tool to the agent's tool-calling system (same pattern as the existing math and weather tools in `agent_tools_example`/`magi_llm_api.py`) — e.g. "how do I get from X to Y."

**Status: aspirational, explicitly "more for later," not started.**

**Options surfaced, ranked by fit:**
1. **Self-hosted OSRM** (OpenStreetMap-based routing engine) — no external API/account needed at all, could run on repositorium. Best fit for this project's local-first ethos (matches [[magi-offline-claude-proposal]]'s whole premise), but requires downloading a regional OSM map extract and running the routing service — one-time moderate setup, not hard.
2. **Mapbox or OpenRouteService** — free API key, no billing account required, much lower friction than Google. Reasonable middle ground if self-hosting OSRM feels like too much setup when this actually gets picked up.
3. **Google Maps Directions API** — most familiar option but needs a Google Cloud account with billing enabled (paid beyond a small free tier) — an account only Gil can create, not something to set up unilaterally.
4. **Apple Maps** — not a practical path; no general third-party server-side directions API comparable to Google's.

**How to apply:** when this gets picked up, implement it as a new tool module following the exact pattern of the existing `weather` tool in `agent_tools_example`/`magi_llm_api.py` (see [[magi-local-llm-feasibility]] Session 4-6 for how that tool-calling pattern works — prompt-embedded tool schema, `<tool_call>` JSON parsing, real Python function dispatch). Default to recommending OSRM unless Gil prefers the lower setup effort of a hosted API when it's time to build.
