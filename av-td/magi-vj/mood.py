"""Mood engine: music features -> Arena control requests, through the tier gateway.

Reads magi:audio:latest (BPM, RMS from magi-audio, ~every 2 s), keeps fast/slow
energy envelopes, and each loop asks the Gateway for:
  tier 1: macro knobs (dashboard 2 Hue drift, 5 Kaleido, 7 Twitch)
  tier 2: a clip trigger on a detected energy rise ("drop"), queued to a bar boundary
Nothing reaches Arena unless VJ_ENABLE=1 AND the rules are verified; dry-run by default.
Usage: python3 mood.py [--secs 30] [--assume-auto]
"""
import argparse, json, os, random, sys, time
import redis
import gateway
from feedback_signal import FeedbackMeter

def load_env(path):
    try:
        for line in open(os.path.expanduser(path)):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass

class DryRedis:
    """Reads go to the real Redis; gateway action logs go to a local file in dry-run."""
    def __init__(self, r, logpath): self.r, self.f = r, open(logpath, "a")
    def get(self, k): return self.r.get(k)
    def smembers(self, k): return self.r.smembers(k)
    def xadd(self, k, fields, **kw): self.f.write(fields["j"] + "\n"); self.f.flush()

def clamp(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))

# Spiral clips (L3C6, L9C5). Gil, live 2026-10-05: "the spiral and kaleidoscope is great" (show-2).
# L10C5 is also a Spiral but stays out of rotation (its param ids were re-created by a deck select).
SPIRAL = {(3, 6), (9, 5)}
SPIRAL_PICK_P = 0.6       # chance the next pick is a Spiral when one is available and not playing
KALEIDO_START = 0.55      # energy where kaleido begins to rise
KALEIDO_START_SPIRAL = 0.42   # earlier start while a Spiral is playing (cap and slew unchanged)

