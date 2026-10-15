---
name: feedback-board-auto-update
description: "Gil's standing rule (2026-10-09): any task done on the MAGI system must also update the MAGI-CLUSTER BOARD, public side sanitized, detail behind login"
metadata:
  node_type: memory
  type: feedback
---

**Rule (Gil, 2026-10-09):** every task done on the MAGI system should automatically update the MAGI-CLUSTER BOARD ([[magi-board]]): findings, fixes, projects, blockers, resolutions, suggestions. Keep sensitive information off the public side; critical information stays behind login.
**Why:** he travels and wants one place to see state; he also said to use agents, MCP connectors and remote workers as necessary to get it done (I used parallel subagents for the memory read; no connector or remote worker was needed: the data is local files, and the routers' workers are for LLM offload, not this).
**How to apply:** after finishing MAGI work, run `~/bin/magi-board card|move|tick|log ...` (see `magi-board --help`); add a `public` block ONLY when the item is harmless to a stranger (the validator enforces this). If nothing board-worthy changed, `magi-board ack`. The project Stop hook will block the stop once if a MAGI memory note is newer than the board, so writing the memory note first and the board second is the normal order. Never edit `data/*.json` by hand.
