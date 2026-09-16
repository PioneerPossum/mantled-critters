#!/bin/bash
# Mirrors the live Claude Code project memory into this repo's memory/ folder.
# Run this after memory updates, then review/commit/push manually.
set -euo pipefail
SRC="/home/sinewave/.claude/projects/-home-sinewave-Projects-claude-projects/memory/"
DST="$(dirname "$0")/memory/"
rsync -av --delete "$SRC" "$DST"
echo "Synced. Review with 'git status' / 'git diff' before committing."
