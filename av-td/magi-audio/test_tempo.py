from tempo_track import TempoTracker

def feed(tt, t0, secs, raw, conf=0.3, rms=-16.0, step=2.0):
    sent, t = [], t0
    while t < t0 + secs:
        b = tt.update(t, raw(t) if callable(raw) else raw, conf, rms, True)
        if b: sent.append((round(t - t0, 1), round(b, 1)))
        t += step
    return sent, t

tt = TempoTracker(start=0.0); tt.current = 118.3; tt.last_sent_t = -999
s, t = feed(tt, 100, 120, lambda t: 88.25 if int(t / 6) % 2 else 118.3)          # aubio flipping 118 <-> 3/4 sub-pulse
assert s == [], s
s, t = feed(tt, t, 40, 88.25)                                                   # steady sub-pulse alone (ratio-only to current): ignored
assert s == [], s
s, t = feed(tt, t, 30, 128.0)                                                   # normal change (8%): ~10 s
assert s and s[0][0] <= 14 and abs(s[0][1] - 128.0) < 1, s
s, t = feed(tt, t, 60, 140.0, conf=0.3)                                         # big non-ratio jump (9% from 128... use 140): ~10 s
assert s and s[0][0] <= 40, s
s2, t = feed(tt, t, 60, 105.0, conf=0.3)        # 105 is 3/4 of the current 140: ignored as a sub-pulse
assert s2 == [], s2
tt = TempoTracker(start=0.0); tt.current = 100.0; tt.last_sent_t = -999
s, t = feed(tt, 100, 100, 150.0)                                                # 3:2 of current, steady 100 s: still ignored (needs 120 s)
assert s == [], s
s, t = feed(tt, t, 60, 150.0)                                                    # steady beyond 2 min: accepted
assert s and s[0][1] == 150.0, s
tt = TempoTracker(start=0.0); tt.current = 100.0; tt.last_sent_t = -999
s, t = feed(tt, 100, 80, 130.0, conf=0.1)                                       # big jump on low confidence: not adopted
assert s == [], s
print("tempo ok")