class Mood:
    def __init__(self):
        self.fast = self.slow = 0.0
        self.hue = 0.0
        self.phase = 0.0
        self.last_trigger = None
        self.last_t = None
        self.rise_since = None
        # populated clip slots to rotate through on a "drop" (generators layer; columns/decks are human-only)
        self.clips = [(3, 3), (9, 3), (3, 6), (9, 5), (9, 1), (3, 1)]   # calm generators only: Line Scape, Rings, Spiral, Metaballs
        self.ci = 0
        self.playing = {1: 1, 2: 1, 3: 1, 6: 1}   # layer -> clip believed playing (baseline); skip re-triggering it
        self.rng = random.Random()

    def update(self, st, now):
        dt = 0.2 if self.last_t is None else now - self.last_t
        self.last_t = now
        active = st and st.get("active") and st.get("rms_db", -99) > -50
        e = clamp((st["rms_db"] + 45) / 35) if active else 0.0
        self.fast += (e - self.fast) * clamp(dt / 4)
        self.slow += (e - self.slow) * clamp(dt / 40)
        bpm = st.get("bpm_smooth") or st.get("bpm") or 120
        self.phase += dt * (0.0007 + 0.003 * self.fast * bpm / 128)
        self.hue = 1.0 - abs(2.0 * (self.phase % 1.0) - 1.0)   # ping-pong 0..1, no jump
        tempo_n = clamp((bpm - 90) / 90)
        tension = clamp(0.5 + (self.fast - self.slow) * 2)
        return {"energy": self.fast, "tempo": tempo_n, "tension": tension, "bpm": bpm, "active": bool(active)}

    def spiral_playing(self):
        return any((lay, clip) in SPIRAL for lay, clip in self.playing.items())

    def kaleido_amount(self, energy):
        start = KALEIDO_START_SPIRAL if self.spiral_playing() else KALEIDO_START
        return clamp((energy - start) * 2.2) * 0.45

    def next_clip(self):
        spirals = [c for c in self.clips if c in SPIRAL and self.playing.get(c[0]) != c[1]]
        if spirals and self.rng.random() < SPIRAL_PICK_P:
            lay, clip = self.rng.choice(spirals)
            self.playing[lay] = clip
            return lay, clip
        for _ in range(len(self.clips)):
            lay, clip = self.clips[self.ci % len(self.clips)]; self.ci += 1
            if self.playing.get(lay) != clip:
                break
        self.playing[lay] = clip
        return lay, clip

    def wants_drop(self, f, now):
        if self.last_trigger is None:
            self.last_trigger = now
        if f["active"] and now - self.last_trigger > 60:   # section change at least every 45 s while music plays
            self.last_trigger = now; self.rise_since = None
            return True
        if self.last_trigger is not None and now - self.last_trigger < 30:   # global spacing between clip changes
            self.rise_since = None
            return False
        if f["energy"] - self.slow > 0.25 and f["active"]:
            self.rise_since = self.rise_since or now
            if now - self.rise_since > 6:
                self.rise_since = None; self.last_trigger = now
                return True
        else:
            self.rise_since = None
        return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=0)
    ap.add_argument("--assume-auto", action="store_true", help="dry-run only: pretend mode=auto")
    a = ap.parse_args()
    load_env("~/.config/magi/worker.env")
    r = redis.Redis(host=os.environ["REDIS_HOST"], password=os.environ["REDIS_PASSWORD"], decode_responses=True)
    live = os.environ.get("VJ_ENABLE", "0") == "1"
    view = r if live else DryRedis(r, os.path.join(os.path.dirname(os.path.abspath(__file__)), "dryrun.jsonl"))
    g = gateway.Gateway(redis=view, mode_override="auto" if (a.assume_auto and not live) else None)
    m, t0, last_print = Mood(), time.time(), 0
    meter, fb_id = FeedbackMeter(), f"{int(time.time() * 1000)}-0"   # live operator/crowd feedback -> intensity multiplier
    for k in (2, 5, 7):   # baseline is 0.0: first sends are slew-limited from there, not a jump
        g.last_sent[f"/composition/dashboard/link{k}"] = (t0 - 1, 0.0)
    while a.secs == 0 or time.time() - t0 < a.secs:
        now = time.time()
        try:                                              # new feedback since the last loop (weighted operator, tiny crowd)
            for sid, ents in r.xread({"magi:vj:feedback": fb_id}, count=100) or []:
                for fb_id, e in ents:
                    try: meter.add(json.loads(e["j"]), now)
                    except Exception: pass
        except Exception:
            pass
        mult = meter.multiplier(now)
        raw = r.get("magi:audio:latest")
        st = json.loads(raw) if raw else None
        if st:
            f = m.update(st, now)
            g.set_tempo(f["bpm"])
            if not f["active"]:   # no music: stay out of the way, leave Arena as the human set it
                time.sleep(0.5); continue
            # dashboard macros (labels from Arena): 2 Hue, 5 Kaleido, 7 Twitch, 8 Trails. Links 3 (Distort) and 8 (Trails) are Gil's defaults at 0 (set 2026-10-05), never touched.
            g.request("/composition/dashboard/link7", min(0.15, clamp((f["tension"] - 0.5) * 2) * 0.15 * mult), "mood")      # twitch on rising energy
            g.request("/composition/dashboard/link2", m.hue, "mood")                                      # slow hue drift
            g.request("/composition/dashboard/link5", min(0.45, m.kaleido_amount(f["energy"]) * mult), "mood")    # kaleido on loud passages; starts earlier while a Spiral plays
            if m.wants_drop(f, now):
                lay, clip = m.next_clip()
                g.request(f"/composition/layers/{lay}/clips/{clip}/connect", 1, "mood")
            g.tick()
            if now - last_print > 5:
                last_print = now
                print(f"{time.strftime('%H:%M:%S')} active={f['active']} bpm={f['bpm']:.1f} energy={f['energy']:.2f} tension={f['tension']:.2f} fb_mult={mult:.2f} mode={g.mode()} live={g.enable}", flush=True)
        time.sleep(0.2)

if __name__ == "__main__":
    main()
