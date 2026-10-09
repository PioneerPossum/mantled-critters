---
name: magi-skywarn-goal
description: "Gil's future goal to have the weather tool pull NWS Mesoscale Discussions/watches/warnings (SKYWARN-style) for better near-term severe-weather forecasting, tied to subscribed users' locations"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-17T17:32:23.101Z
---

Suggested by Gil 2026-09-17, alongside a real example: an NWS Mesoscale Precipitation Discussion (MPD 1313, flash-flooding risk across northern IL/IN into northwestern OH) shown via a RadarScope Pro screenshot.

**The idea:** when a subscribed user is in or near an area with an active NWS watch/warning or mesoscale discussion, the weather tool (see [[magi-local-llm-feasibility]] for the existing weather tool, just fixed for a rain-consistency bug in Session 11) should factor that into its answer — pull the actual NWS mesoscale discussion / watch / warning text for the relevant area and use it to give a better near-term ("SKYWARN mode") forecast, not just the current Open-Meteo conditions snapshot it uses today.

**Data source, not yet researched:** NWS/NOAA publishes Mesoscale Discussions (from the Weather Prediction Center) and Watches/Warnings (from local WFOs) via public APIs — likely `api.weather.gov` (the modern NWS API, free, no key required) has both active alerts (by lat/lon or zone) and forecast discussion text. Worth checking whether mesoscale discussions specifically (a WPC product, not the same as SPC's severe weather outlooks/watches) are available through that same API or need a separate source (e.g. scraping `www.wpc.ncep.noaa.gov` text products).

**Explicitly tied to the not-yet-scoped Telegram bot/notification system** (see the magi-cluster Telegram bridge discussion) — the "subscribed user" framing means this is meant to trigger proactive alerts to opted-in users near an active weather threat, not just answer on-demand questions. Don't build this in isolation from that bot scope conversation, which Gil wanted to have separately before any building starts there.

**Status: idea stage, not started.** How to apply: revisit once the Telegram bot scope conversation happens and/or when doing further weather-tool work — check `api.weather.gov`'s alerts/discussion endpoints first before assuming a scraping approach is needed.
