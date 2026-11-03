#!/bin/bash
# Writes crowd-sense/include/secrets.h (mode 600) from ~/.secrets/ESP_WIFI_SSID.secret and ESP_WIFI_PASS.secret.
# Create those with:  ~/bin/set-secret ESP_WIFI_SSID   and   ~/bin/set-secret ESP_WIFI_PASS
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)/crowd-sense/include"
ssid=$(cat ~/.secrets/ESP_WIFI_SSID.secret); pass=$(cat ~/.secrets/ESP_WIFI_PASS.secret)
umask 077
cat > "$D/secrets.h" <<H
#pragma once
#define WIFI_SSID "$ssid"
#define WIFI_PASS "$pass"
#define CROWD_RX_HOST "${CROWD_RX_HOST:-192.168.8.184}"
#define CROWD_RX_PORT ${CROWD_RX_PORT:-7020}
#define DEVICE_ID "${DEVICE_ID:-xiao-1}"
H
echo "wrote $D/secrets.h (600)"
