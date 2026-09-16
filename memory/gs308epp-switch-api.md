---
name: gs308epp-switch-api
description: "How to script changes against the Netgear GS308EPP switch on the magi-cluster's Netgear network (login scheme, packed-JS unpacking, CSRF hash token, write endpoints)"
metadata: 
  node_type: memory
  type: reference
  originSessionId: e5718790-74ec-4313-bedc-feb387a7b25c
  modified: 2026-09-14T22:48:14.342Z
---

The Netgear GS308EPP PoE++ switch on edgerunner's network segment (see [[magi-cluster-status]]) has a web UI that can be fully scripted once you know its quirks. Reverse-engineered 2026-09-14.

**Login:** `GET /login.cgi` to scrape a `rand` value from a hidden input, then `POST /login.cgi` with `password=<merge_hash_md5(real_password, rand)>`. The `python-netgear-switch-library` pip package (installed in a venv at `~/nsdp-venv` on edgerunner) already implements `merge_hash_md5` and `parse_login_rand` in `netgear_switch.protocols.http.crypt`/`.parse` — reuse those rather than reimplementing.

**The library's built-in model spec for this switch family (`gs305ep`) is close but not exact** — GS308EPP's `dashboard.cgi` HTML doesn't match the library's parser (different port-table structure), so `ngsw` CLI reads fail. Don't rely on the CLI for this exact model; talk to the raw endpoints instead.

**Every write requires a fresh CSRF-style `hash` token**, NOT a static per-session value: `GET /dashboard.cgi` (authenticated) and scrape `<input ... id='hash' value="...">` — it appears to be reissued on each dashboard load. Missing or stale hash → response body literally `"CHECK HASH FAILED"`. This was the blocker that took the longest to find; the actual field name guessing (`ip_address`, `subnet_mask`, etc.) was correct on the first try, the hash was the missing piece.

**Session limit is very low** — a handful of logins in quick succession (e.g. several separate script runs each doing their own login without `/logout.cgi`) triggers `"The maximum number of sessions has been reached. Wait a few minutes and then try again."` on the login page. In practice it took roughly 10 minutes of no further attempts to clear. **Always reuse one `httpx.Client()` session across multiple reads/writes in a single script rather than logging in per-action.**

**Static assets vs. dynamic content:** most of the UI's real logic lives in *packed* JS (`page.js`, `page2.js`, `function.js`, `function2.js`, `en.js` — only served once authenticated) using a custom LZ77-style packer with backtick (`` ` ``) as the escape byte: read 4 bytes at the escape (`` ` c1 c2 c3 ``), `length = ord(c3) - 28`; if `length > 4` it's a back-reference `start = current_length - (ord(c1)*96 + ord(c2)) + 3104 - length`, copy `length` chars from there; else it's a literal escaped backtick. A working Python unpacker exists in this session's history — reconstruct it the same way rather than trying to eval the JS (no Node.js is installed on any of these boxes).

**Known write endpoints (POST, `hash=` always first param):**
- `/ip_dhcp.cgi` — `hash`, `dhcpMode` (0/1), `ip_address`, `subnet_mask`, `gateway_address`, `dns_primary`, `dns_secondary`.
- `/port_status.cgi` — `hash`, `port<N>=checked`, `SPEED` (1=Auto), `FLOW_CONTROL` (1=on/2=off), `DESCRIPTION` (URL-encoded port label text), `IngressRate`/`EgressRate` (1=No Limit), `priority` (0=Low/default). Read current values first from `dashboard.cgi`'s hidden `class="Speed"/"flowCtr"/"ingressRate"/"egressRate"` fields per port-block so you don't clobber existing config.
- The real app shell (with the actual JS `<script src>` list) is at `/index.cgi`, not `/` (root just redirects there via inline JS). Static files not referenced from the login page (e.g. guessed names like `main.js`) all silently return a 200 OK generic stub (303 bytes) — HTTP 200 does NOT mean the file exists on this device; compare content length/content against a known-bad control to tell real files from the stub.

Full session transcript (this account, 2026-09-14) has the working Python for login + unpack + the port-label and DHCP-fix scripts if reconstructing from scratch is needed.
