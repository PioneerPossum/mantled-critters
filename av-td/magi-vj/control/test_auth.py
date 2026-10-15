"""Sign-in tests for the VJ control backend (no Redis/ssh: vjctl and get_state are stubbed)."""
import json, os, tempfile
from pathlib import Path
d = tempfile.mkdtemp(); os.environ["VJ_USERS_FILE"] = f"{d}/users.json"; os.environ["VJ_SESSION_KEY"] = f"{d}/key"
from werkzeug.security import generate_password_hash
import vj_control as v
v.get_state = lambda force=False: {"ok": True, "mode": "manual", "locks": [], "link": "up", "control": [], "actions": []}
calls = []
v.vjctl = lambda *a, **k: (calls.append(a) or {"ok": True, "mode": a[1] if a[0] == "mode" else "manual", "locks": [], "control": [], "actions": []})
c = v.app.test_client()
# no users file: everyone read-only
assert c.get("/api/vj/state").status_code == 200 and c.get("/api/vj/state").json["auth"] == {"authed": False, "user": None, "configured": False}
assert c.post("/api/vj/arm", json={"action": "mode", "value": "auto"}).status_code == 401
assert c.post("/api/vj/login", json={"user": "gil", "password": "x"}).status_code == 401
Path(os.environ["VJ_USERS_FILE"]).write_text(json.dumps({"gil": generate_password_hash("correct horse battery", method="scrypt")}))
assert c.get("/api/vj/me").json["configured"] is True and c.get("/api/vj/me").json["authed"] is False
for path, body in (("/api/vj/arm", {"action": "mode", "value": "auto"}), ("/api/vj/commit", {"token": "x"}),
                   ("/api/shows/show-2/feedback", {"text": "x"}), ("/api/shows/show-2/status", {"status": "reviewed"}),
                   ("/api/shows/show-2/feedback/abc/delete", {})):
    r = c.post(path, json=body); assert r.status_code == 401 and r.json["locked"] is True, (path, r.status_code)
assert c.get("/api/shows").status_code in (200, 500), "viewing stays open"
assert c.post("/api/vj/login", json={"user": "gil", "password": "wrong"}).status_code == 401
assert c.post("/api/vj/login", json={"user": "nobody", "password": "correct horse battery"}).status_code == 401
r = c.post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"}); assert r.status_code == 200 and r.json["authed"]
assert "HttpOnly" in r.headers["Set-Cookie"] and "SameSite=Strict" in r.headers["Set-Cookie"], r.headers["Set-Cookie"]
assert c.get("/api/vj/state").json["auth"]["user"] == "gil"
a = c.post("/api/vj/arm", json={"action": "mode", "value": "auto"}); assert a.status_code == 200, a.status_code
import time; time.sleep(v.MIN_HOLD_S + 0.1)
assert c.post("/api/vj/commit", json={"token": a.json["token"]}).status_code == 200 and calls[-1][-1].startswith("web:gil@"), calls
# idle expiry
with c.session_transaction() as s: s["last"] = time.time() - v.SESSION_IDLE_S - 5
assert c.post("/api/vj/arm", json={"action": "mode", "value": "auto"}).status_code == 401
# removed user is signed out at once
c.post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"})
Path(os.environ["VJ_USERS_FILE"]).write_text("{}")
assert c.post("/api/vj/arm", json={"action": "mode", "value": "auto"}).status_code == 401
# logout
Path(os.environ["VJ_USERS_FILE"]).write_text(json.dumps({"gil": generate_password_hash("correct horse battery", method="scrypt")}))
c.post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"}); c.post("/api/vj/logout")
assert c.post("/api/vj/arm", json={"action": "mode", "value": "auto"}).status_code == 401
# brute force throttle: 5 failures -> 429, even for the right password during the lockout
c2 = v.app.test_client()
for _ in range(5): assert c2.post("/api/vj/login", json={"user": "gil", "password": "bad"}).status_code == 401
r = c2.post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"}); assert r.status_code == 429, r.status_code
assert oct(os.stat(os.environ["VJ_SESSION_KEY"]).st_mode)[-3:] == "600"
print("auth tests ok")

