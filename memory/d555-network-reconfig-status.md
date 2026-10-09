---
name: d555-network-reconfig-status
description: "Status of getting the D555 camera working over PoE/Ethernet (not USB) — real progress made, blocked on the exact DDS unicast-peer JSON schema for NAT traversal"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-19T21:45:51.489Z
---

2026-09-19: Gil moved the D555 from USB (audire-videre) to PoE/Ethernet on the Netgear switch, wanting it reachable that way going forward. See [[d555-poe-setup-guide]] for the original PoE research.

## Confirmed working
- **PoE power delivery is genuinely fine** — camera responds to ping on its factory-default address (`192.168.11.55`) when reached via the Netgear switch.
- The camera physically sits on the Netgear-switch segment (`10.0.0.0/24` native, port previously labeled 5), same segment as **edgerunner** — the only machine physically adjacent to it. edgerunner is ARM64 with no RealSense SDK (same category of blocker as the earlier D435i/Caspar ARM64 pain).
- **audire-videre has full working `pyrealsense2` (x86_64, no ARM blocker)** but is on a *different physical switch* (Ubiquiti, `192.168.8.0/24`) — not L2-adjacent to the camera at all.

## Bridge built (still in place, not torn down)
To let audire-videre's working SDK reach the camera despite being on a different physical network:
- **edgerunner**: secondary IP `192.168.11.70/24` on `eth0` (the camera's native subnet), `net.ipv4.ip_forward=1`, iptables `MASQUERADE` for traffic to `192.168.11.0/24` out `eth0`.
- **audire-videre**: `sudo ip route add 192.168.11.0/24 via 192.168.8.140` (edgerunner's wlan0 address) — Gil ran this.
- Result: **unicast reachability confirmed** (ping works both directions through the bridge).

## The actual remaining blocker: DDS/RTPS discovery doesn't cross NAT cleanly
`pyrealsense2.context()`'s USB-style device discovery finds 0 devices over this bridge, even with `{"context":{"dds":{"enabled":true,"domain":0}}}` passed. Root-caused, not just assumed:
- DDS peer discovery (SPDP) uses **multicast** (`239.255.0.1:7400`, confirmed via real packet capture — the camera announces itself every 2s to this address). NAT/routing alone (unicast MASQUERADE) doesn't carry multicast.
- **Fixed**: installed `smcroute` on edgerunner, configured bidirectional static multicast relay between `eth0`/`wlan0` for `239.255.0.1`. **Verified working** — a raw Python UDP multicast-group listener on audire-videre genuinely receives the camera's real announcement packets through the relay.
- **Still blocked**: even with multicast successfully bridged, `pyrealsense2` still finds 0 devices. This points to the *next* layer: DDS/RTPS's discovery handshake is two-phase — multicast announcement, then a **unicast** follow-up where each side embeds its own IP for the other to connect back to directly. This handshake isn't NAT-aware by design; the camera or audire-videre likely embeds a self-reported address the other side can't actually route to through the bridge.
- Investigated a genuine lower-level API: `pyrealsense2.pyrealdds` (a real, separate compiled module linking Fast-DDS/`eprosima::fastdds` symbols directly — confirmed via `strings` on the `.so`). Has a real `participant`/`device_watcher` API (`participant.init(local_settings_json, domain_id)`) that likely supports an "initial peers" style unicast-peer override (Fast-DDS itself definitely supports this concept generically), but **the exact JSON key names this Intel wrapper expects for `local-settings` were not discoverable** via `load_rs_settings()` (returns `{}` empty by default) or web research — would need direct access to librealsense's C++ source (`dds_participant.cpp` or similar) to find the real schema, which wasn't efficiently accessible with the tools available this session.
- Also tried: a Fast-DDS XML profile via `FASTRTPS_DEFAULT_PROFILES_FILE` env var with an explicit `<initialPeersList>` pointing at the camera — no effect, suggesting librealsense's DDS layer may construct its participant programmatically rather than honoring this standard Fast-DDS env var.

## Options going forward (Gil to decide, not yet chosen)
1. **Keep digging on the JSON schema** — would need to actually clone/grep librealsense's C++ source for the `local-settings` parser (or ask Hailo/Intel-adjacent community/forums) to find the real initial-peers key names for `pyrealsense2.pyrealdds.participant.init()`.
2. **Build librealsense natively on edgerunner (ARM64)** — sidesteps the NAT problem entirely since edgerunner is genuinely L2-adjacent to the camera (no bridge needed at all). Known to be a real time investment, same pain category as the earlier D435i blocker.
3. **Drop back to USB via audire-videre** — already fully working, zero risk, if the network path isn't worth the continued effort.

## Bridge is still live
The multicast relay (smcroute) and unicast bridge (edgerunner secondary IP + MASQUERADE + audire-videre route) are all **still in place** as of this writing — nothing was torn down, since Gil may want to keep experimenting. Low risk to leave running (isolated to `192.168.11.0/24` traffic specifically), but worth knowing it's there if debugging anything else on edgerunner or audire-videre's routing table later.

## CORRECTION 2026-09-21 (read-only survey after the 2026-09-20 power cycle)
The "Bridge is still live" note above is now WRONG: nothing in the bridge persisted through reboot. On edgerunner the secondary IP 192.168.11.70 is gone and the iptables MASQUERADE rule is gone (no persistence mechanism was ever set up: eth0 is an auto-generated NM profile, no iptables-persistent); on audire-videre the `192.168.11.0/24 via 192.168.8.140` route is gone (and edgerunner's wlan0 is now .141 anyway). Only smcroute survived (active+enabled, /etc/smcroute.conf intact, relay rules loaded both ways), and `net.ipv4.ip_forward=1` survives by accident via /etc/sysctl.d/99-tailscale.conf. Camera ping fails from both hosts because no 192.168.11.x address/route exists, which does NOT prove the camera is dead: the multicast table shows a (192.168.11.55, 239.255.0.1) entry, so it is announcing. When the D555 work resumes, rebuild the bridge (or better, do it on the reinstalled 64-bit edgerunner with a persistent NM secondary address + nftables rule, and reserve edgerunner's wlan0 IP so the route next-hop stops drifting).

## 2026-10-03 CORRECTION: the "Netgear-switch segment 10.0.0.0/24" and edgerunner 10.0.0.x notes above are STALE
The Netgear switch and edgerunner are no longer on 10.0.0.0/24, so any rebuilt 192.168.11.x bridge must use the current addresses (and the old next-hop "edgerunner wlan0 192.168.8.140/.141" is stale too; wlan0 is now on 192.168.10.x). Verified live 2026-10-02 (the whole net moved to 192.168.8.0/24, see [[magi-net-8x-migration-status]]): Netgear GS308EPP = **192.168.8.101** (same MAC xx:xx:xx:xx:xx:xx, web UI HTTP 200). edgerunner eth0 = **192.168.8.139** (the same MAC also answers on .119), wlan0 = 192.168.10.142, tailscale 100.x.x.x. The `edgerunner` ssh alias in `~/.ssh/config` on audire-videre now points to .139 (backup `~/.ssh/config.bak-20261002`). edgerunner's `tailscale serve` already targets 192.168.8.101:80 (:8239) and 192.168.8.136:8090 (:8090); both backends returned 200. The old text above stays as history; do not use those addresses. Before reporting a host as down, sweep and check hostname/MAC first (see [[lessons-learned]]). D555 status itself was not re-checked on 2026-10-02.
