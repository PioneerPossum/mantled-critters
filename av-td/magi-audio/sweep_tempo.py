"""Sweep big-jump adoption settings of TempoTracker over the stored readings (stream export)."""
import json, sys
from datetime import datetime
from tempo_track import TempoTracker

rows = []
for line in open(sys.argv[1]):
    s = json.loads(line.split(" ", 1)[1]); s["t"] = datetime.fromisoformat(s["ts"]).timestamp(); rows.append(s)
SUSPECT = (0.5, 2 / 3, 0.75, 4 / 3, 1.5, 2.0)
def run(**kw):
    tt = TempoTracker(**kw); out = []; segs = []; seg = []
    for s in rows:                                   # restart state across idle gaps (service restarts)
        if seg and s["t"] - seg[-1]["t"] > 300: segs.append(seg); seg = []
        seg.append(s)
    segs.append(seg)
    for sg in segs:
        tt = TempoTracker(**kw); prev = None
        for s in sg:
            b = tt.update(s["t"], s["bpm_raw"], s["confidence"], s["rms_db"], s["active"])
            if b: out.append((s["t"], b, prev)); prev = b
    return out
def hm(t): return datetime.fromtimestamp(t).strftime("%H:%M:%S")
cfgs = {"90s (deployed)": dict(jump_hold_s=90), "30s": dict(jump_hold_s=30), "30s conf>=.12": dict(jump_hold_s=30, jump_conf=0.12), "30s conf>=.18": dict(jump_hold_s=30, jump_conf=0.18), "20s conf>=.18": dict(jump_hold_s=20, jump_conf=0.18)}
for name, kw in cfgs.items():
    out = run(**kw)
    sus = [(t, b, p) for t, b, p in out if p and any(abs((b / p) - q) < 0.04 for q in SUSPECT)]
    big = [(t, b, p) for t, b, p in out if p and not 0.88 <= b / p <= 1.12]
    today = [(hm(t), round(b, 1)) for t, b, p in out if hm(t) >= "19:45:00" and t > datetime(2026, 10, 5).timestamp()]
    print(f"{name}: sends={len(out)} big_jumps={len(big)} suspect_ratio_sends={len(sus)}")
    print("   suspect:", [(hm(t), round(p, 1), round(b, 1)) for t, b, p in sus][:14])
    print("   today 19:45+:", today)
