---
name: d555-poe-setup-guide
description: "Official Intel/RealSense D555 PoE Quick Setup Guide (Rev 001, Nov 2025) — explains why earlier D555 network-discovery attempts on the Netgear switch found nothing, and how to actually set up PoE/Ethernet streaming if ever needed"
metadata: 
  node_type: memory
  type: reference
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-15T21:26:41.352Z
---

Source: https://realsenseai.com/wp-content/uploads/2025/12/D555_PoESetupGuide-Rev001.pdf (official RealSense doc, Rev 001, Nov 2025).

**This retroactively explains the network-discovery dead end from the original D555 investigation** (see [[magi-cluster-status]] — ARP/ping sweeps, GVCP/GigE Vision discovery, and DDS multicast listening on the Netgear switch all came up empty):

- **The D555 ships on its own private subnet by default: `192.168.11.55/24`**, gateway `192.168.11.1` — completely disjoint from Gil's actual networks (`192.168.8.0/24` magi-cluster net, `10.0.0.0/24` Netgear/Xfinity net). Nothing scanning the real LANs would ever see it unless it happened to also be bridged onto `192.168.11.0/24`.
- **It doesn't speak GigE Vision/GVCP at all** — it uses **DDS** (Data Distribution Service, RTPS-based pub/sub middleware) as its Ethernet transport, which explains why the GVCP discovery attempt specifically found nothing (wrong protocol entirely).
- The DDS multicast-listening attempt also would have failed even if the protocol guess were right, because **DDS discovery is subnet/broadcast-domain scoped** — a listener on `192.168.8.0/24` or `10.0.0.0/24` will never see multicast traffic from a device on `192.168.11.0/24`.
- **Requires jumbo frames (MTU 9000 on the host, 9014 bytes on the wire)** to work at all — a mismatched MTU would silently break communication even if the subnet issue were solved.
- Net result: the earlier conclusion in magi-cluster-status.md ("the Ethernet link-up we chased was almost certainly a PoE→USB power-splitter's own NIC, not the camera's data path") is very likely still correct as the practical answer for THIS setup — since the D555 ended up connected via USB, not Ethernet, this entire PoE stack was never actually engaged. But if PoE/Ethernet streaming is ever revisited, this doc is the reason why, not USB brownout tuning.

**If PoE/Ethernet streaming is ever set up for real, here's what it takes:**
- Hardware: 2+ Cat6-or-better ethernet cables, a **Gigabit+ PoE switch or injector that supports jumbo frames** (examples given: TP-Link TL-POE150S, BV-Tech 30W Gigabit PoE injector, UCY Gigabit PoE injector).
- Software: `librealsense` SDK v2.56.4+, camera FW v7.56.19918.835+ (the D555 here is already on FW 7.56.19919.4144, above the minimum — confirmed via pyrealsense2 earlier).
- Host network config: static IP in the `192.168.11.0/24` range (e.g. `192.168.11.70`), subnet mask `255.255.255.0`, MTU 9000 (Linux: `sudo ip link set <iface> mtu 9000`, permanent via `nmcli connection modify "<conn>" 802-3-ethernet.mtu 9000`).
- Enable DDS: either in RealSense Viewer's settings (checkbox, requires app restart) or via CLI: `rs-dds-config --eth-first`. Config persists in `~/.realsense-config.json` (Linux) or `%appdata%` (Windows).
- To reconfigure the camera's own IP (needed for multi-camera setups, or to move it onto an existing LAN instead of a dedicated `192.168.11.x` segment): `rs-dds-config --serial-number <SN> --ip <IP address> --gateway <gateway>`.
- Multi-camera options: either all cameras + host on one NIC/subnet with distinct camera IPs, or one NIC-and-subnet pair per camera (cleaner, avoids IP conflicts).
- Troubleshooting: Windows Firewall must allow RealSense Viewer / `rs-dds-config` through (public+private networks); Ubuntu's firewall is disabled by default and should stay that way for this (or explicitly allow it if enabled).

**How to apply:** irrelevant to the current working setup (D555 is USB-attached to audire-videre and streaming fine — see [[magi-cluster-status]]). Only relevant if Gil later wants to move the camera to true PoE/Ethernet operation (e.g. to decouple it from being physically tethered to audire-videre), or if a second D555 gets added and PoE starts making more sense for multi-camera wiring.
