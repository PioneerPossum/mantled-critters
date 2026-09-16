#!/usr/bin/env python3
"""magi-dashboard data API — runs on repositorium only.

Read-only JSON API over Postgres (events/devices) and Redis (the
magi:detections stream), for the magi-cluster dashboard hosted on
audire-videre to poll over the LAN. Credentials are read server-side from
~/.config/magi/worker.env (same file magi_worker.py already uses) and never
logged or returned in any response.

Binds to the LAN so audire-videre can reach it directly; there is nothing
sensitive in the responses (aggregate counts / recent detection events only,
no credentials, no raw DB access) so no auth layer was added for this home
dashboard — keep it that way; do not add write endpoints here.
"""
import os
import time

import psycopg2
import psycopg2.extras
import redis
from flask import Flask, jsonify

ENV_PATH = os.path.expanduser("~/.config/magi/worker.env")
STREAM_KEY = "magi:detections"


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


ENV = load_env(ENV_PATH)

app = Flask(__name__)


def pg_conn():
    return psycopg2.connect(
        host=ENV["PG_HOST"], dbname=ENV["PG_DB"],
        user=ENV["PG_USER"], password=ENV["PG_PASSWORD"],
        connect_timeout=5,
    )


def redis_conn():
    return redis.Redis(
        host=ENV["REDIS_HOST"], password=ENV["REDIS_PASSWORD"],
        decode_responses=True, socket_connect_timeout=5, socket_timeout=5,
    )


@app.get("/health")
def health():
    return jsonify(ok=True, ts=time.time())


@app.get("/api/events/recent")
def events_recent():
    limit = 25
    conn = pg_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            select e.id, d.name as device, e.event_type,
                   e.occurred_at, e.data
            from events e
            join devices d on d.id = e.device_id
            order by e.occurred_at desc
            limit %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        out = []
        for r in rows:
            data = r["data"] or {}
            out.append({
                "id": r["id"],
                "device": r["device"],
                "event_type": r["event_type"],
                "occurred_at": r["occurred_at"].isoformat(),
                "class": data.get("class"),
                "confidence": data.get("confidence"),
            })
        return jsonify(events=out)
    finally:
        conn.close()


@app.get("/api/events/stats")
def events_stats():
    conn = pg_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # totals + rate in last 60s / 5min
        cur.execute("select count(*) as total from events")
        total = cur.fetchone()["total"]

        cur.execute(
            "select count(*) as c from events where occurred_at > now() - interval '60 seconds'"
        )
        last_60s = cur.fetchone()["c"]

        cur.execute(
            "select count(*) as c from events where occurred_at > now() - interval '5 minutes'"
        )
        last_5m = cur.fetchone()["c"]

        cur.execute(
            """
            select d.name as device, e.data->>'class' as class, count(*) as c
            from events e join devices d on d.id = e.device_id
            where e.occurred_at > now() - interval '10 minutes'
              and e.event_type = 'detection'
            group by d.name, e.data->>'class'
            order by c desc
            limit 12
            """
        )
        by_class = cur.fetchall()

        cur.execute(
            """
            select d.name as device, count(*) as c
            from events e join devices d on d.id = e.device_id
            where e.occurred_at > now() - interval '10 minutes'
            group by d.name
            order by c desc
            """
        )
        by_device = cur.fetchall()

        return jsonify(
            total=total,
            last_60s=last_60s,
            last_5m=last_5m,
            rate_per_min=round(last_5m / 5.0, 1),
            by_class_10min=by_class,
            by_device_10min=by_device,
        )
    finally:
        conn.close()


@app.get("/api/stream/info")
def stream_info():
    r = redis_conn()
    info = {"stream": STREAM_KEY}
    try:
        info["length"] = r.xlen(STREAM_KEY)
    except redis.ResponseError:
        info["length"] = 0
        info["error"] = "stream does not exist yet"
        return jsonify(info)

    try:
        s = r.xinfo_stream(STREAM_KEY)
        info["last_generated_id"] = s.get("last-generated-id")
        info["first_entry_id"] = (s.get("first-entry") or [None])[0]
        info["last_entry_id"] = (s.get("last-entry") or [None])[0]
    except Exception as e:
        info["stream_info_error"] = str(e)

    try:
        groups = r.xinfo_groups(STREAM_KEY)
        info["groups"] = [
            {
                "name": g.get("name"),
                "consumers": g.get("consumers"),
                "pending": g.get("pending"),
                "lag": g.get("lag"),
                "last_delivered_id": g.get("last-delivered-id"),
            }
            for g in groups
        ]
    except Exception as e:
        info["groups"] = []
        info["groups_error"] = str(e)

    return jsonify(info)


# Which roles each node runs as its own permanent duty (never auto-stopped
# by magi_failover.py, even if it happens to be the one currently running
# it) -- must match ROLES[...]["own_duty"] in magi_melchior/magi_failover.py
# and magi_balthazar/magi_failover.py. See magi-failover.md.
FAILOVER_NORMAL_DUTY = {
    "melchior": {"vision", "language"},
    "balthazar": set(),  # balthazar's real day-to-day duty is hailo-ollama
                          # (not tracked as a heartbeat role); vision/language
                          # heartbeat keys existing for balthazar always mean
                          # it's actively covering for melchior.
}
FAILOVER_STALE_THRESHOLD_S = 90  # must match magi_failover.py's STALE_THRESHOLD_S


@app.get("/api/failover/status")
def failover_status():
    """Read-only view of magi:heartbeat:<device>:<role> keys (written by
    magi_heartbeat.py on melchior/balthazar) for the dashboard's failover
    panel. A missing key means the role isn't running there at all; a
    present-but-old key means the node stopped updating it (crashed/hung/
    network-partitioned) without cleanly stopping the service -- also
    surfaced as 'stale' so the dashboard can flag it even before the TTL
    expires the key outright."""
    r = redis_conn()
    now = int(time.time())
    out = {"stale_threshold_s": FAILOVER_STALE_THRESHOLD_S, "devices": {}}
    for dev, normal_duty in FAILOVER_NORMAL_DUTY.items():
        dev_out = {"roles": {}, "failover_active": False}
        for role in ("vision", "language"):
            key = f"magi:heartbeat:{dev}:{role}"
            try:
                ts = r.get(key)
            except Exception as e:
                dev_out["roles"][role] = {"error": str(e)}
                continue
            running = ts is not None
            age = (now - int(ts)) if running else None
            status = "stopped" if not running else ("healthy" if age <= FAILOVER_STALE_THRESHOLD_S else "stale")
            is_own_duty = role in normal_duty
            is_failover = running and not is_own_duty
            if is_failover:
                dev_out["failover_active"] = True
            dev_out["roles"][role] = {
                "running": running, "status": status, "age_s": age,
                "own_duty": is_own_duty, "failover": is_failover,
            }
        out["devices"][dev] = dev_out
    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8801, threaded=True)
