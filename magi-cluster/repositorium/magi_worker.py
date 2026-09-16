#!/usr/bin/env python3
"""Consumes detection events from the Redis stream 'magi:detections' and
persists them into the magi_memory Postgres database. Runs on repositorium,
co-located with both services, as the magi-worker systemd service."""
import json
import os
import redis
import psycopg2


def load_env(path):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


load_env(os.path.expanduser("~/.config/magi/worker.env"))

REDIS_HOST = os.environ["REDIS_HOST"]
REDIS_PASSWORD = os.environ["REDIS_PASSWORD"]
PG_DSN = (
    f"host={os.environ['PG_HOST']} dbname={os.environ['PG_DB']} "
    f"user={os.environ['PG_USER']} password={os.environ['PG_PASSWORD']}"
)

STREAM = "magi:detections"
GROUP = "magi_workers"
CONSUMER = "worker1"

r = redis.Redis(host=REDIS_HOST, password=REDIS_PASSWORD, decode_responses=True)
conn = psycopg2.connect(PG_DSN)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT id, name FROM devices")
device_ids = {name: did for did, name in cur.fetchall()}
print("known devices:", device_ids, flush=True)

try:
    r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    print(f"created consumer group {GROUP}", flush=True)
except redis.exceptions.ResponseError as e:
    if "BUSYGROUP" not in str(e):
        raise
    print(f"consumer group {GROUP} already exists", flush=True)

print(f"listening on {STREAM} as {CONSUMER}...", flush=True)
while True:
    resp = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000)
    if not resp:
        continue
    for _stream, messages in resp:
        for msg_id, fields in messages:
            try:
                device_name = fields["device"]
                device_id = device_ids.get(device_name)
                if device_id is None:
                    print(f"unknown device {device_name!r}, skipping {msg_id}", flush=True)
                    r.xack(STREAM, GROUP, msg_id)
                    continue
                event_type = fields["event_type"]
                data = fields.get("data", "{}")
                embedding = fields.get("embedding")
                if embedding:
                    emb_list = json.loads(embedding)
                    cur.execute(
                        "INSERT INTO events (device_id, event_type, data, embedding) "
                        "VALUES (%s, %s, %s::jsonb, %s::vector)",
                        (device_id, event_type, data, emb_list),
                    )
                else:
                    cur.execute(
                        "INSERT INTO events (device_id, event_type, data) "
                        "VALUES (%s, %s, %s::jsonb)",
                        (device_id, event_type, data),
                    )
                r.xack(STREAM, GROUP, msg_id)
                print(f"persisted {msg_id} from {device_name} ({event_type})", flush=True)
            except Exception as e:
                print(f"error processing {msg_id}: {e}", flush=True)
