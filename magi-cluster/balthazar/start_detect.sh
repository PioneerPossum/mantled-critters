#!/bin/bash
# Start balthazar's detection publisher (bundled sample video — no live
# camera bridge exists on balthazar yet). Mirrors melchior's
# start_live_detect.sh control pattern for dashboard parity.
~/magi/balthazar/stop_detect.sh
sleep 1
cd ~/hailo-apps && source setup_env.sh >/dev/null 2>&1
cd ~/magi/balthazar
setsid nohup python3 -u magi_detect.py \
    > /home/sinewave/magi_detect_live.log 2>&1 < /dev/null &
disown
sleep 2
echo "Started balthazar detection publisher (sample video)."
echo "Log: /home/sinewave/magi_detect_live.log"
ps aux | grep -i "hailo detection" | grep -v grep
