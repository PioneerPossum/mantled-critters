---
name: magi-offline-claude-proposal
description: "Gil's proposal to run local, network-independent LLM agents on melchior/balthazar's Hailo-10H NPUs as offloadable \"physical workers\" for general Q&A and coding tasks, reducing Anthropic token usage"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-15T15:35:45.048Z
---

Gil's goal (clarified 2026-09-15, after an initial vague mention this session couldn't find prior context for — a linked claude.ai/code session URL was inaccessible, 403/authenticated): use melchior and balthazar's Hailo-10H NPUs as **local inference "physical agents"** that can be offloaded to instead of cloud Claude, to reduce overall Anthropic token usage/impact on plan limits.

**Concrete shape of the ask:**
- Submit requests via the (in-progress) web dashboard — e.g. general questions ("what's the weather today?") or coding requests ("write me a Python script that...").
- The two Hailo nodes handle these **on their own hardware**, without a network round-trip to Anthropic for that request.
- **Persistent memory** for the agents should live on repositorium (already running Postgres+pgvector — see [[magi-cluster-status]]), ideally in a **Claude-Code-like format/operational style** — Gil wants something closer to an actual coding agent (tool use, file operations, persistent context) than a bare chatbot.
- Framed explicitly as "two physical agents you can offload requests to instead of cloud processing."

**Open technical question — researched 2026-09-15, see [[magi-local-llm-feasibility]] for full findings.** Short version: yes, Hailo ships an official GenAI/LLM stack for Hailo-10H specifically (both nodes are confirmed H10), including a tool-calling agent example close to what Gil described — but none of it is installed or tested yet on either node, so real numbers (tokens/sec, context length, quality) are still unmeasured. That memory file has the full breakdown, ranked architecture options, and the decisions Gil needs to make before any build work starts.

**Also worth flagging to Gil, not yet resolved:** a literal "what's the weather today" query needs a live data source (an API call), which is in tension with a fully network-independent design — worth clarifying whether "offline" means no dependency on Anthropic specifically (local network/API calls to other services still fine) vs. fully air-gapped.

**How to apply:** this is still at the research/feasibility stage, not implementation — don't build a full agent framework until the Hailo GenAI capability question is answered and Gil has seen a concrete architecture proposal to react to.
