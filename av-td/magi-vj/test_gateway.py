import gateway, time

class FakeRedis:
    def __init__(s): s.d, s.sets, s.xs = {}, {}, []
    def get(s, k): return s.d.get(k)
    def smembers(s, k): return s.sets.get(k, set())
    def xadd(s, k, f, **kw): s.xs.append(f)

t = [1000.0]
clock = lambda: t[0]
r = FakeRedis(); r.d["magi:vj:mode"] = "auto"
g = gateway.Gateway(redis=r, enable=False, clock=clock)
g.set_tempo(120.0, 1000.0)

def chk(addr, v, want):
    d, why = g.request(addr, v); assert d == want, (addr, d, why, want); return why

chk("/composition/screens/1/output", 1, "denied")                    # tier 3
chk("/application/preferences/audio/device", 1, "denied")
chk("/composition/layers/1/video/opacity", 0.5, "sent")              # tier 1
chk("/composition/layers/1/video/opacity", 0.9, "dropped")           # rate limit
t[0] += 1
chk("/composition/layers/1/video/opacity", 0.9, "sent")
assert abs(g.last_sent["/composition/layers/1/video/opacity"][1] - 0.6) < 1e-6  # slew limited
chk("/composition/unknown/thing", 1.0, "denied")                     # default deny
chk("/composition/layers/2/clips/3/connect", 1, "denied")            # empty slot not allowlisted
chk("/composition/layers/2/clips/4/connect", 1, "queued")            # tier 2
g.tick(); assert g.queue
t[0] += 17.0                                                         # cross an 8-bar (16 s) boundary at 120 BPM
assert g.tick() == ["/composition/layers/2/clips/4/connect"]
chk("/composition/layers/2/clips/5/connect", 1, "dropped")           # cooldown
r.d["magi:vj:mode"] = "assist"; t[0] += 20
chk("/composition/layers/3/clips/2/connect", 1, "proposed")
r.d["magi:vj:mode"] = "auto"; r.sets["magi:vj:locks"] = {"/composition/layers/1/**"}
chk("/composition/layers/1/video/opacity", 0.2, "denied")            # human lock
r.d["magi:vj:mode"] = "manual"
chk("/composition/layers/3/video/opacity", 0.2, "denied")            # manual
del r.d["magi:vj:mode"]
chk("/composition/layers/3/video/opacity", 0.2, "denied")            # missing mode = safe
assert len(r.xs) >= 10
r.d["magi:vj:mode"] = "auto"
chk("/composition/layers/1/clips/1/transport/controls/playmode", 1, "denied")  # new tier-3
chk("/composition/tempocontroller/tempo", 0.3, "denied")               # magi-audio owns tempo
chk("/composition/dashboard/link3", 0.5, "denied")                    # Gil: distort out
chk("/composition/dashboard/link7", 1.0, "sent"); assert abs(g.last_sent["/composition/dashboard/link7"][1] - 0.15) < 1e-9  # capped at 0.15
chk("/composition/columns/2/connect", 1, "denied")                    # columns now tier 3
chk("/composition/decks/2/select", 1, "denied")
g2 = gateway.Gateway(redis=r, enable=True, clock=clock)
d, why = g2.request("/composition/video/effects/huerotate/huerotate", 0.5); assert d == "denied", (d, why)
d, why = g2.request("/composition/dashboard/link2", 0.5); assert d == "sent", (d, why)
for a in ["/composition/tempocontroller/resync","/composition/tempocontroller/tempo_tap","/composition/tempocontroller/tempotap","/composition/tempocontroller/tempo_push","/composition/tempocontroller/tempopull"]:
    chk(a, 1, "denied")                                                 # beat phase owned by legion-resolume
t[0] += 5; g.last_sent.pop("/composition/dashboard/link2", None)
chk("/composition/dashboard/link2", 0.5, "sent"); t[0] += 1
chk("/composition/dashboard/link2", 0.5, "dropped")                    # unchanged value is not resent
print("ok", len(r.xs), "log records")
