#!/bin/bash
# Run melchior's real-detection publisher against the live D555 feed
# streamed from audire-videre, instead of the bundled sample video.
# Usage: ./start_live_detect.sh [udp-port]
PORT="${1:-5000}"
~/magi/melchior/stop_live_detect.sh
sleep 1
cd ~/hailo-apps && source setup_env.sh >/dev/null 2>&1
cd ~/magi/melchior
setsid nohup python3 -u magi_detect.py --input "udp://:${PORT}" \
    > /home/sinewave/magi_detect_live.log 2>&1 < /dev/null &
disown
sleep 2
echo "Started live-camera detection publisher, listening on udp://:${PORT}"
echo "Log: /home/sinewave/magi_detect_live.log"
ps aux | grep -i "hailo detection" | grep -v grep
