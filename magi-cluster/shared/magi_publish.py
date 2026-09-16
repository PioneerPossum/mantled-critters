#!/usr/bin/env python3
"""Reusable helper for publishing a detection/event from any Hailo box into
the magi-cluster's Redis stream. Import publish_event() from real inference
code once a camera pipeline exists; run this file directly to send one
synthetic test event."""
import json
import os
import sys
import redis


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
STREAM = "magi:detections"

_r = redis.Redis(host=REDIS_HOST, password=REDIS_PASSWORD, decode_responses=True)


def publish_event(device: str, event_type: str, data: dict, embedding: list | None = None):
    fields = {
        "device": device,
        "event_type": event_type,
        "data": json.dumps(data),
    }
    if embedding is not None:
        fields["embedding"] = json.dumps(embedding)
    msg_id = _r.xadd(STREAM, fields)
    return msg_id


if __name__ == "__main__":
    device = sys.argv[1] if len(sys.argv) > 1 else "melchior"
    msg_id = publish_event(
        device,
        "detection",
        {"class": "test_person", "confidence": 0.87, "bbox": [10, 20, 100, 200]},
    )
    print(f"published test event {msg_id} from {device}")
