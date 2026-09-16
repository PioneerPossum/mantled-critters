#!/bin/bash
# Stop the live-camera magi_detect.py run cleanly. Plain `pkill -f magi_detect.py`
# is NOT reliable here: hailo_apps calls setproctitle() so the process shows up
# as "Hailo Detection App" in ps, not by its original argv/script name — a
# pattern-based pkill on the script name silently misses it, leaving it bound
# to /dev/hailo0 and the UDP port, blocking every subsequent run. Kill by
# whatever is actually holding the Hailo device instead.
PIDS=$(sudo fuser /dev/hailo0 2>/dev/null)
if [ -n "$PIDS" ]; then
    sudo kill -9 $PIDS
    echo "Stopped process(es) holding /dev/hailo0: $PIDS"
else
    echo "No process was holding /dev/hailo0."
fi
