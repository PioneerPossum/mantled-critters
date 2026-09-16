#!/bin/bash
# Start streaming the D555's color feed to melchior as RTP/H264 over UDP.
# Usage: ./start_d555_stream.sh [melchior-ip] [port]
HOST="${1:-192.168.8.175}"
PORT="${2:-5000}"
pkill -f magi_stream_d555.py 2>/dev/null
sleep 1
nohup python3 /home/sinewave/magi_stream_d555.py --host "$HOST" --port "$PORT" \
    > /home/sinewave/d555_stream.log 2>&1 < /dev/null &
disown
sleep 1
echo "Started D555 stream sender -> rtp://$HOST:$PORT (pid $(pgrep -f magi_stream_d555.py | head -1))"
echo "Log: /home/sinewave/d555_stream.log"
