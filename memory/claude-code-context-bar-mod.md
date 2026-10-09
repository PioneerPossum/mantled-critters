---
name: claude-code-context-bar-mod
description: "Claude Code mod 'context-bar' (built 2026-10-02): a stacked /context-colored usage bar above the prompt, toggled with /context-bar; where it lives and the plugin-validator gotcha hit while building it"
metadata:
  node_type: memory
  type: reference
  originSessionId: 386af2e4-e1b9-47fc-b2e9-293f7280d9ca
  modified: 2026-10-03T15:38:16.347Z
---

Built 2026-10-02 in the session named "context window visualization mod".

- **Location:** `/home/sinewave/.claude/dev-mods/386af2e4-e1b9-47fc-b2e9-293f7280d9ca/context-bar/`
- **What it does:** a stacked bar above the prompt, colored the same way as `/context`'s categories, showing context-window usage by category.
- **Toggle:** `/context-bar`.
- **Refresh:** once per turn, via `$.session.usage({breakdown:'summary'})`.

**Lesson (plugin validator):** the validator only accepts `$` passed to **top-level function declarations**. A `const` arrow helper defined inside `register` that takes or closes over `$` is rejected. Write helpers that need `$` as top-level `function name($, ...) {}` declarations and call them from `register`.

Related: [[lessons-learned]] (Claude Code tooling section), [[session-execution-model]].
