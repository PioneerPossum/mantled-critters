#!/bin/bash
# On-demand start for Open WebUI / hailo-ollama on balthazar.
#
# balthazar's normal duty is magi-llm-api (the dashboard's tool-capable chat
# offload service) -- see ~/.config/magi/publish.env's OWN_DUTY_ROLES=language.
# hailo-ollama (Open WebUI's backend) and magi-llm-api cannot run concurrently
# on the one Hailo-10H chip (same conflict the vision failover coupling logic
# was built around -- see magi_failover.py's module docstring), so this stops
# magi-llm-api first, then starts hailo-ollama. Reuses magi_failover.py's own
# `ctl openwebui start` coordination code (same stop-the-other-side pattern
# already used for failover, just repurposed for a manual on-demand toggle)
# rather than duplicating the systemctl calls here.
#
# Open WebUI's own Docker container (`open-webui`) is NOT touched -- it's a
# separate resource (just a web UI hitting hailo-ollama's HTTP API) that
# isn't chip-conflicted, so it can stay running all the time.
python3 ~/magi/balthazar/magi_failover.py ctl openwebui start
echo
echo "Open WebUI should now be reachable (hailo-ollama active); magi-llm-api is stopped for the duration."
echo "Run stop_openwebui.sh when done to hand the chip back to magi-llm-api."
