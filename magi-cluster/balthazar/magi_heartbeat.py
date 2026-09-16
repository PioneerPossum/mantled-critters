#!/usr/bin/env python3
"""magi-heartbeat — liveness beacon for magi-cluster failover.

Every INTERVAL_S seconds, writes `SET magi:heartbeat:<DEVICE>:<role> <unix_ts>
EX TTL_S` to repositorium's Redis for each role whose systemd unit is
currently active() on THIS node. Keys are left to expire naturally (TTL) the
moment a role's unit stops being active — no explicit delete needed, and a
crashed/hung node's keys age out on their own once the process (or the node
itself) stops updating them.

Read by magi_failover.py on the peer node (to detect a stale peer role and
take over) and by the dashboard (read-only, same Redis credentials, for a
status panel). Liveness only — this says nothing about tamper/intrusion,
that's explicitly out of scope for this subsystem.

Deploy note: DEVICE and ROLES below are hardcoded per node (same convention
as magi_detect.py's `DEVICE = "melchior"` / `"balthazar"` constant) rather
than derived from hostname, because the actual OS hostnames don't match the
magi-cluster node names (balthazar's real hostname is "dinky-pai").
"""
import logging
import os
import subprocess
import time

import redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("magi_heartbeat")

DEVICE = "balthazar"

# role name -> systemd unit that must be active() on this node for this
# node to currently be claiming the role (normal duty OR failover-started).
ROLES = {
    "vision": "magi-live-detect",
    "language": "magi-llm-api",
}

INTERVAL_S = 15
TTL_S = 60


def load_env(path):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


load_env(os.path.expanduser("~/.config/magi/publish.env"))
REDIS_HOST = os.environ["REDIS_HOST"]
REDIS_PASSWORD = os.environ["REDIS_PASSWORD"]

r = redis.Redis(host=REDIS_HOST, password=REDIS_PASSWORD, decode_responses=True)


def is_active(unit: str) -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() == "active"
    except Exception as e:
        logger.warning("systemctl is-active %s failed: %s", unit, e)
        return False


def main():
    logger.info("magi-heartbeat starting for device=%s roles=%s", DEVICE, list(ROLES))
    while True:
        now = int(time.time())
        for role, unit in ROLES.items():
            key = f"magi:heartbeat:{DEVICE}:{role}"
            try:
                if is_active(unit):
                    r.set(key, now, ex=TTL_S)
                # else: deliberately don't refresh -> key expires via TTL
                # once this role stops being active on this node.
            except Exception as e:
                logger.warning("Redis write failed for %s: %s", key, e)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
