"""Feed a 120 BPM click track through aubio + BeatClock exactly as magi_vban_audio does; report beat spacing."""
import aubio, numpy as np
from beat_emit import BeatClock
RATE, HOP, WIN = 48000, 512, 1024
bpm = 120; n = RATE * 40
x = np.zeros(n, np.float32); period = int(RATE * 60 / bpm)
for i in range(0, n, period):
    x[i:i + 400] = np.hanning(400) * 0.9 * np.sin(2 * np.pi * 1000 * np.arange(400) / RATE)
    if (i // period) % 4 == 0: x[i:i + 400] *= 1.0
    else: x[i:i + 400] *= 0.35
t = aubio.tempo("default", WIN, HOP, RATE); clock = BeatClock(RATE); ts = []; fake_now = 1000.0
for k in range(0, n - HOP, HOP):
    hop = x[k:k + HOP]; fake_now = 1000.0 + (k + HOP) / RATE
    b = t(hop)[0]; clock.fed(HOP)
    if b:
        ts.append((clock.beat(fake_now, t.get_last(), float(np.dot(hop, hop)))))
tt = [d["t"] for d in ts]; gaps = np.diff(tt)
print("beats", len(ts), "median gap %.3f s (expect 0.500)" % np.median(gaps), "bpm", round(t.get_bpm(), 1))
print("bar_guess seen:", sorted({d["bar_guess"] for d in ts if d["bar_guess"] is not None}))
