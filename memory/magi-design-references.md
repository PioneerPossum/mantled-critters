---
name: magi-design-references
description: "Gil's 9 Evangelion MAGI/NERV reference screenshots (sets 1+2) and the design decisions derived from them for the MAGI dashboard Claude Design canvas"
metadata:
  node_type: memory
  type: project
  originSessionId: 7c56e99d-afba-524d-bb78-a1dfbc1c104d
  modified: 2026-09-29T01:49:12.450Z
---

Gil supplied two reference sets (5 + 4 images, 2026-09-28) for the NERV/MAGI dashboard revamp in Claude Design ([[magi-router]], [[magi-remote-worker-plan]]). Images live in the session dir (`/tmp/claude-1000/.../images/`, ephemeral), so the descriptions below are the durable record.

**Set 1**
- MAGI nodes as irregular pentagon panels. Two looks: outlined neon teal with a boxed 承認 (red-outlined 否定 for dissent), and solid mint/sky-blue fills with heavy black condensed text.
- Silhouette: BALTHASAR-2 on top (tall, pointing down), CASPER-3 as a left slab with clipped upper-right corner, MELCHIOR-1 as a right slab with clipped upper-left corner, small center "MAGI" hex joined by orange connector bars.
- Stacked orange readout: CODE / FILE:MAGI_SYS / EXTENTION / EX_MODE / PRIORITY. Rounded orange "RESULT OF THE DELIBERATION" banner, double-ruled kanji banners, "ALL GREEN 終了" badge.
- Tokyo-3 defense-line map (orange polyomino clusters, numbered cells, green-yellow-orange gradient fills). TYPE-B DEFENSE concentric-ring display with corner readout/timer boxes.

**Set 2**
- Data-flow schematic: slanted mint bars labeled "SYNC" in stacked columns, thin orange wiring lines, small orange tick tabs, tiny address codes (MT-01365, A0136, GTC01001010).
- Deliberation screen: orange outline frame, 提訴 / 決議 headers, CODE:132, FILE:MAGI_SYS, EXTENTION:2048, EX_MODE:ON, PRIORITY:A, red-boxed 審議中 "deliberating" stamp. Circle ring with serif "MAGI" center. Three tilted squares: BALTHASAR 2 (top, mint), CASPER 3 (tilted mint), MELCHIOR (black outlined, empty = pending/not yet voted).
- EMERGENCY screen: red/black, orange-black hazard stripes, 非常事態 EMERGENCY, CODE: 102, MAGI hex with red CASPER 3 / MELCHIOR 1 panels, ACTIVE TIME DISPLAY, PSYCHOGRAPHIC DISPLAY header.
- Composite screen: big seven-segment ACTIVE TIME DISPLAY clock (22:09:36.432) with STOP / SLOW / NORMAL / RACING selector (NORMAL lit red) and 外部 EXTERNAL badge on a red→yellow→green gradient. PSYCHOGRAPHIC DISPLAY graph (link lines + waveform, axis -60..+60). Blue-pentagon MAGI with CODE:239. MENTAL TOXICITY LEVEL bar meters for subjects 00/01/02 with [CAUTION]/[DANGER] brackets, ELAPSED TIME, L.C.L. PURITY 99.999989%.

**Design decisions to apply (planned, not yet built)**
- Rebuild hero with the real MAGI silhouette (BALTHASAR top, CASPER bottom-left, MELCHIOR bottom-right, center MAGI hex, orange bars) instead of the current horizontal 3-hex row. Solid mint fill = served, outline = skipped/down/not in chain, red = failed, empty black outline = pending. A 審議中 stamp shows while a request is in flight.
- Palette shift: redder orange (~#ff5a1f) plus mint/teal primary data color; red for alert; gradient bar for the load/latency gauge.
- Header readout mapped to router data: CODE = request id, FILE/EXTENTION = class/tokens, EX_MODE = auto/local/cloud, PRIORITY = tier. "RESULT OF THE DELIBERATION" banner = final verdict.
- Emergency variant: whole UI flips to red/hazard-stripe 非常事態 mode when the full escalation chain is down (a real case in the sample history).
- Bar meters (mental-toxicity style) for per-backend latency/load; seven-segment clock and STOP/SLOW/NORMAL/RACING selector for router mode or load state; concentric-ring radar for the tier ladder (NPU→GPU→cloud); numbered-cell defense map for the node/worker cluster; SYNC-bar column schematic for the failover/data-flow panel.

**Current state (2026-09-29):** redesign approved and published (v23) at https://claude.ai/artifact/1QxhvHfTHkp9p3CsiUipEJ with Main, Phone, Components, Console, Motion boards. Translated to the live Flask frontend at `~/magi_dashboard/static/nerv.{html,css,js}`, served at `/static/nerv.html`. `/` still serves the legacy index.html (backup `index.html.bak.20260929_pre-nerv`); switching `/` in app.py is pending Gil's OK. Verified via file:// render with sample data (headless chromium here hangs on http URLs, so test that way).
