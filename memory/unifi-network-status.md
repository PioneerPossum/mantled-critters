---
name: unifi-network-status
description: "UniFi controller deployment status — pivoting from a Docker/linuxserver.io setup to Ubiquiti's official \"UniFi OS Server\" per their own in-app guidance; audire-videre (x64) in progress, edgerunner (arm64) blocked on OS reinstall"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-27T06:07:29.846Z
---

Gil wants UniFi Network running and accessible — two genuinely separate deployments, not redundancy: **audire-videre** manages the home network's fixed Ubiquiti switch; **edgerunner** is meant to eventually manage a separate *portable* UniFi switch Gil plans to carry with edgerunner when mobile.

## Pivot, 2026-09-19: use official "UniFi OS Server," not the community Docker image

After getting the Docker-based `lscr.io/linuxserver/unifi-network-application` + separate MongoDB setup fully working on audire-videre (see below for that story and a real bug fixed along the way — worth keeping for reference even though superseded), Gil signed into the web UI and the app itself recommended migrating to **"UniFi OS Server"** — Ubiquiti's own official, natively-packaged distribution, citing update/maintenance concerns with the older standalone approach. This is authoritative first-party guidance, not a third-party opinion — worth trusting over the just-built setup, especially given the class of bug already hit (a community image's first-run config-generation quirk — exactly the kind of thing an officially-maintained package should avoid).

**Available builds:** Linux x64 and Linux arm64 (confirmed by Gil, logged into their Ubiquiti account). Plan:
- **audire-videre**: Linux x64 build — in progress, Gil downloading (requires their Ubiquiti account login, not something to fetch without them).
- **edgerunner**: Linux arm64 build — held until edgerunner's OS reinstall to 64-bit is complete (see below, unrelated in-progress migration).

**Once the official installer is in place on audire-videre, tear down the Docker/Mongo setup** (`unifi-network` + `unifi-mongo` containers, `/home/sinewave/unifi-data`, `/home/sinewave/unifi-mongo-data`, `/home/sinewave/.config/unifi/mongo.env`) — no reason to run both.

## Reference: the Docker/linuxserver.io attempt (superseded, kept for the bug-fix lesson)

Deployed via Docker (`--net host` for L2 device-discovery), `mongo:7.0` + `lscr.io/linuxserver/unifi-network-application:latest`, got working after fixing a real bug:

**The bug, worth remembering for ANY linuxserver.io-style image**: Mongo connection env vars are only evaluated on the *very first* container startup — baked into a generated `/config/data/system.properties` (`db.mongo.uri=...`) and never regenerated from env vars again, even across container recreation. The first launch attempt here used empty Mongo credentials (before Mongo auth was even configured), baking in `db.mongo.uri=mongodb://:@127.0.0.1:27117/unifi` (empty user/pass) — every subsequent attempt with correct env vars kept failing with `"No username is provided in the connection string"` because the app kept reading that same stale baked-in URI. Fix: wipe the `/config` volume entirely (`docker run --rm -v <dir>:/data alpine sh -c 'rm -rf /data/*'`, no host sudo needed) and recreate fresh with correct vars present from the true first boot. Confirmed via `system.properties` showing a real populated URI afterward, port 8443 responding 200 immediately.

## edgerunner — blocked, pending OS reinstall (unrelated to the UniFi pivot)

**Root cause, confirmed via minimal reproduction:** edgerunner's kernel is 64-bit (`aarch64`) but its OS *userspace* is 32-bit ARM (`armhf`) — common Pi OS default even on 64-bit-capable hardware. A 32-bit Docker/`runc` cannot launch real arm64 containers at all — confirmed by a minimal official `arm64v8/alpine` image crashing identically (exit 159/SIGSYS) just running `echo`. Not a config problem; needs a genuine OS reinstall.

**Gil's plan (in progress):** fresh 64-bit SD card, migrate edgerunner's current config over, verify, then relocate from SD to NVMe for permanent hardware. Real risk flagged: temporary loss of remote troubleshooting for edgerunner specifically if something goes wrong (mitigated by the independent `gl-e5800` Tailscale exit node still providing subnet-routed home access) — and it needs physical presence, can't be done remotely.

**Do not attempt any edgerunner work while this migration is in progress.**

