#!/usr/bin/env python3
"""VJ interaction-table backend (Flask, :8095, LAN/tailnet only like the dashboard).

Serves the control page and a small API. Redis lives on repositorium and its creds stay there, so state
is read/written through ~/magi/vj/vjctl.py over the existing passwordless SSH (persistent ControlMaster).
Every change is two-step: POST /api/vj/arm returns a one-use token, POST /api/vj/commit executes it only if
it arrives >= MIN_HOLD_S after arming (the page makes the user HOLD a button, so a stray click or a replayed
request cannot change authority) and within TOKEN_TTL_S. Everything is audited in Redis stream magi:vj:control.
"""
from datetime import datetime
import hashlib, json, os, re, secrets, shlex, subprocess, sys, threading, time, uuid
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, abort, session
from werkzeug.security import check_password_hash

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from feedback_signal import classify, CROWD_WEIGHT, OP_WEIGHTS, REACTIONS   # noqa: E402
WEB = HERE / "web"
NERV_STATIC = Path.home() / "magi_dashboard" / "static"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ControlMaster=auto",
       "-o", f"ControlPath={Path.home()}/.ssh/cm-vj-%C", "-o", "ControlPersist=120", "repositorium"]
SHOWS = HERE.parent / "shows"
MIN_HOLD_S, TOKEN_TTL_S, STATE_TTL_S = 1.0, 15.0, 1.0
MODES = {"auto": "AI drives tier 1 + bar-quantized tier 2",
         "assist": "AI drives tier 1; tier 2 only proposed, never sent",
         "manual": "Human has control: the AI sends nothing"}

# What the AI can reach (lock rows). Patterns are exactly what the gateway matches against.
LAYERS_WITH_CLIPS = {1: 1, 2: 4, 3: 6, 4: 1, 5: 1, 6: 3, 7: 1, 9: 5, 10: 1, 11: 1}
SURFACES = {
    "macros": [{"id": f"link{n}", "label": lab, "jp": jp, "addr": f"/composition/dashboard/link{n}",
                "note": note}
               for n, lab, jp, note in ((2, "Hue", "色相", "slow drift"), (5, "Kaleido", "万華鏡", "cap 0.45, loud passages"),
                                        (7, "Twitch", "痙攣", "cap 0.15, rising energy"))],
    "opacity": [{"id": f"opacity{n}", "label": f"Layer {n} opacity", "addr": f"/composition/layers/{n}/video/opacity",
                 "note": "tier 1"} for n in range(1, 13)],
    "triggers": [{"id": f"trig{n}", "label": f"Layer {n} clip triggers", "addr": f"/composition/layers/{n}/clips/*/connect",
                  "note": f"{c} populated slot{'s' if c > 1 else ''}, tier 2, bar-quantized"} for n, c in LAYERS_WITH_CLIPS.items()],
}
GATED = [
    ("Outputs, NDI senders, screens, slices", "Arena preference files; not reachable by OSC at all"),
    ("Audio device and VBAN", "Voicemeeter / Arena prefs"),
    ("Columns", "a sparse column blacks the show out"),
    ("Decks", "blanks the clip grid, recreates clip parameter ids"),
    ("Tempo value", "owned by magi-audio"),
    ("Beat phase, tap and resync", "owned by legion-resolume"),
    ("Links 1, 3, 4, 6, 8 (RGB, Distort, Flip, Mirror, Trails)", "your defaults at 0, never the AI's"),
    ("Clip speed, transport, targets, trigger styles", "unverified or behaviour-changing"),
]
ALLOWED_LOCKS = {s["addr"] for g in SURFACES.values() for s in g}

