"""Tempo selection for magi-audio: decides which BPM (if any) to send to Arena.

aubio often locks on a metric sub-pulse (e.g. 3/4 of the real tempo: 118 -> 88.25 -> x2 = 176.5).
Instead of folding every reading into a fixed range, a reading that is a simple metric
ratio of the CURRENT tempo (1/2, 2/3, 3/4, 4/3, 3/2, 2) is treated as the same tempo.
A new tempo is adopted only when readings are steady: ~10 s for a normal change (<=12%),
30 s for a big jump (was 90 s; shortened at Gil's request 2026-10-05), optionally gated by median confidence. Readings in quiet passages (breakdowns) are ignored.
"""
import statistics
from collections import deque

RATIOS = (0.5, 2 / 3, 0.75, 1.0, 4 / 3, 1.5, 2.0)
OCTAVES = (0.5, 1.0, 2.0)


class TempoTracker:
    def __init__(self, bpm_min=90.0, bpm_max=180.0, conf_min=0.1, hold_s=10.0, jump_hold_s=30.0, jump_conf=0.18, suspect_hold_s=60.0, ratio_hold_s=120.0,
                 quiet_db=8.0, min_gap_s=8.0, step=2.0, warmup_s=15.0, start=0.0):
        self.bmin, self.bmax = bpm_min, bpm_max
        self.conf_min, self.hold_s, self.jump_hold_s, self.jump_conf = conf_min, hold_s, jump_hold_s, jump_conf
        self.suspect_hold_s, self.ratio_hold_s = suspect_hold_s, ratio_hold_s
        self.quiet_db, self.min_gap_s, self.step, self.warmup_s = quiet_db, min_gap_s, step, warmup_s
        self.started = start
        self.hist = deque()      # (t, folded bpm) of accepted readings, last 120 s
        self.rms = deque()       # (t, rms_db) last 60 s
        self.current = None      # last sent tempo
        self.last_sent_t = -1e9

    def fold(self, raw):
        """Octave-fold into [bmin, bmax], preferring 90-140 when several octaves fit."""
        if raw <= 0:
            return 0.0
        c = [raw * 2 ** k for k in range(-3, 4) if self.bmin <= raw * 2 ** k <= self.bmax]
        if not c:
            return 0.0
        pref = [x for x in c if 90 <= x <= 140]
        pool = pref or c
        return min(pool, key=lambda x: abs(x - 120))

    def _steady(self, now, window, target, tol=1.5, frac=0.85, min_conf=0.0):
        pts = [b for t, b, _ in self.hist if now - t <= window]
        if len(pts) < max(3, window / 4.0) or now - self.hist[0][0] < window * 0.9:
            return None
        med = statistics.median(pts)
        if sum(1 for b in pts if abs(b - med) <= tol) / len(pts) < frac:
            return None
        if min_conf and statistics.median(c for t, _, c in self.hist if now - t <= window) < min_conf:
            return None
        return med

    def update(self, now, raw, conf, rms_db, active):
        """Return a BPM to send, or None."""
        if not self.started:
            self.started = now
        if not active or rms_db < -50 or raw <= 0 or not (0.0 <= conf <= 2.0) or conf < self.conf_min:
            return None
        self.rms.append((now, rms_db))
        while self.rms and now - self.rms[0][0] > 60:
            self.rms.popleft()
        if len(self.rms) >= 10 and rms_db < statistics.median(r for _, r in self.rms) - self.quiet_db:
            return None                       # quiet breakdown: beat tracking is unreliable
        f = self.fold(raw)
        if f <= 0:
            return None
        self.hist.append((now, f, conf))
        while self.hist and now - self.hist[0][0] > 120:
            self.hist.popleft()
        if now - self.started < self.warmup_s or now - self.last_sent_t < self.min_gap_s:
            return None
        if self.current is None:
            med = self._steady(now, 8.0, f, frac=0.6)
            return self._send(now, med) if med else None
        # metric-ratio consistent with the tempo we already sent -> same tempo, ignore
        ratio_only = (any(abs(raw * r - self.current) <= 0.015 * self.current for r in RATIOS)
                      and not any(abs(raw * r - self.current) <= 0.015 * self.current for r in OCTAVES))
        if ratio_only:                        # only a 2/3, 3/4, 4/3 or 3/2 relation: almost certainly a sub-pulse
            med = self._steady(now, self.ratio_hold_s, f, frac=0.95, min_conf=self.jump_conf)   # ...unless rock steady for 2 min
            return self._send(now, med) if med is not None and abs(med - self.current) > self.step else None
        ratio = f / self.current
        suspect = any(abs(ratio - q) < 0.04 for q in (0.5, 2 / 3, 0.75, 4 / 3, 1.5, 2.0))   # metric-ratio guard on the folded value
        window = self.hold_s if 0.88 <= ratio <= 1.12 else (self.suspect_hold_s if suspect else self.jump_hold_s)
        med = self._steady(now, window, f, min_conf=self.jump_conf if window != self.hold_s else 0.0)
        if med is not None and abs(med - self.current) > self.step:
            return self._send(now, med)
        return None

    def _send(self, now, bpm):
        self.current, self.last_sent_t = bpm, now
        return bpm
