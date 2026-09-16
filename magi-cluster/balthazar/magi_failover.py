#!/usr/bin/env python3
"""magi-failover — liveness-triggered failover watchdog for magi-cluster.

Polls the PEER node's heartbeat keys in repositorium's Redis (written by
magi_heartbeat.py running there). If a peer's heartbeat for a role it
normally owns goes stale (missing or older than STALE_THRESHOLD_S), this
node starts that role locally (if not already running). When the peer's
heartbeat is healthy again for STABILIZATION_CHECKS consecutive polls, this
node stops the role again -- but ONLY a role it took over via failover,
never a role that's this node's own normal duty.

Liveness-only trigger (missed heartbeat). Not tamper/intrusion detection --
explicitly out of scope. Every failover transition is logged clearly via
the standard logger (systemd journal) so it's debuggable later.

--- Real-world constraint discovered 2026-09-16, baked into this design ---
On balthazar, `hailo-ollama.service` (Gil's day-to-day Open WebUI backend)
and hailo_apps-based tools (magi_detect.py / magi_llm_api.py, both using
Hailo's `SHARED_VDEVICE_GROUP_ID="SHARED"` mechanism) do NOT coexist on the
one Hailo-10H chip, despite both nominally supporting "shared" vdevice
access -- confirmed by testing both pairings directly: running
magi_llm_api.py while hailo-ollama was live crashed hailo-ollama outright
(SIGSEGV, mid-request) once real generation happened concurrently, and
starting magi_detect.py while hailo-ollama held the device failed
immediately with HAILO_OUT_OF_PHYSICAL_DEVICES. They are each individually
"shareable" but not WITH each other -- likely because hailo-ollama's binary
uses its own internal vdevice group-id scheme, not hailo_apps's constant.
So on balthazar, COUPLED_SERVICE below is stopped before starting ANY
failover role here, and restarted once no failover role is active anymore.
This does mean a real melchior outage takes Gil's Open WebUI down for the
duration (balthazar can't run hailo-ollama and vision/language failover at
the same time) -- an explicit, logged tradeoff, not a silent side effect.

Usage:
  python3 magi_failover.py            # run the daemon loop (systemd)
  python3 magi_failover.py ctl start vision    # manual override (dashboard)
  python3 magi_failover.py ctl stop  vision    # manual override (dashboard)
  python3 magi_failover.py ctl start language
  python3 magi_failover.py ctl stop  language
"""
import logging
import os
import subprocess
import sys
import time

import redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("magi_failover")

DEVICE = "balthazar"
PEER = "melchior"

# role -> {service: local systemd unit for this role,
#          own_duty: True if this node ALWAYS runs this role normally
#                     (never stopped by this watchdog, even after peer
#                     recovers -- only monitored/self-healed),
#          peer_key: the peer's heartbeat key for this role}
ROLES = {
    "vision":   {"service": "magi-live-detect", "own_duty": False, "peer_key": "magi:heartbeat:melchior:vision"},
    "language": {"service": "magi-llm-api",      "own_duty": False, "peer_key": "magi:heartbeat:melchior:language"},
}

# Service that must be stopped before this node can start ANY failover role,
# and restarted once none of them are active anymore. None on melchior (no
# such conflicting service exists there).
COUPLED_SERVICE = "hailo-ollama"

STALE_THRESHOLD_S = 90        # ~3-4 missed 15-20s heartbeats before acting
POLL_INTERVAL_S = 25
STABILIZATION_CHECKS = 2      # consecutive healthy polls before un-failing-over


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


def systemctl(action: str, unit: str) -> bool:
    try:
        subprocess.run(
            ["sudo", "systemctl", action, unit],
            capture_output=True, text=True, timeout=20, check=True,
        )
        logger.info("systemctl %s %s: ok", action, unit)
        return True
    except Exception as e:
        logger.error("systemctl %s %s FAILED: %s", action, unit, e)
        return False


def ensure_coupled_stopped():
    if COUPLED_SERVICE and is_active(COUPLED_SERVICE):
        logger.warning(
            "FAILOVER: stopping %s on %s to free the Hailo device for a "
            "failover role (known conflict, see module docstring)",
            COUPLED_SERVICE, DEVICE,
        )
        systemctl("stop", COUPLED_SERVICE)


