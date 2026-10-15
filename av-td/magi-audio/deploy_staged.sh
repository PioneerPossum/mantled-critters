#!/bin/bash
# Deploy the staged magi-audio update (faster tempo adoption + beat emitter, beat OFF by default).
# Rollback: ~/magi/audio/*.bak-20261006 (made here) -> copy back and restart.
set -euo pipefail
ssh repositorium 'cd ~/magi/audio && for f in magi_vban_audio.py tempo_track.py; do cp -n $f $f.bak-20261006; done
  cp staged/beat_emit.py staged/magi_vban_audio.py staged/tempo_track.py . && python3 -m py_compile beat_emit.py magi_vban_audio.py tempo_track.py
  sudo systemctl restart magi-audio && sleep 5 && journalctl -u magi-audio -n 8 --no-pager'
