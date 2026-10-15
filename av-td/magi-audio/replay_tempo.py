"""Replay magi:audio stats history through the OLD logic (as deployed) and the NEW TempoTracker."""
import json, sys, statistics
from collections import deque
from datetime import datetime
from tempo_track import TempoTracker

rows = []
for line in open(sys.argv[1]):
    s = json.loads(line.split(" ", 1)[1])
    s["t"] = datetime.fromisoformat(s["ts"]).timestamp()
    rows.append(s)

def old_logic(rows, bmin=90, bmax=180, conf_min=0.2, hold=10, suspect=25, warm=15):
    def fold(b):
        if b <= 0: return 0.0
        while b < bmin: b *= 2
        while b > bmax: b /= 2
        return b
    hist = deque(); last, last_t, cand, cand_since, started = None, 0, None, 0, rows[0]["t"]
    out = []
    for s in rows:
        now, active = s["t"], s["active"]
        conf = s["confidence"] if 0 <= s["confidence"] <= 2 else 0.0
        bpm = fold(s["bpm_raw"])
        if active and s["rms_db"] > -50 and conf >= conf_min and bpm > 0: hist.append((now, bpm))
        while hist and now - hist[0][0] > 8: hist.popleft()
        sm = statistics.median(b for _, b in hist) if len(hist) >= 4 else None
        if sm and conf >= conf_min and now - started >= warm and (last is None or abs(sm - last) > 0.5):
            if cand is None or abs(sm - cand) > 1.5: cand, cand_since = sm, now
            h = hold
            if last:
                ratio = max(sm, last) / min(sm, last)
                if any(abs(ratio - q) < 0.04 for q in (4 / 3, 3 / 2, 2.0)): h = suspect
            if now - cand_since >= h and now - last_t >= 3:
                out.append((now, sm)); last, last_t, cand = sm, now, None
        elif sm and last is not None and abs(sm - last) <= 0.5: cand = None
    return out

def new_logic(rows, **kw):
    tt = TempoTracker(**kw); out = []
    for s in rows:
        b = tt.update(s["t"], s["bpm_raw"], s["confidence"], s["rms_db"], s["active"])
        if b: out.append((s["t"], b))
    return out

def fmt(t): return datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S")
# split into sessions at gaps > 5 min of inactivity, restart state each time like a service restart
seg, segs = [], []
for s in rows:
    if seg and s["t"] - seg[-1]["t"] > 300: segs.append(seg); seg = []
    seg.append(s)
segs.append(seg)
o = n = 0
for sg in segs:
    a, b = old_logic(sg), new_logic(sg)
    o += len(a); n += len(b)
    if len(sg) < 60: continue
    print(f"--- segment {fmt(sg[0]['t'])} .. {fmt(sg[-1]['t'])} ({len(sg)} rows)")
    print("  OLD:", [(fmt(t)[6:], round(v, 1)) for t, v in a])
    print("  NEW:", [(fmt(t)[6:], round(v, 1)) for t, v in b])
print("total sends old", o, "new", n)
