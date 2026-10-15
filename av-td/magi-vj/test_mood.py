import mood
m = mood.Mood(); t = 0.0; picks = []
for i in range(2000):                       # 400 s of synthetic music
    st = {"active": True, "rms_db": -40 if i < 200 else -15, "bpm": 128}
    f = m.update(st, t); t += 0.2
    if m.wants_drop(f, t):
        picks.append(m.next_clip())
assert len(picks) >= 5, picks
assert all(c not in [(1, 1), (6, 1)] for c in picks)
assert picks[0] != (3, 1), picks            # baseline-playing clip on layer 3 is skipped first
assert all(0 <= m.hue <= 1 for _ in [0])
print("mood ok", picks)

# Spiral + Kaleido weighting (2026-10-06)
m = mood.Mood(); m.rng.seed(1)
assert not m.spiral_playing()
base = m.kaleido_amount(0.5)
m.playing[3] = 6                              # Spiral L3C6 playing
assert m.spiral_playing()
assert m.kaleido_amount(0.5) > base, "kaleido should start earlier with a Spiral playing"
assert m.kaleido_amount(1.0) <= 0.45 + 1e-9, "cap unchanged"
assert m.kaleido_amount(0.3) == 0.0
m = mood.Mood(); m.rng.seed(7); seq = [m.next_clip() for _ in range(400)]
spiral_share = sum(c in mood.SPIRAL for c in seq) / len(seq)
assert 0.3 < spiral_share < 0.7, spiral_share   # favoured, not exclusive
assert all(c in m.clips for c in seq)
assert all(a != b for a, b in zip(seq, seq[1:])), "never retrigger the clip already playing on its layer"
print("spiral weighting ok", round(spiral_share, 2))