# ---------------------------------------------------------------- sign-in (production lock)
# Anyone on the LAN can LOOK (state, shows, video). Every POST under /api/ needs a signed-in user, because
# authority, locks and feedback are production-level controls. Users live in ~/.secrets/vj-users.json as scrypt
# hashes (add them with ~/bin/vj-adduser); no users file = nobody can sign in = read-only (the safe default).
SECRETS = Path.home() / ".secrets"
USERS_FILE = Path(os.environ.get("VJ_USERS_FILE", SECRETS / "vj-users.json"))
KEY_FILE = Path(os.environ.get("VJ_SESSION_KEY", SECRETS / "vj-session.key"))
SESSION_IDLE_S, SESSION_MAX_S = 3600, 8 * 3600
FAIL_LIMIT, FAIL_WINDOW_S, LOCKOUT_S = 5, 300, 60
DUMMY_HASH = "scrypt:32768:8:1$x$" + "0" * 128      # compared when the user is unknown, so timing does not reveal names


def _session_key():
    try:
        return KEY_FILE.read_bytes()
    except FileNotFoundError:
        SECRETS.mkdir(mode=0o700, exist_ok=True)
        k = secrets.token_bytes(32)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(k)
        return k


from flask.sessions import SecureCookieSessionInterface   # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix        # noqa: E402


class _SessionIface(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):          # Secure cookie only when this request came in over HTTPS
        return request.is_secure

    def get_cookie_name(self, app):
        # Cookies ignore ports, so an https visit (Secure cookie) and a plain-http visit to the same host would share
        # one name, and browsers refuse to overwrite a Secure cookie from an http page: the http login then never sticks
        # (toast says signed in, next poll says locked). Separate names per scheme; both differ from the old "vj_session".
        return "__Secure-vjs" if request.is_secure else "vjs"


app = Flask(__name__, static_folder=None)
app.session_interface = _SessionIface()
_plain_wsgi, _proxied_wsgi = app.wsgi_app, ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0)
# Trust X-Forwarded-* ONLY from the local Caddy (127.0.0.1); direct clients cannot spoof their address or scheme.
app.wsgi_app = lambda environ, start: (_proxied_wsgi if environ.get("REMOTE_ADDR") == "127.0.0.1" else _plain_wsgi)(environ, start)
REQUIRE_HTTPS_LOGIN = os.environ.get("VJ_REQUIRE_HTTPS", "0") == "1"   # flip on once the CA is on the operator's devices
app.secret_key = _session_key()
app.config.update(SESSION_COOKIE_NAME="vjs", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  SESSION_COOKIE_SECURE=False)   # plain HTTP on the LAN for now: HTTPS is a planned improvement
_fails = {}       # ip -> [timestamps of recent failures]
_locked_until = {}


