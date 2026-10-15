#!/usr/bin/env python3
"""Control-state helper for the VJ interaction page; runs on repositorium next to Redis.
Usage: vjctl.py export <t0> <t1> | feedback <json> <who> | state | mode <auto|assist|manual> <who> | lock <pattern> <who> | unlock <pattern> <who>
Prints JSON. Every change is appended to the Redis stream magi:vj:control (audit trail)."""
import json, os, sys, time
import redis

for l in open(os.path.expanduser("~/.config/magi/worker.env")):
    l = l.strip()
    if "=" in l and not l.startswith("#"):
        k, v = l.split("=", 1); os.environ.setdefault(k, v)
r = redis.Redis(host=os.environ["REDIS_HOST"], password=os.environ["REDIS_PASSWORD"], decode_responses=True)

def audit(kind, detail, who):
    r.xadd("magi:vj:control", {"j": json.dumps({"t": time.time(), "kind": kind, "detail": detail, "who": who})},
           maxlen=5000, approximate=True)

def suggestions(n=30):
    """Assist-mode proposals (decision == 'proposed'), newest first. They are rare compared with the macro stream,
    so scan a wide window but only parse entries whose raw text mentions 'proposed'."""
    out = []
    for _id, e in r.xrevrange("magi:vj:actions", count=8000):
        if '"proposed"' not in e["j"]:
            continue
        try:
            a = json.loads(e["j"])
        except Exception:
            continue
        if a.get("decision") == "proposed":
            out.append({k: a.get(k) for k in ("t", "addr", "value", "tier", "reason", "mode", "bpm")})
            if len(out) >= n:
                break
    return out

def crowd(window=60):
    """Reactions in the last `window` s: counts by kind and source, plus the newest few."""
    now = time.time(); lo = str(int((now - window) * 1000))
    counts = {"operator": {}, "crowd": {}}; recent = []
    for _id, e in r.xrevrange("magi:vj:feedback", max="+", min=lo, count=400):
        try:
            x = json.loads(e["j"])
        except Exception:
            continue
        if x.get("kind") != "reaction":
            continue
        src = "operator" if x.get("source") == "operator" else "crowd"
        counts[src][x.get("reaction")] = counts[src].get(x.get("reaction"), 0) + 1
        if len(recent) < 8:
            recent.append({k: x.get(k) for k in ("t", "reaction", "source", "weight")})
    pos = sum(v for k, v in counts["crowd"].items() if k in ("like", "more"))
    neg = sum(v for k, v in counts["crowd"].items() if k in ("dislike", "less"))
    return {"window_s": window, "counts": counts, "recent": recent, "sentiment": round((pos - neg) / (pos + neg), 2) if pos + neg else 0.0}

def state():
    acts = []
    for _id, e in r.xrevrange("magi:vj:actions", count=40):
        try:
            a = json.loads(e["j"])
            acts.append({k: a.get(k) for k in ("t", "addr", "value", "tier", "decision", "reason", "live", "source")})
        except Exception:
            pass
    ctl = []
    for _id, e in r.xrevrange("magi:vj:control", count=15):
        try: ctl.append(json.loads(e["j"]))
        except Exception: pass
    try: audio = json.loads(r.get("magi:audio:latest") or "null")
    except Exception: audio = None
    return {"ok": True, "now": time.time(), "mode": r.get("magi:vj:mode") or "manual",
            "mode_set": r.get("magi:vj:mode") is not None, "locks": sorted(r.smembers("magi:vj:locks")),
            "audio": audio, "tempo_sent": r.get("magi:audio:tempo_sent"), "actions": acts, "control": ctl,
            "suggestions": suggestions(30), "crowd": crowd(60)}

def export(t0, t1):
    lo, hi = str(int(t0 * 1000)), str(int(t1 * 1000))
    acts = []
    for _id, e in r.xrange("magi:vj:actions", min=lo, max=hi):
        try: acts.append(json.loads(e["j"]))
        except Exception: pass
    audio = []
    for _id, e in r.xrange("magi:audio", min=lo, max=hi):
        try:
            s = json.loads(e["json"]); audio.append({k: s.get(k) for k in ("ts", "active", "bpm_raw", "bpm", "bpm_smooth", "confidence", "rms_db", "peak_db")})
        except Exception: pass
    fb = []
    for _id, e in r.xrange("magi:vj:feedback", min=lo, max=hi):
        try: fb.append(json.loads(e["j"]))
        except Exception: pass
    return {"ok": True, "actions": acts, "audio": audio, "feedback": fb}

def main():
    a = sys.argv[1:]
    if a and a[0] == "export":
        print(json.dumps(export(float(a[1]), float(a[2])))); return
    if a and a[0] == "feedback":
        rec = json.loads(a[1]); rec["who"] = a[2]; rec.setdefault("t", time.time())
        r.xadd("magi:vj:feedback", {"j": json.dumps(rec)}, maxlen=5000, approximate=True)
        print(json.dumps({"ok": True})); return
    if not a or a[0] == "state":
        pass
    elif a[0] == "mode" and len(a) >= 3 and a[1] in ("auto", "assist", "manual"):
        old = r.get("magi:vj:mode") or "manual"; r.set("magi:vj:mode", a[1]); audit("mode", f"{old} -> {a[1]}", a[2])
    elif a[0] in ("lock", "unlock") and len(a) >= 3 and a[1].startswith("/composition/"):
        (r.sadd if a[0] == "lock" else r.srem)("magi:vj:locks", a[1]); audit(a[0], a[1], a[2])
    else:
        print(json.dumps({"ok": False, "error": "bad args"})); sys.exit(2)
    print(json.dumps(state()))

main()
