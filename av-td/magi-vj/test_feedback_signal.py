import feedback_signal as f
m = f.FeedbackMeter(); T = 1000.0
assert m.multiplier(T) == 1.0
i, s = f.classify("too_much"); m.add({"t": T, "source": "operator", "weight": 1.0, "intensity": i, "sentiment": s}, T)
assert 0.55 <= m.multiplier(T) < 0.7, m.multiplier(T)                         # a normal 'too much' cuts intensity
assert abs(m.multiplier(T + 600) - 1.0) < 0.01                               # forgotten after ~10 min
m = f.FeedbackMeter()
for _ in range(100):                                                           # 100 crowd 'more' taps can only nudge, never dominate
    m.add({"t": T, "source": "crowd", "weight": f.CROWD_WEIGHT, "intensity": 1.0, "sentiment": 0.5}, T)
assert 1.0 < m.multiplier(T) <= 1.0 + f.CROWD_GAIN * f.CROWD_CAP + 1e-9, m.multiplier(T)
m.add({"t": T, "source": "operator", "weight": 2.0, "intensity": -1.0, "sentiment": -0.5}, T)   # strong operator 'too much' beats the crowd
assert m.multiplier(T) < 1.0, m.multiplier(T)
assert m.crowd_sentiment(T) > 0 and f.classify(reaction="dislike") == (-0.5, -1.0)
assert f.classify("good", "more_intensity") == (1.0, 1.0)
m.add({"t": "bad"}, T); m.add({"t": T, "weight": "x"}, T)                      # garbage never raises
assert f.MULT_MIN <= m.multiplier(T) <= f.MULT_MAX
print("feedback_signal ok")