def load_users():
    try:
        return json.loads(USERS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def current_user():
    u, t0, last = session.get("u"), session.get("t0", 0), session.get("last", 0)
    now = time.time()
    if not u or now - t0 > SESSION_MAX_S or now - last > SESSION_IDLE_S or u not in load_users():
        session.clear()
        return None
    session["last"] = now
    return u


def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


OPEN_POSTS = ("/api/vj/login", "/api/vj/logout", "/api/vj/react")      # react = crowd reaction unless signed in
CROWD_REPLAY = re.compile(r"^/api/shows/[a-z0-9-]+/reaction$")
_react_seen = {}


def react_gate(ip, signed_in):
    """Crowd throttle: 1 per 2 s and 20 per minute per address (operators: 1 per second). Returns error text or ''."""
    now = time.time(); recent = [t for t in _react_seen.get(ip, []) if now - t < 60]
    if recent and now - recent[-1] < (1.0 if signed_in else 2.0):
        return "slow down"
    if not signed_in and len(recent) >= 20:
        return "too many reactions this minute"
    recent.append(now); _react_seen[ip] = recent[-30:]
    return ""


def who_tag(signed_user):
    if signed_user:
        return f"web:{signed_user}@{request.remote_addr}"
    return "crowd:" + hashlib.sha256(f"vj{request.remote_addr}".encode()).hexdigest()[:8]   # not the raw address


DEBUG_AUTH_FLAG = Path.home() / ".config" / "magi" / "vj-debug-auth"


def auth_reason():
    """Why current_user() would say no: absent / bad-signature-or-empty / expired / user-missing (no values logged)."""
    name = app.session_interface.get_cookie_name(app)
    if name not in request.cookies:
        return f"no {name} cookie sent"
    u, t0, last = session.get("u"), session.get("t0", 0), session.get("last", 0)
    if not u:
        return "cookie present but no valid session in it (bad signature or empty)"
    now = time.time()
    if now - t0 > SESSION_MAX_S:
        return "session older than the 8 h maximum"
    if now - last > SESSION_IDLE_S:
        return "session idle too long"
    if u not in load_users():
        return "user no longer exists"
    return "ok"


@app.before_request
def debug_auth_log():
    if request.path in ("/api/vj/me", "/api/vj/state") and DEBUG_AUTH_FLAG.exists():
        log(f"AUTHDBG {request.path} ip={request.remote_addr} scheme={'https' if request.is_secure else 'http'} "
            f"cookies={sorted(request.cookies)} -> {auth_reason()} | site={request.headers.get('Sec-Fetch-Site')} "
            f"dest={request.headers.get('Sec-Fetch-Dest')} ref={request.headers.get('Referer', '')[:60]}")


@app.before_request
def require_login_for_changes():
    if request.method == "POST" and request.path.startswith("/api/") and request.path not in OPEN_POSTS and not CROWD_REPLAY.match(request.path):
        if not current_user():
            return jsonify(error="sign in required", locked=True), 401


@app.post("/api/vj/login")
def api_login():
    ip, now = request.remote_addr, time.time()
    if REQUIRE_HTTPS_LOGIN and not request.is_secure:
        return jsonify(error="sign-in is only allowed over HTTPS: use the https:// address"), 403
    if _locked_until.get(ip, 0) > now:
        return jsonify(error=f"too many attempts, wait {int(_locked_until[ip] - now) + 1} s"), 429
    b = request.get_json(silent=True) or {}
    name, pw = str(b.get("user", ""))[:64], str(b.get("password", ""))[:256]
    users = load_users()
    ok = check_password_hash(users.get(name, DUMMY_HASH), pw) and name in users
    if not ok:
        recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW_S] + [now]
        _fails[ip] = recent
        if len(recent) >= FAIL_LIMIT:
            _locked_until[ip] = now + LOCKOUT_S; _fails[ip] = []
        log(f"vj login FAILED user={name!r} ip={ip}")
        return jsonify(error="wrong user or password"), 401
    _fails.pop(ip, None)
    session.clear(); session.update(u=name, t0=now, last=now)
    log(f"vj login ok user={name} ip={ip}")
    return jsonify(authed=True, user=name)


@app.post("/api/vj/logout")
def api_logout():
    u = session.get("u"); session.clear()
    if u:
        log(f"vj logout user={u} ip={request.remote_addr}")
    return jsonify(authed=False)


@app.get("/api/vj/me")
def api_me():
    u = current_user()
    return jsonify(authed=bool(u), user=u, configured=bool(load_users()))


_lock = threading.Lock()
_cache = {"t": 0.0, "data": None}
_tokens = {}      # token -> dict(action, value, label, armed, ip)


def vjctl(*args, timeout=12):
    # ssh joins its arguments into ONE remote shell command, so every argument must be shell-quoted (JSON has quotes and spaces)
    p = subprocess.run(SSH + ["python3 ~/magi/vj/vjctl.py " + " ".join(shlex.quote(str(a)) for a in args)],
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "vjctl failed").strip()[-300:])
    return json.loads(p.stdout)


def get_state(force=False):
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["t"] < STATE_TTL_S:
            return _cache["data"]
        try:
            d = vjctl("state"); d["link"] = "up"
        except Exception as e:
            d = {"ok": False, "link": "down", "error": str(e)[:200]}
        _cache.update(t=time.time(), data=d)
        return d


SPIRAL_CLIPS = {(3, 6), (9, 5)}


