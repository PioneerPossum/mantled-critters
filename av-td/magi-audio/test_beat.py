import beat_emit as b
c = b.BeatClock(48000)
# timestamps: beat 3 hops (1536 samples = 32 ms) old
c.fed(48000)
d = c.beat(100.0, 48000 - 1536, 1.0)
assert abs(d["t"] - (100.0 - 0.032)) < 1e-6, d
assert c.beat(100.5, 0, 1.0)["t"] == 100.5          # missing sample index -> no age
c = b.BeatClock(48000); c.fed(10**6)
assert c.beat(50.0, 0, 1.0)["t"] == 50.0
assert c.beat(50.0, 10**6 - 10**5, 1.0)["t"] == 50.0 - 0.25   # absurd age clamped
# downbeat guess: beats 4,8,12,... strong (every 4th), steady for >= 20 beats
c = b.BeatClock(48000); got = []
for i in range(1, 60):
    c.fed(24000)
    got.append((i, c.beat(i * 0.5, c.samples, 1.0 if i % 4 == 0 else 0.3)["bar_guess"]))
assert all(g is None for i, g in got[:18]), got[:18]
late = [(i, g) for i, g in got if g is not None]
assert late, got
assert all(g == (i % 4) for i, g in late), late      # downbeat = beat numbers that are multiples of 4
# flat energy -> never guesses
c = b.BeatClock(48000)
for i in range(1, 60):
    c.fed(24000); assert c.beat(i * 0.5, c.samples, 1.0)["bar_guess"] is None
print("beat ok")
