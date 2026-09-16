---
name: esp32-av-ndi-streaming
description: Sibling project to magi-cluster — wiring XIAO ESP32S3 Sense camera boards into the existing Resolume/NDI AV pipeline; shares hardware lineage with the fursuit HUD project
metadata: 
  node_type: memory
  type: project
  originSessionId: e5718790-74ec-4313-bedc-feb387a7b25c
  modified: 2026-09-15T00:56:20.130Z
---

Uploaded from a separate project's `CLAUDE.md` on 2026-09-15. This is a **different repo/project** from magi-cluster but shares hardware and some infrastructure history — worth cross-referencing, not merging.

**What it is:** getting XIAO ESP32S3 Sense boards (chosen over base ESP32S3 Sense for better WiFi antenna) with OV5640 camera modules (upgraded from OV2640) ingested into Gil's existing Resolume Arena + NDI show-control pipeline (Lenovo Legion laptop → NDI → Raspberry Pi endpoints via Dicaffeine — this is the same "oculus"/Dicaffeine pipeline whose Boost/ICU and Bookworm/Trixie troubleshooting is in Gil's general working history, and the same `dserver`/Dicaffeine process still found running on **repositorium** in [[magi-cluster-status]] from its prior life as "oculus").

**Relationship to magi-cluster:** a spinoff of the Magi/fursuit three-node AI vision project ([[realsense-caspar-sensor-fusion]]) — the *same* ESP32S3 Sense boards and firmware were also deployed for the fursuit's wearable HUD, so firmware fixes may or may not carry over depending on whether it's literally the same physical boards or a second set.

**Known facts (may be stale, flagged as such in the source notes):**
- One board had a working MJPEG stream at `http://192.168.8.115/stream` — **not verified**, could easily have changed (DHCP lease, board swap).
- Firmware gotchas carried over from the fursuit boards: `sensor_t` needs `#include "sensor.h"` separately from `esp_camera.h`; OV5640 horizontal-mirror fix is `s->set_hmirror(s, 1)`.

**Open questions the source notes flag (unresolved as of upload):**
1. Ingestion path into Resolume — direct IP-camera source, or via an ffmpeg/GStreamer relay on a Pi re-publishing as NDI? Depends on Resolume's native format support (MJPEG vs H.264).
2. Same physical boards as the fursuit project, or a separate set? Determines whether firmware fixes need reapplying.
3. Network topology — same Ubiquiti segment as the rest of the AV gear, or isolated? (Note: as of the 2026-09-14 magi-cluster session, Gil's home network has *at least* the Ubiquiti magi-cluster segment (`192.168.8.0/24` proper) and a separate Netgear-switch/Xfinity-modem segment (`10.0.0.0/24`) — worth checking which one these ESP32 boards, and `192.168.8.115` specifically, actually live on.)
4. Target use case — motion-tracking input, monitoring feed, or decorative video source — changes whether framerate or latency matters more.
