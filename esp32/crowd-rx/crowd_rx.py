#!/usr/bin/env python3
"""Receive crowd-sense datagrams (UDP JSON) and publish them to Redis.

Listens on UDP CROWD_PORT (default 7020). Accepts only packets from CROWD_ALLOW (a /24 prefix, default
192.168.8.) that parse as the v1 schema with sane ranges; anything else is dropped and counted.
Per packet: XADD magi:crowd (maxlen ~20000) and SET magi:crowd:latest (ex 10) with a rolling summary
(max over the last second) so mood.py can read one key like it does for magi:audio:latest.
Usage: crowd_rx.py [--dry]    (--dry prints instead of writing to Redis)
Credentials: ~/.config/magi/worker.env (same as magi-audio). Nothing here stores images or audio.
"""
import argparse, json, os, socket, time
from collections import deque

PORT = int(os.environ.get("CROWD_PORT", "7020"))
ALLOW = os.environ.get("CROWD_ALLOW", "192.168.8.")
MAX_LEN = 600
GRID = 12

def load_env(path):
    try:
        for line in open(os.path.expanduser(path)):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass

def num(x, lo, hi):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and lo <= x <= hi

def validate(raw):
    """Return a cleaned dict or None."""
    try:
        d = json.loads(raw)
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("v") != 1:
        return None
    dev = d.get("id")
    if not isinstance(dev, str) or not (1 <= len(dev) <= 24) or not dev.replace("-", "").replace("_", "").isalnum():
        return None
    cells, bands = d.get("cells"), d.get("bands")
    ok = (num(d.get("motion"), 0, 1) and num(d.get("lux"), 0, 1) and num(d.get("mic_db"), -130, 10)
          and isinstance(cells, list) and len(cells) == GRID and all(num(c, 0, 255) for c in cells)
          and isinstance(bands, list) and len(bands) == 3 and all(num(b, 0, 10) for b in bands))
    if not ok:
        return None
    return {"id": dev, "ms": int(d.get("ms", 0)) if num(d.get("ms", 0), 0, 2**40) else 0,
            "motion": float(d["motion"]), "lux": float(d["lux"]), "cells": [int(c) for c in cells],
            "mic_db": float(d["mic_db"]), "bands": [float(b) for b in bands],
            "cam": 1 if d.get("cam") else 0, "rssi": int(d["rssi"]) if num(d.get("rssi", 0), -127, 0) else 0}

class Rolling:
    """Max motion / mic level per device over the last second, so a single quiet frame does not hide activity."""
    def __init__(self, win=1.0): self.win, self.q = win, {}
    def add(self, rec, now):
        q = self.q.setdefault(rec["id"], deque()); q.append((now, rec))
        while q and now - q[0][0] > self.win: q.popleft()
    def summary(self, now):
        out = {}
        for dev, q in self.q.items():
            recs = [r for t, r in q if now - t <= self.win]
            if not recs: continue
            out[dev] = {"motion_max": max(r["motion"] for r in recs), "mic_db_max": max(r["mic_db"] for r in recs),
                        "lux": recs[-1]["lux"], "cells_max": [max(r["cells"][i] for r in recs) for i in range(GRID)],
                        "n": len(recs), "cam": recs[-1]["cam"], "rssi": recs[-1]["rssi"]}
        return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry", action="store_true"); a = ap.parse_args()
    r = None
    if not a.dry:
        import redis
        load_env("~/.config/magi/worker.env")
        r = redis.Redis(host=os.environ["REDIS_HOST"], password=os.environ["REDIS_PASSWORD"], decode_responses=True)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", PORT)); s.settimeout(1.0)
    roll, dropped, good, last_log = Rolling(), 0, 0, time.time()
    print(f"crowd_rx listening on UDP {PORT}, allow {ALLOW}*, {'DRY' if a.dry else 'redis'}", flush=True)
    while True:
        now = time.time()
        try:
            data, addr = s.recvfrom(MAX_LEN + 1)
        except socket.timeout:
            data = None
        if data is not None:
            rec = validate(data) if (addr[0].startswith(ALLOW) and len(data) <= MAX_LEN) else None
            if rec is None:
                dropped += 1
            else:
                good += 1; rec["ts"] = round(now, 3); roll.add(rec, now)
                if a.dry: print(json.dumps(rec), flush=True)
                else:
                    r.xadd("magi:crowd", {"json": json.dumps(rec)}, maxlen=20000, approximate=True)
                    r.set("magi:crowd:latest", json.dumps({"ts": round(now, 3), "devices": roll.summary(now)}), ex=10)
        if now - last_log > 60:
            print(f"{time.strftime('%H:%M:%S')} ok={good} dropped={dropped}", flush=True); last_log = now

if __name__ == "__main__":
    main()
