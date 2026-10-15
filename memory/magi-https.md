---
name: magi-https
description: "HTTPS front end for audire-videre web services (user-level Caddy, local CA, ports 8443/8445/8446), CA install, 443 switch, and the app changes still needed"
metadata:
  node_type: memory
  type: project
---

## 2026-10-09: built (HTTPS fork, no root)
- **Caddy 2.11.4 from the Kali repo** (`apt-get download caddy`, signature-verified, `dpkg -x` into `~/caddy/root`, binary `~/caddy/root/usr/bin/caddy`). User unit `magi-caddy.service` (enabled, linger), config `~/.config/caddy/Caddyfile`, local CA `tls internal` (data in `~/.local/share/caddy/pki/authorities/local/`, ROOT KEY STAYS THERE: never copy/commit/back up it casually). Public root cert exported to `~/ca-export/magi-local-ca.crt` (SHA-256 8F:4A:E6:6C:...:C0:31, valid to 2036-08) with per-OS install steps in `~/ca-export/INSTALL-CA.txt`.
- **Endpoints** (cert SANs: IP 192.168.8.136 via default_sni, plus audire-videre, audire-videre.local, localhost): dashboard `https://192.168.8.136:8443` (+ `/live/*` proxied to MediaMTX WHEP :8889), VJ control `https://...:8445`, router API `https://...:8446`, `http://...:8081` redirects to https :8443. Plain http 8090/8095/8810/8889 untouched and still working. 8080/8444/11443 are taken by other services (so not used).
- Verified with the CA (no -k): chain OK, SAN OK, dashboard/VJ/router pages load, Range requests to /media return 206 with Content-Range, X-Forwarded-For and X-Forwarded-Proto: https reach the backend, WHEP OPTIONS 204 over https.
- **443/80 switch (needs Gil's one-time sudo):** see `~/caddy/README-443.txt` (`sudo setcap cap_net_bind_service=+ep ~/caddy/root/usr/bin/caddy`, then copy `Caddyfile.443` over `Caddyfile`, restart). Rollback documented there.
- **App changes still needed (not done by the fork, files owned by other sessions):** (1) VJ Flask: trust the proxy only from 127.0.0.1 and set the session cookie Secure only when the request is https (ProxyFix applied only when remote_addr is 127.0.0.1, `get_cookie_secure` returns request.is_secure), then optionally refuse login over plain http. (2) `static/live.js`: when `location.protocol === "https:"` default WHEP_URL to `${location.origin}/live/whep` (mixed content blocks http://host:8889). (3) `static/nerv.js` VJ tab iframe: use `https://<host>:8445/` when the dashboard is on https. Other absolute `http://` URLs in the pages will be blocked as mixed content.
- At the show on the Mudi, the IP stays 192.168.8.136 if the LAN is renumbered to 8.x as planned in [[magi-net-8x-migration-status]]; otherwise the cert has no SAN for a different IP (add it to the Caddyfile and reload).

## 2026-10-09 (later): switched to standard ports, board added
- Gil ran the setcap; I swapped in `Caddyfile.443` (backup `Caddyfile.8443.bak`) and added a board site. Now: dashboard `https://192.168.8.136/` (443; `http://` 301-redirects), VJ `:8445`, router API `:8446`, board `:8447` (proxy to 127.0.0.1:8096). The old `:8443` and `:8081` listeners no longer exist. The reason Gil saw "invalid response" on the board: it is a separate plain-HTTP service on 8096 with no HTTPS front, so TLS to :8096 returns garbage.
- Gotcha: `systemctl --user restart magi-caddy` takes up to ~90 s because Caddy uses an eternal grace period and open browser streams (live WebRTC/SSE) hold it open until systemd times out; wait it out or use `caddy reload`.
- Told the board session (claude-projects-59) about: Board-tab link and VJ iframe must use https ports when the page is https; `BOARD_COOKIE_SECURE=1`; its login throttle sees all proxied visitors as 127.0.0.1 (needs the same trusted-proxy handling as the VJ app). Those files are theirs.