# ---------------- reactions, weighted feedback, suggestions, proxy/cookie handling (2026-10-09)
import tempfile as _tf, shutil
sent = []
v.vjctl = lambda *a, **k: (sent.append(a) or {"ok": True, "mode": "manual", "locks": [], "control": [], "actions": []})
v._react_seen.clear()
Path(os.environ["VJ_USERS_FILE"]).write_text(json.dumps({"gil": generate_password_hash("correct horse battery", method="scrypt")}))
cc = v.app.test_client()                                   # a signed-out crowd member
r = cc.post("/api/vj/react", json={"kind": "like"}); assert r.status_code == 200 and r.json["source"] == "crowd" and r.json["weight"] == v.CROWD_WEIGHT
rec = json.loads(sent[-1][1]); assert rec["source"] == "crowd" and rec["reaction"] == "like" and sent[-1][2].startswith("crowd:") and "127.0.0.1" not in sent[-1][2]
assert cc.post("/api/vj/react", json={"kind": "like"}).status_code == 429, "crowd throttle 1 per 2 s"
assert cc.post("/api/vj/react", json={"kind": "nonsense"}).status_code == 400
assert cc.post("/api/vj/arm", json={"action": "mode", "value": "auto"}).status_code == 401, "crowd cannot control"
assert cc.post("/api/shows/show-2/feedback", json={"category": "good", "t_video": 5}).status_code == 401, "crowd cannot give weighted feedback"
v._react_seen.clear(); v._locked_until.clear(); v._fails.clear(); op = v.app.test_client(); assert op.post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"}).status_code == 200
r = op.post("/api/vj/react", json={"kind": "less", "strength": 3}); assert r.json["source"] == "operator" and r.json["weight"] == v.OP_WEIGHTS[3], r.json
rec = json.loads(sent[-1][1]); assert rec["intensity"] == -1.0 and sent[-1][2].startswith("web:gil@")
# show-replay crowd marker + operator weighted feedback on a throwaway show dir
tmp = Path(_tf.mkdtemp()); (tmp / "show-9").mkdir(); (tmp / "show-9" / "show.json").write_text(json.dumps({"id": "show-9", "t0": 1000.0, "video": {"clip_start": 1000.0}}))
v.SHOWS = tmp; v._react_seen.clear()
r = cc.post("/api/shows/show-9/reaction", json={"kind": "more", "t_video": 12.5}); assert r.status_code == 200 and r.json["source"] == "crowd" and r.json["weight"] == v.CROWD_WEIGHT, r.status_code
r = op.post("/api/shows/show-9/feedback", json={"category": "too_much", "t_video": 20, "strength": 3, "suggest": ""}); assert r.status_code == 200 and r.json["source"] == "operator" and r.json["weight"] == 2.0 and r.json["intensity"] == -1.0, r.json
items = v.feedback_list(tmp / "show-9"); assert [i["source"] for i in items] == ["crowd", "operator"], items
# suggestion text
t = v.describe({"addr": "/composition/layers/3/clips/6/connect", "value": 1, "reason": "assist mode: not sent", "bpm": 126.4}); assert "Layer 3 · clip 6 (Spiral)" in t and "126 BPM" in t, t
assert "macro 2" in v.describe({"addr": "/composition/dashboard/link2", "value": 0.5, "reason": "r"})
with v.app.test_request_context("/"): pub = v.public({"suggestions": [{"t": 1, "addr": "/composition/layers/2/clips/4/connect", "reason": "x"}], "locks": []})
assert pub["suggestions"][0]["text"].startswith("Trigger Layer 2")
# proxy header trust: only from 127.0.0.1 (test client is 127.0.0.1 -> trusted); a direct client spoofing is not
e = {"REMOTE_ADDR": "10.1.2.3", "HTTP_X_FORWARDED_PROTO": "https", "HTTP_X_FORWARDED_FOR": "1.1.1.1"}
r = v.app.test_client().post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"}, headers={"X-Forwarded-Proto": "https"}, environ_overrides={"REMOTE_ADDR": "10.1.2.3"})
assert r.status_code == 200 and "Secure" not in r.headers["Set-Cookie"], "spoofed header must not mark the cookie Secure"
r = v.app.test_client().post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"}, headers={"X-Forwarded-Proto": "https"})
assert r.status_code == 200 and "Secure" in r.headers["Set-Cookie"], "from the local proxy: Secure cookie"
shutil.rmtree(tmp)
print("reaction/feedback/suggestion/proxy tests ok")

# ---------------- cookie names per scheme (2026-10-09): an https cookie must never shadow a plain-http login
cs = v.app.test_client(); v._locked_until.clear(); v._fails.clear()
r = cs.post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"})
assert r.headers["Set-Cookie"].startswith("vjs=") and "Secure" not in r.headers["Set-Cookie"], r.headers["Set-Cookie"]
assert cs.get("/api/vj/me").json["authed"] is True, "plain-http login must stick"
cs2 = v.app.test_client()
r = cs2.post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"}, headers={"X-Forwarded-Proto": "https"})
assert r.headers["Set-Cookie"].startswith("__Secure-vjs=") and "Secure" in r.headers["Set-Cookie"], r.headers["Set-Cookie"]
assert cs2.get("/api/vj/me", headers={"X-Forwarded-Proto": "https"}).json["authed"] is True
assert cs2.get("/api/vj/me").json["authed"] is False, "an https cookie is not accepted over plain http (different name)"
cs3 = v.app.test_client(); cs3.set_cookie("vj_session", "stale-secure-cookie-from-an-https-visit")
assert cs3.post("/api/vj/login", json={"user": "gil", "password": "correct horse battery"}).status_code == 200 and cs3.get("/api/vj/me").json["authed"] is True, "legacy stale cookie must not block"
print("cookie-name tests ok")
