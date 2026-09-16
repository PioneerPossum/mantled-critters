---
name: feedback-network-automation-caution
description: "Lessons from real mistakes made while automating changes on the magi-cluster's home network infrastructure (2026-09-14) — apply to any future direct network/host automation in this project"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e5718790-74ec-4313-bedc-feb387a7b25c
  modified: 2026-09-14T22:48:30.129Z
---

Two real, self-inflicted mistakes happened during network automation work on 2026-09-14, both recoverable but worth not repeating.

**1. Don't add a secondary IP alias in the same subnet on a second interface of a multi-homed host.** Adding `192.168.8.51/24` to balthazar's `wlan0` (to reach a device stuck on a stale `192.168.8.0/24` address on a different physical wire) caused balthazar's *real* `eth1` address on the actual `192.168.8.0/24` LAN to become unreachable from other hosts — likely an ARP/weak-host-model conflict from having the same subnet configured on two interfaces at once. Reason: Linux's default weak host model lets any interface answer ARP for any locally-configured address, which breaks routing when two interfaces claim overlapping subnets that are actually different physical networks.
**How to apply:** if you need to reach a device on a subnet that doesn't match any of a target host's real interfaces, expect this kind of trick to have side effects on that host's other interfaces. Prefer a machine that's *only* on the target segment (or has no conflicting subnet already configured) before reaching for a secondary-IP alias. If you do it anyway, remove it immediately after use and verify the host's normal connectivity recovers.

**2. Double-check you're targeting the *right* host when multiple similar addresses exist.** edgerunner has two addresses on the same `10.0.0.0/24` segment (`10.0.0.137` eth0, `10.0.0.138` wlan0); balthazar's wifi address on the same segment is `10.0.0.197`. Mixed up `10.0.0.138` (edgerunner's wifi) for balthazar's wifi address once, and "fixed" the wrong machine as a result (a no-op, but wasted a step and could have been worse).
**How to apply:** when several hosts share a subnet and only differ by the last octet, explicitly verify identity (`hostname` over SSH) before making any config change — don't rely on remembered/assumed IP-to-host mapping, especially under time pressure or after several other addresses have been juggled in the same session.

**General pattern that held up well and is worth repeating:** for any device with a strict rate limit or session limit on its management interface (see [[gs308epp-switch-api]] for the concrete case), consolidate all reads/writes for one attempt into a single script/session rather than making several separate connections — and when locked out, back off for real wall-clock time (the user explicitly asked for 10-minute-spaced retries here) rather than retrying quickly, which just extends the lockout.
