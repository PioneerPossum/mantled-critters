---
name: feedback-dated-note-before-ending
description: "Gil's standing rule (2026-10-09): a session that did real work writes a dated memory note before it ends; the Stop hook then covers the board side; how it is enforced and how to opt out"
metadata:
  node_type: memory
  type: feedback
---

**Rule (Gil, 2026-10-09):** before a session ends, it writes a dated memory note: a `## YYYY-MM-DD ...` section appended to the relevant memory note (or a new note with an index line). The Stop hook then makes sure the board side is covered ([[magi-board]], [[feedback-board-auto-update]]).
**Why:** memory is how the next session knows what happened; sessions here are long, parallel (forks and peers) and often end without anyone recording the outcome. Gil wants that guaranteed, not remembered.
**How to apply:**
- Applies to sessions that did real work (file edits, state-changing commands, a finding or fix). Pure Q&A or reading needs no note. Memory stays append-only; never write a secret value.
- Order: dated note first, then `~/bin/magi-board card|move|tick|log` (or `ack`), then `~/bin/magi-board check`.
- Enforcement is in code, not memory: the project Stop hook (`.claude/settings.json` -> `magi-board hook-stop`, logic in `magi-board/stop_hook.py`) blocks the stop ONCE with up to four numbered reasons: NOTE (work but no dated note this session), BOARD (memory newer than board), MEMORY (index/frontmatter errors), SYNC (board data exposed to a public location). It detects "work" from the session transcript's tool calls (Write/Edit outside memory and /tmp, and state-changing Bash like ssh/systemctl/mv/rm/sed -i/git commit/magi-board writes) and "dated note" as a memory note modified since the session began that contains a date from the session's span. Fails open on any error; `stop_hook_active` always lets it stop, so it cannot loop.
- Opt out for one session with nothing worth recording: `~/bin/magi-board note-skip --session <session id> "reason"` (the hook's message prints the id). Global kill switch: `touch ~/Projects/claude-projects/magi-board/data/hook.disabled`.
- The rule is also in the project's `~/Projects/claude-projects/CLAUDE.md`, so every session in this project loads it.
