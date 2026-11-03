import json, socket, subprocess, sys, time, os
import crowd_rx as c
good = {"v":1,"id":"xiao-1","ms":1000,"motion":0.12,"lux":0.4,"cells":list(range(12)),"mic_db":-40.2,"bands":[0.01,0.02,0.0],"cam":1,"rssi":-55}
assert c.validate(json.dumps(good).encode())["motion"] == 0.12
for bad in [dict(good, v=2), dict(good, motion=1.5), dict(good, cells=[1]*11), dict(good, id="../x"), dict(good, mic_db=50),
            dict(good, bands=[0,0]), dict(good, motion=True), b"junk"]:
    assert c.validate(bad if isinstance(bad, bytes) else json.dumps(bad).encode()) is None, bad
r = c.Rolling(); a = c.validate(json.dumps(good).encode()); b = dict(a, motion=0.5, cells=[255]*12)
r.add(a, 10.0); r.add(b, 10.4); s = r.summary(10.5)["xiao-1"]
assert s["motion_max"] == 0.5 and s["cells_max"][0] == 255 and s["n"] == 2
assert r.summary(12.0) == {}, "stale data must expire"
# end to end over a real socket in --dry mode
env = dict(os.environ, CROWD_PORT="17020", CROWD_ALLOW="127.")
p = subprocess.Popen([sys.executable, "crowd_rx.py", "--dry"], stdout=subprocess.PIPE, text=True, env=env); time.sleep(1)
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
u.sendto(json.dumps(good).encode(), ("127.0.0.1", 17020)); u.sendto(b"garbage", ("127.0.0.1", 17020)); time.sleep(0.5)
p.terminate(); out = p.stdout.read()
assert out.count('"id": "xiao-1"') == 1, out
print("crowd_rx ok")
