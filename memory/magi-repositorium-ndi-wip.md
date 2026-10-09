---
name: magi-repositorium-ndi-wip
description: "The leftover Dicaffeine NDI/VJ dserver still running on repositorium's port 80 is an intentionally-parked work-in-progress, not stale cruft — revisit with a dedicated session later"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-17T17:32:12.430Z
---

Repositorium's NVMe was previously used as "oculus" for an NDI/VJ (video-jockey) pipeline before being repurposed as the magi-cluster's shared Postgres/Redis hub (see [[magi-cluster-status]]) — a leftover Dicaffeine `dserver` still runs on its port 80 from that era.

**Clarified by Gil 2026-09-17:** this is NOT the same "oculus" as the Dexcom e-paper project (see [[dexcom-epaper-project-ref]] — that's a different physical Pi at his partner's house, unrelated). This is a genuinely separate, older project idea of Gil's own that happens to have used the same "oculus" name.

**Status: intentionally parked, not abandoned.** Gil wants this stored as a "work in progress we can touch with a different session at some point" — i.e., don't clean it up or treat the leftover `dserver` as cruft to remove; it represents unfinished project intent worth returning to.

**How to apply:** don't touch/remove the Dicaffeine `dserver` on repositorium without checking with Gil first, and don't confuse this with the Dexcom project when either "oculus" comes up in conversation — they are unrelated. No action needed until Gil specifically wants to revisit this project.
