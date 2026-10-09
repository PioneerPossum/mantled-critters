---
name: feedback-memory-incremental
description: "Gil's rule for memory files - append incrementally, never overwrite or condense away details; keep a lessons-learned recap so mistakes are not repeated"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 386af2e4-e1b9-47fc-b2e9-293f7280d9ca
  modified: 2026-10-03T15:34:40.883Z
---

Memory files must be updated incrementally: append new dated sections, never overwrite, rewrite, or summarize away existing details. When something turns out wrong, add a dated correction below it (strike or mark the old claim) instead of deleting it. Also maintain a lessons-learned recap ([[lessons-learned]]) that distills the mistakes and their fixes, so the same errors are not repeated.

**Why:** Gil said on 2026-10-03: "Memory files should be incremental, never overwritten ... need to make sure details are preserved and important lessons are learned so summarizing those as a recap of lessons learned in itself is important to not repeat same mistakes." Same day, I reported the Netgear switch and edgerunner on stale 10.0.0.x addresses taken from old notes, when everything had moved to 192.168.8.x.

**How to apply:** use append (`>>` or an Edit that adds after the last line) on existing memory files; when a fact changes, add a dated "UPDATE"/"CORRECTION" section. Only MEMORY.md index lines may be edited in place (one line per memory). Before stating live state (IPs, service status) from memory, verify it first, and record any new lesson in [[lessons-learned]].