def describe(a):
    """Human text for an assist-mode proposal."""
    addr, val = a.get("addr") or "", a.get("value")
    m = re.search(r"/layers/(\d+)/clips/(\d+)/connect", addr)
    if m:
        l, c = int(m.group(1)), int(m.group(2))
        what = f"Trigger Layer {l} · clip {c}" + (" (Spiral)" if (l, c) in SPIRAL_CLIPS else "")
    elif "/dashboard/link" in addr:
        what = f"Set macro {addr.rsplit('link', 1)[-1]} to {val:.2f}" if isinstance(val, (int, float)) else f"Adjust {addr}"
    else:
        what = f"Change {addr.replace('/composition', '')}"
    bpm = f" · {a['bpm']:.0f} BPM" if isinstance(a.get("bpm"), (int, float)) else ""
    return f"{what}{bpm} · {a.get('reason') or 'proposed, not sent'}"


def public(d):
    d = dict(d); d["modes"] = MODES; d["surfaces"] = SURFACES
    d["suggestions"] = [{**x, "text": describe(x)} for x in d.get("suggestions", [])]
    d["gated"] = [{"label": a, "why": b} for a, b in GATED]
    d["min_hold_s"] = MIN_HOLD_S
    u = current_user()
    d["auth"] = {"authed": bool(u), "user": u, "configured": bool(load_users())}
    return d


@app.get("/")
def index():
    return send_from_directory(WEB, "vj.html")


@app.get("/web/<path:name>")
def web(name):
    return send_from_directory(WEB, name)


@app.get("/static/<path:name>")        # reuse the dashboard's own theme files, so the skin stays in sync
def static_nerv(name):
    if name not in ("nerv.css", "nerv-skin.css"):
        abort(404)
    return send_from_directory(NERV_STATIC, name)


@app.get("/api/vj/state")
def api_state():
    return jsonify(public(get_state()))


@app.post("/api/vj/arm")
def api_arm():
    b = request.get_json(silent=True) or {}
    action, value = b.get("action"), b.get("value")
    if action == "mode" and value in MODES:
        label = f"set authority to {value.upper()}"
    elif action in ("lock", "unlock") and value in ALLOWED_LOCKS:
        label = f"{action} {value}"
    else:
        return jsonify(error="invalid action"), 400
    now = time.time()
    for k in [k for k, v in _tokens.items() if now - v["armed"] > TOKEN_TTL_S]:
        _tokens.pop(k, None)
    tok = secrets.token_urlsafe(12)
    _tokens[tok] = dict(action=action, value=value, label=label, armed=now, ip=request.remote_addr)
    return jsonify(token=tok, min_hold_s=MIN_HOLD_S, ttl_s=TOKEN_TTL_S, label=label)


@app.post("/api/vj/commit")
def api_commit():
    tok = (request.get_json(silent=True) or {}).get("token")
    t = _tokens.pop(tok, None)            # single use whatever happens next
    if not t:
        return jsonify(error="unknown or expired token"), 400
    held = time.time() - t["armed"]
    if held < MIN_HOLD_S:
        return jsonify(error=f"released too early ({held:.2f}s < {MIN_HOLD_S}s): not applied"), 400
    if held > TOKEN_TTL_S:
        return jsonify(error="token expired"), 400
    who = f"web:{session.get('u')}@{request.remote_addr}"
    try:
        if t["action"] == "mode":
            d = vjctl("mode", t["value"], who)
        else:
            d = vjctl(t["action"], t["value"], who)
    except Exception as e:
        return jsonify(error=str(e)), 502
    d["link"] = "up"
    _cache.update(t=time.time(), data=d)
    return jsonify(public(d))


# ---------------------------------------------------------------- recorded shows: review + feedback
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
CATEGORIES = {"good", "too_much", "too_little", "bad_timing", "missed_opportunity", "note"}
SUGGEST = {"", "clip_change", "more_intensity", "less_intensity", "hue_shift", "hold_still", "calmer_clip", "kaleido", "twitch"}
_show_lock = threading.Lock()


