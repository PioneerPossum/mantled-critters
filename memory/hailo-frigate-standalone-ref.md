---
name: hailo-frigate-standalone-ref
description: Third-party GitHub project (msorenss/hailo-frigate-standalone) combining Frigate NVR with Hailo-8L/10H detection and a Hailo VLM chat service on one Pi5 — real-world validation that Vision+GenAI can share a Hailo-10H concurrently
metadata: 
  node_type: memory
  type: reference
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-16T03:37:51.957Z
---

Found by Gil 2026-09-15: https://github.com/msorenss/hailo-frigate-standalone

**What it is:** a Docker Compose scaffold running Frigate 0.18.0 (NVR/video-surveillance with object detection) configured for both Hailo-8L and Hailo-10H detector plugins, **plus a companion Hailo VLM (Vision-Language Model) Chat service**, both sharing the same Hailo accelerator ("enables shared Hailo access"). Runs standalone (no Home Assistant Supervisor required, unlike the upstream add-ons it's based on), with optional MQTT bridge back to Home Assistant. Target hardware: Pi5 + Hailo-10H exposed as `/dev/h1x-0`, Debian/Raspberry Pi OS Trixie, HailoRT 5.3.0.

**Why this matters for magi-cluster:** it's independent, real-world evidence that a Vision detection workload (Frigate) and a GenAI workload (VLM chat) can genuinely run concurrently sharing one Hailo-10H chip — the same claim [[magi-local-llm-feasibility]] found in Hailo's own docs (`running_parallel.md`) but hadn't seen validated by a third party yet. Also useful as a working reference for HailoRT device paths and Docker-based deployment patterns, if that approach is ever preferred over the current bare-metal venv setup on melchior/balthazar.

**Not directly reusable as-is** — melchior/balthazar are running bare-metal `hailo-apps` (not Docker/Frigate), and the current project's actual goal is a custom tool-calling agent (`agent_tools_example`), not an NVR. Worth a closer look for implementation details (their HailoRT device-sharing config specifically) once the local-LLM build actually starts, rather than adopting the whole stack.
