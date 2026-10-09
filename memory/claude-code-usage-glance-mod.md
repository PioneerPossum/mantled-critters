---
name: claude-code-usage-glance-mod
description: Claude Code mod "usage-glance" (built 2026-10-04): condensed context/rate-limit/cost/agent-roster readout; band above prompt (terminal+desktop) and /glance pane (all surfaces incl. phone)
metadata:
  type: reference
---

Built 2026-10-04 in the session that asked for a readable usage + active/available-agents display on phone, desktop and CLI.

- **Location:** `/home/sinewave/.claude/dev-mods/1706d219-d055-4b6a-b37c-a203df863510/usage-glance/`
- **Surfaces:** `AbovePrompt` is raised on terminal and desktop ONLY (API doc); the phone gets the `Pane` (raised on every surface; mobile table has Box/Text/Button/Svg/Link/Code/Markdown, no Input/Select). So `/glance` opens the pane, `/glance-band` toggles the band.
- **Data:** `$.session.usage({breakdown:'summary'})` (context, rateLimits, cost) + `$.agent.list()` (counts by status) + `agent.offer` hook (available agent types, collected as the engine lists them).
- **Validated:** `claude plugin validate` passes, 4 unit tests pass. Not yet confirmed: live render, whether the phone app can run plugin slash commands, whether `agent.offer` fires often enough to fill the types list.
- Sibling of [[claude-code-context-bar-mod]] (the older stacked-bar mod); same `$`-only-in-top-level-function validator rule applies.
