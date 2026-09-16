---
name: realsense-caspar-sensor-fusion
description: "Prior project notes on a third Magi-system node (\"Caspar\") doing RealSense D435i sensor fusion, and the known ARM64/pyrealsense2 blocker — directly relevant to getting any RealSense camera (including the D555 from the 2026-09-14 session) working on a Pi"
metadata: 
  node_type: memory
  type: project
  originSessionId: e5718790-74ec-4313-bedc-feb387a7b25c
  modified: 2026-09-15T03:22:05.027Z
---

Uploaded from an earlier project's `CLAUDE.md`-style notes file (`realsense-d435i-notes.md`) on 2026-09-15. Original context, not yet cross-checked against current [[magi-cluster-status]] hardware:

**Caspar** is a third node in the "Magi three-node vision system" alongside melchior and balthazar — originally a Pi 4, CPU-only (no Hailo HAT), doing sensor fusion: an **Intel RealSense D435i** depth camera + an MLX90640 thermal camera + humidity/temp sensors. Feeds the fursuit HUD's mode-switcher (camera passthrough / AI detection overlay / thermal / depth map / dashboard / ambient).

**Confirmed 2026-09-15: Caspar IS `repositorium`** in [[magi-cluster-status]] — it's been repurposed to also (or instead) serve as the cluster's Postgres/Redis "repository" node, and its hardware now reports as Pi 5 (`rpi-2712` kernel), matching the "upgradeable to Pi 5 + Hailo HAT" note below. No RealSense device currently shows up over USB on repositorium.

**Also confirmed 2026-09-15: the D555 and D435i are two separate, different cameras** (different USB PIDs — D555 is `8086:0b56`) — see [[magi-cluster-status]] for the full D555 resolution (it's working great over USB on audire-videre; x86_64 `pyrealsense2` just works, no build needed). **The ARM64 blocker described below is still real and unresolved** for any Pi-hosted RealSense camera (D435i on Caspar, or the D555 if it ever needs to move to a Pi) — nothing in this session's D555 work touches or fixes it.

**Note the model mismatch:** these notes are about a **D435i** (USB depth camera), while the camera physically found on the Netgear switch in the 2026-09-14 session was described as a **D555** with its own PoE/Ethernet capability. These may be two different cameras entirely (different Intel RealSense generations/connection types), not the same unit — don't assume the D435i's USB-specific blockers automatically apply to the D555 without confirming which camera is actually in hand.

**Known blockers (from prior work on the D435i specifically, on Caspar's Pi 4):**
1. **No ARM64 wheels for `pyrealsense2` on PyPI.** `pip install pyrealsense2` fails outright on any Raspberry Pi (or other ARM64 board) — there's simply no prebuilt wheel published for that architecture. The only fix is building [`librealsense`](https://github.com/IntelRealSense/librealsense) from source, which was **not yet completed** as of these notes. **On x86_64 (a normal Linux/Windows laptop or desktop), prebuilt `pyrealsense2` wheels exist on PyPI and a plain `pip install pyrealsense2` should just work** — if the D555 needs to be driven by RealSense SDK/software rather than talking a discovery protocol itself, doing that from an x86_64 machine (this Kali box, `audire-videre`) is far less work than an ARM64 source build on a Pi.
2. **GStreamer has no native RealSense support.** Bridging requires a GStreamer `appsrc` fed by a background thread, `rs.align()` to correct the physical color/depth sensor offset, and manual BGR→RGB conversion. Only relevant once actual streaming/pipeline integration is being built, not for initial discovery/viewing.

**For a GUI-available session:** Intel ships a standalone **RealSense Viewer** GUI app as part of the SDK — this is likely the fastest way to confirm a camera is alive and see its stream/config without writing any pipeline code at all, and doesn't hit the ARM64 wheel problem the same way pyrealsense2-via-pip does (the Viewer ships prebuilt for x86_64; for ARM64 it would need the same from-source `librealsense` build). Worth trying before writing any custom Python.

**Still open (per original notes, unconfirmed current state):** the `librealsense` ARM64 source build itself, and distance-annotation rendering (OpenCV overlay or structured JSON output) once a pipeline exists.