def show_dir(sid):
    if not ID_RE.match(sid or ""):
        abort(404)
    d = SHOWS / sid
    if not (d / "show.json").exists():
        abort(404)
    return d


def read_jsonl(p):
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def feedback_list(d):
    items = {}
    try:
        sj = json.loads((d / "show.json").read_text())
        clip0 = (sj.get("video") or {}).get("clip_start", sj["t0"])
    except Exception:
        clip0 = 0
    for r in read_jsonl(d / "feedback.jsonl"):
        if r.get("kind") == "deleted":
            items.pop(r.get("id"), None)
        else:
            r.setdefault("id", f"remark-{int(r.get('t_wall') or r.get('t') or 0)}")
            if r.get("t_video") is None and r.get("t_wall"):
                r["t_video"] = round(r["t_wall"] - clip0, 2)
            items[r["id"]] = r
    return sorted(items.values(), key=lambda r: (r.get("t_video") is None, r.get("t_video") or 0))


@app.get("/api/shows")
def api_shows():
    out = []
    for p in sorted(SHOWS.glob("*/show.json")):
        s = json.loads(p.read_text())
        s["feedback_count"] = len(feedback_list(p.parent))
        out.append(s)
    out.sort(key=lambda s: s.get("t0", 0), reverse=True)
    return jsonify(out)


@app.get("/api/shows/<sid>")
def api_show(sid):
    d = show_dir(sid)
    s = json.loads((d / "show.json").read_text())
    last = {}
    acts = []
    for a in read_jsonl(d / "actions.jsonl"):          # thin the dense tier-1 stream: one point per second per address
        if a.get("decision") not in ("sent", "queued", "proposed", "denied"):
            continue
        k = (a["addr"], int(a["t"]))
        if a.get("tier") == 1 and k in last:
            continue
        last[k] = 1
        acts.append({x: a.get(x) for x in ("t", "addr", "value", "tier", "decision", "reason")})
    aud = read_jsonl(d / "audio.jsonl")
    for a in aud:        # naive local ISO stamp from repositorium -> epoch (server and repositorium share the CDT zone)
        try:
            a["t"] = datetime.fromisoformat(a["ts"]).timestamp()
        except (KeyError, ValueError):
            a["t"] = None
    s["actions"], s["audio"], s["feedback"] = acts, aud, feedback_list(d)
    return jsonify(s)


@app.get("/media/<sid>/<name>")
def media(sid, name):
    d = show_dir(sid)
    s = json.loads((d / "show.json").read_text())
    v = s.get("video")
    if not v or name != v["file"]:
        abort(404)
    return send_from_directory(v["dir"], name, conditional=True, mimetype="video/mp4")