def maybe_restore_coupled():
    """Ground-truth check (real systemctl state, not in-memory bookkeeping)
    so this is safe to call from both the daemon loop and a one-shot `ctl`
    invocation: only restarts COUPLED_SERVICE if NONE of this node's
    failover-role services are currently active."""
    if not COUPLED_SERVICE or is_active(COUPLED_SERVICE):
        return
    if any(is_active(cfg["service"]) for cfg in ROLES.values() if not cfg["own_duty"]):
        return
    logger.info(
        "FAILOVER RECOVERY: no failover roles active on %s anymore, "
        "restarting %s", DEVICE, COUPLED_SERVICE,
    )
    systemctl("start", COUPLED_SERVICE)


def start_role(role: str):
    cfg = ROLES[role]
    if is_active(cfg["service"]):
        logger.info("%s already active on %s, nothing to start", cfg["service"], DEVICE)
        return
    ensure_coupled_stopped()
    logger.warning(
        "FAILOVER: starting %s locally on %s (role=%s)", cfg["service"], DEVICE, role,
    )
    systemctl("start", cfg["service"])


def stop_role(role: str, started_by_failover: dict):
    cfg = ROLES[role]
    if cfg["own_duty"]:
        logger.info("Refusing to stop %s -- it is %s's own normal duty, not a failover role", cfg["service"], DEVICE)
        return
    if is_active(cfg["service"]):
        logger.warning(
            "FAILOVER RECOVERY: peer %s healthy again, stopping locally-started %s on %s",
            role, cfg["service"], DEVICE,
        )
        systemctl("stop", cfg["service"])
    started_by_failover[role] = False
    maybe_restore_coupled()


def peer_heartbeat_age(peer_key: str):
    """Returns seconds since the peer's heartbeat, or None if missing."""
    try:
        ts = r.get(peer_key)
    except Exception as e:
        logger.warning("Redis read failed for %s: %s", peer_key, e)
        return None
    if ts is None:
        return None
    try:
        return int(time.time()) - int(ts)
    except (TypeError, ValueError):
        return None


def run_daemon():
    logger.info(
        "magi-failover starting on %s (peer=%s, roles=%s, coupled=%s)",
        DEVICE, PEER, list(ROLES), COUPLED_SERVICE,
    )
    healthy_streak = {role: 0 for role in ROLES}
    started_by_failover = {role: False for role in ROLES}

    while True:
        for role, cfg in ROLES.items():
            age = peer_heartbeat_age(cfg["peer_key"])
            stale = age is None or age > STALE_THRESHOLD_S

            if cfg["own_duty"]:
                # Informational only -- this node already always runs this
                # role itself, so there's nothing to take over here. NOTE:
                # deliberately NOT self-healing an inactive own_duty service
                # from this loop -- that's systemd's Restart=on-failure job
                # (crash recovery only), and doing it here too would fight
                # any intentional/manual `systemctl stop` of that unit
                # (maintenance, or exactly the kind of outage simulation
                # this whole subsystem is meant to be tested with).
                if stale:
                    logger.info(
                        "Peer %s's %s heartbeat is stale (age=%s) -- no action needed, "
                        "%s already runs %s as its own normal duty",
                        PEER, role, age, DEVICE, role,
                    )
                continue

            if stale:
                healthy_streak[role] = 0
                if not is_active(cfg["service"]):
                    start_role(role)
                started_by_failover[role] = True
            else:
                healthy_streak[role] += 1
                if started_by_failover[role] and healthy_streak[role] >= STABILIZATION_CHECKS:
                    stop_role(role, started_by_failover)

        time.sleep(POLL_INTERVAL_S)


def run_ctl(action: str, role: str):
    if role not in ROLES:
        print(f"unknown role {role!r}, expected one of {list(ROLES)}", file=sys.stderr)
        sys.exit(2)
    if action == "start":
        start_role(role)
    elif action == "stop":
        # Manual force-clear: allowed even for own_duty roles is refused by
        # stop_role's own guard; pass a throwaway dict so it doesn't affect
        # any running daemon's in-memory state (separate process).
        stop_role(role, {role: True})
    else:
        print(f"unknown action {action!r}, expected start|stop", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "ctl":
        run_ctl(sys.argv[2], sys.argv[3])
    else:
        run_daemon()
