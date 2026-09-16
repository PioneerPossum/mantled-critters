#!/bin/bash
# On-demand stop for Open WebUI / hailo-ollama on balthazar -- reverse of
# start_openwebui.sh. Stops hailo-ollama and restarts magi-llm-api (balthazar's
# normal duty), via magi_failover.py's `ctl openwebui stop` (same reused
# coordination code as the start side).
python3 ~/magi/balthazar/magi_failover.py ctl openwebui stop
echo
echo "hailo-ollama stopped; magi-llm-api should be back to normal duty."