## 2026-09-25 prep done (install still waiting on Gil's sudo)
Work folder now `~/Projects/claude-projects/unifi/` (runbook.md, findings.md, `install-as-gil.sh`; old runbook path is a symlink). Gil's answers: SSO owner + local "restrict to local access" Super Admin as failover, `--network-mode pasta`, 2 GiB swapfile OK, backups in ~/Downloads (current: `network_backup_25.09.2026_17-06_v10.6.106.unf`, sha 23a4d83a...; the 09.24 .unf files are gone). Installer files: `5172-linux-x64-...` = audire-videre (sha f6111e93...), `f730-linux-arm64-...` = edgerunner (sha 8e29bd50..., NOT ready, OS reinstall pending). `install-as-gil.sh` is unexecuted, y/N-gated, stops (never removes) the Docker stack. **Gotcha:** UOS 5.1.42 likely bundles Network 10.5.67 < backup 10.6.106, so update the Network app in UOS before restoring. Copy to repositorium: `/home/sinewave/512_VideoStick` is root-owned; a subagent instruction to write `backups/unifi/` there was denied by the auto-mode classifier, so it is NOT done and needs Gil's OK. 09-25 03:15/03:35 magi-backups verified OK. The router's `dhcp-export` CSV in Downloads is header-only (empty).

## 2026-09-26 UOS INSTALLED on audire-videre (Gil ran install-as-gil.sh; earlier "syntax error ( unexpected" was him running it with `sh`=dash, use `bash`)
Read-only check afterward: `uosserver` active, listening 11443/8080/8880/6789 tcp + 3478/5514/10001 udp, 2 GiB /swapfile active, Docker unifi-network/unifi-mongo Exited (stopped, not removed = rollback intact). Switch .155, USW-Flex-2 .159, sensor .212 all ping. Gil says everything worked EXCEPT the UP-AirQuality sensor (.212), reported "owned by a different network" (still not visible/controllable). Not fixed (needs UI/device creds/physical reset, Gil asleep). Options: factory-reset sensor then adopt; or set-inform via device SSH creds (Settings > System > Advanced > Device SSH Authentication in restored config); check whether it is a Protect-family device managed elsewhere. Do NOT restart the old Docker stack to "forget" it without Gil (port conflicts with UOS).

## 2026-09-27 old Docker UniFi containers removed (Gil asked, timestamped)
`docker rm unifi-network unifi-mongo` at 2026-09-27 00:57:40 CDT (were already stopped ~24h). Removal logged in `~/Projects/claude-projects/unifi/removal-log.txt`. Kept for safety: both images, `~/unifi-data`, `~/unifi-mongo-data`, and a config snapshot (`docker inspect` output, mode 600, contains DB creds) in `~/backups/unifi-docker-removal-20260927-005740/`. UOS confirmed unaffected (web UI 200 after removal). Explained to Gil that container removal does NOT release the air-quality sensor .212 (its binding lives on the sensor + the old controller's DB, not the containers) - still needs a factory reset + re-adopt, or `set-inform` via device-SSH creds once available.

## Next steps
- (superseded by the 2026-09-26 install above) Get the UniFi OS Server x64 installer from Gil, install on audire-videre, tear down the Docker setup.
- Once edgerunner's 64-bit reinstall is done, install the arm64 UniFi OS Server build there for the portable-switch use case.
- Complete first-run setup (admin account, adopt the actual switch at `192.168.8.155`) via whichever UI ends up live.

## 2026-09-28 new site: Colorado, site-to-site VPN planned (noted by Gil, not yet built)
Gil has another site in **Colorado** to join to the total Ubiquiti network via **site-to-site VPN**. Hardware named: a **GL.iNet GL-3600** (Brume/Beryl-class GL router) at the Colorado end, connecting to a **UDM Pro Max** (the gateway at the other end; which site hosts it is not yet stated, and it is not mentioned elsewhere in these notes, so confirm). Open questions to settle before any config: which site the UDM Pro Max is at; VPN type (WireGuard is supported natively on both UniFi and GL.iNet, IPsec is the alternative); subnets at each site (must not overlap; home LAN is 192.168.8.x, see [[magi-net-8x-migration-status]]); whether the UDM has a public/static IP or is behind the Xfinity bridge; how this interacts with the existing Tailscale subnet routing. No changes made.
**Answers (2026-09-28):** UDM Pro Max is in **Colorado**, LAN 192.168.1.0/24 (gateway 192.168.1.1), public IP 67.177.200.160 (Comcast residential, may be dynamic). Home LAN is 192.168.8.x, so no overlap. WireGuard, simplest path preferred. GL-3600 is the home-side client, so the tunnel dials out to the UDM. Tailscale stays as a supplement, not replaced; just don't advertise 192.168.1.0/24 or 192.168.8.0/24 via both paths. Still unconfirmed: whether the GL-3600 is the home router or sits behind something, and whether 67.177.200.160 is static.
**2026-09-28 tunnel UP (Gil set up):** OpenVPN site-to-site, GL-3600 (home, gateway 192.168.8.1) <-> Colorado UDM Pro Max. Verified from audire-videre: ping 192.168.1.1 4/4, ~50 ms, traceroute 192.168.8.1 -> 192.168.1.1. Oddity left open: audire-videre's public IP read 67.177.200.160, the same address Gil gave as the UDM's WAN IP, so either all traffic exits via Colorado (route-all on) or that was home's IP. Gil considers it done; other nodes' routes not tested.
