#!/bin/bash
# Same fuser-based stop pattern as melchior — hailo_apps calls
# setproctitle() so pkill -f magi_detect.py silently misses the real process.
PIDS=$(sudo fuser /dev/hailo0 2>/dev/null)
if [ -n "$PIDS" ]; then
    sudo kill -9 $PIDS
    echo "Stopped process(es) holding /dev/hailo0: $PIDS"
else
    echo "No process was holding /dev/hailo0."
fi
