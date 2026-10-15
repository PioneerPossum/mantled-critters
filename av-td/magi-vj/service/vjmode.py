#!/usr/bin/env python3
"""VJ mode + safety helper (runs on repositorium next to mood.py).
  vjmode.py get | set auto|assist|manual | preflight | settle | report [SECS]
preflight: Redis answers and the audio analyzer is fresh (<20 s). Does NOT touch the mode.
settle:    ExecStopPost hook; on a CLEAN stop (SERVICE_RESULT=success) hands control back (mode=manual).
           After a crash (result != success) it leaves the mode alone so systemd's restart carries on.
report:    decisions logged by the gateway in the last SECS seconds (default 60), counted by decision."""
import json, os, sys, time
from collections import Counter
import redis

def r():
    for l in open(os.path.expanduser("~/.config/magi/worker.env")):
        l = l.strip()
        if "=" in l and not l.startswith("#"):
            k, v = l.split("=", 1); os.environ.setdefault(k, v)
    return redis.Redis(host=os.environ["REDIS_HOST"], password=os.environ["REDIS_PASSWORD"], decode_responses=True)

def main():
    a = sys.argv[1:] or ["get"]; c = r()
    if a[0] == "get":
        print(c.get("magi:vj:mode") or "manual")
    elif a[0] == "set" and len(a) == 2 and a[1] in ("auto", "assist", "manual"):
        c.set("magi:vj:mode", a[1]); print("mode ->", a[1])
    elif a[0] == "preflight":
        c.ping(); raw = c.get("magi:audio:latest")
        if not raw: sys.exit("preflight: no magi:audio:latest (magi-audio down?)")
        st = json.loads(raw)
        age = time.time() - time.mktime(time.strptime(st["ts"], "%Y-%m-%dT%H:%M:%S"))
        if age > 20: sys.exit(f"preflight: audio analysis is stale ({age:.0f} s old)")
        print(f"preflight ok: mode={c.get('magi:vj:mode') or 'manual'} audio age {age:.0f}s active={st.get('active')} bpm={st.get('bpm')}")
    elif a[0] == "settle":
        if os.environ.get("SERVICE_RESULT", "success") == "success":
            c.set("magi:vj:mode", "manual"); print("clean stop: mode -> manual")
        else:
            print("service failed: mode left as is for the restart")
    elif a[0] == "report":
        secs = float(a[1]) if len(a) > 1 else 60
        lo = f"{int((time.time() - secs) * 1000)}-0"
        n = Counter(); last = None
        for _, e in c.xrange("magi:vj:actions", lo, "+"):
            d = json.loads(e["j"]); n[d["decision"]] += 1; last = d
        print(f"last {secs:.0f}s: " + (", ".join(f"{k}={v}" for k, v in sorted(n.items())) or "no actions"))
        if last: print("latest:", last["decision"], last["addr"], last["value"], "|", last["reason"], "| mode", last["mode"], "| live", last["live"])
    else:
        sys.exit(__doc__)
main()
