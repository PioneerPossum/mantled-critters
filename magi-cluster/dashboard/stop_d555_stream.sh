#!/bin/bash
# Stop the D555 -> melchior RTP stream sender on audire-videre.
pkill -f magi_stream_d555.py && echo "Stopped D555 stream sender." || echo "No sender was running."