@app.post("/api/shows/<sid>/feedback")
def api_feedback(sid):
    d = show_dir(sid)
    b = request.get_json(silent=True) or {}
    cat, sug = b.get("category"), b.get("suggest", "")
    if cat not in CATEGORIES or sug not in SUGGEST:
        return jsonify(error="invalid category"), 400
    try:
        tv = float(b.get("t_video"))
    except (TypeError, ValueError):
        return jsonify(error="t_video required"), 400
    s = json.loads((d / "show.json").read_text())
    clip0 = (s.get("video") or {}).get("clip_start", s["t0"])
    try:
        strength = int(b.get("strength", 2))
    except (TypeError, ValueError):
        strength = 2
    strength = min(3, max(1, strength)); inten, sent = classify(cat, sug)
    rec = {"id": uuid.uuid4().hex[:10], "kind": "feedback", "show": sid, "category": cat, "suggest": sug,
           "text": str(b.get("text", ""))[:600], "t_video": round(tv, 2), "t_wall": round(clip0 + tv, 2),
           "created": time.time(), "who": who_tag(session.get("u")), "source": "operator", "strength": strength,
           "weight": OP_WEIGHTS[strength], "intensity": inten, "sentiment": sent}
    with _show_lock:
        with open(d / "feedback.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    try:   # mirror to the cluster Redis as training data
        vjctl("feedback", json.dumps({**rec, "t": rec["t_wall"]}), rec["who"])
    except Exception:
        pass
    return jsonify(rec)


@app.post("/api/shows/<sid>/feedback/<fid>/delete")
def api_feedback_delete(sid, fid):
    d = show_dir(sid)
    if not re.match(r"^[a-z0-9-]{3,40}$", fid):
        abort(404)
    with _show_lock:
        with open(d / "feedback.jsonl", "a") as f:
            f.write(json.dumps({"kind": "deleted", "id": fid, "created": time.time()}) + "\n")
    return jsonify(ok=True)


@app.post("/api/vj/react")
def api_react():
    """Live reaction during a show. Signed in = weighted OPERATOR feedback; signed out = tiny-weight CROWD indication."""
    kind = (request.get_json(silent=True) or {}).get("kind")
    if kind not in REACTIONS:
        return jsonify(error="unknown reaction"), 400
    u = current_user()
    err = react_gate(request.remote_addr, bool(u))
    if err:
        return jsonify(error=err), 429
    try:
        strength = min(3, max(1, int((request.get_json(silent=True) or {}).get("strength", 2))))
    except (TypeError, ValueError):
        strength = 2
    inten, sent = REACTIONS[kind]
    rec = {"kind": "reaction", "reaction": kind, "source": "operator" if u else "crowd",
           "weight": OP_WEIGHTS[strength] if u else CROWD_WEIGHT, "intensity": inten, "sentiment": sent, "t": time.time()}
    try:
        vjctl("feedback", json.dumps(rec), who_tag(u))
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(ok=True, source=rec["source"], weight=rec["weight"])


@app.post("/api/shows/<sid>/reaction")
def api_show_reaction(sid):
    """Crowd marker on a recorded show: anyone, reaction kinds only (no free text), shown on the replay, never operator-weighted."""
    d = show_dir(sid)
    b = request.get_json(silent=True) or {}
    kind = b.get("kind")
    if kind not in REACTIONS:
        return jsonify(error="unknown reaction"), 400
    try:
        tv = float(b.get("t_video"))
    except (TypeError, ValueError):
        return jsonify(error="t_video required"), 400
    u = current_user()
    err = react_gate(request.remote_addr, bool(u))
    if err:
        return jsonify(error=err), 429
    s = json.loads((d / "show.json").read_text())
    clip0 = (s.get("video") or {}).get("clip_start", s["t0"])
    inten, sent = REACTIONS[kind]
    rec = {"id": uuid.uuid4().hex[:10], "kind": "feedback", "show": sid, "category": "crowd", "reaction": kind, "suggest": "",
           "text": "", "t_video": round(tv, 2), "t_wall": round(clip0 + tv, 2), "created": time.time(), "who": who_tag(None),
           "source": "crowd", "weight": CROWD_WEIGHT, "intensity": inten, "sentiment": sent}
    with _show_lock:
        with open(d / "feedback.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    try:
        vjctl("feedback", json.dumps({**rec, "t": rec["t_wall"], "source": "crowd"}), rec["who"])
    except Exception:
        pass
    return jsonify(rec)


@app.post("/api/shows/<sid>/status")
def api_show_status(sid):
    d = show_dir(sid)
    st = (request.get_json(silent=True) or {}).get("status")
    if st not in ("needs_review", "reviewed"):
        return jsonify(error="invalid status"), 400
    with _show_lock:
        s = json.loads((d / "show.json").read_text())
        s["status"] = st
        s["flags"] = ["for-review"] if st == "needs_review" else []
        if st == "reviewed":
            s["reviewed_at"] = time.time()
        (d / "show.json").write_text(json.dumps(s, indent=1))
    return jsonify(s)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("VJ_PORT", "8095")), threaded=True)
