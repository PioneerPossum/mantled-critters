#!/usr/bin/env python3
"""magi-cluster dashboard — main backend, hosted on audire-videre.

Why here and not repositorium: this host already has passwordless SSH
configured to melchior/balthazar/repositorium/edgerunner (used for node
health + detection-service control below) and is where the D555 camera
physically lives (magi_stream_d555.py's MJPEG preview server on :8092).
Postgres/Redis data is fetched from repositorium's read-only
magi-dashboard-data API (see ~/magi/dashboard/data_api.py there) rather than
connecting to the DB directly from here, to avoid widening Postgres's
pg_hba.conf beyond melchior/balthazar.

Serves the single-page frontend plus a small JSON API. No auth layer — this
is a LAN/tailnet-only home dashboard per the task brief, never exposed to
the open internet (see magi-dashboard.md for the access model).
"""
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

app = Flask(__name__, static_folder="static")

DATA_API = "http://192.168.8.184:8801"
MJPEG_SOURCE = "http://127.0.0.1:8092"
# Local Hailo-10H LLM API (magi-llm-api.service, same code both nodes) — see
# ~/magi/<node>/magi_llm_api.py and magi-local-llm-feasibility.md. Each node
# serializes GenAI requests itself (HailoRT allows only one session at a time
# per chip), so a 429 from there is passed straight through rather than
# retried here.
#
# Role flip (2026-09-16): balthazar is now the default/normal-duty language
# node (OWN_DUTY_ROLES=language in its publish.env); melchior's magi-llm-api
# is failover-only (installed+disabled, only started by magi_failover.py or
# a manual override if balthazar's language heartbeat goes stale). Default
# target is balthazar; api_llm_chat() falls back to melchior only if
# balthazar is actually unreachable/erroring, checking /api/failover/status
# to confirm melchior has genuinely taken over language before routing there
# (no point sending a real request to a melchior that isn't running it).
LLM_API_BY_DEVICE = {
    "balthazar": "http://192.168.8.133:8802",
    "melchior": "http://192.168.8.175:8802",
}
LLM_DEFAULT_DEVICE = "balthazar"
LLM_CHAT_TIMEOUT_S = 90
# Conversation history lives on repositorium, not on melchior/balthazar --
# magi-conversation-api (~/magi/dashboard/conversation_api.py, port 8803)
# owns the Postgres llm_conversations table directly (same box as
# data_api.py, same credential file). melchior/balthazar's magi_llm_api.py
# calls it internally when generating a reply; this dashboard proxy is a
# separate read path so the frontend can render the stored thread on page
# load without going through either LLM node.
CONVERSATION_API = "http://192.168.8.184:8803"
CONVERSATION_HISTORY_TIMEOUT_S = 10
# TTS ("read responses aloud" toggle) -- magi-tts.service, a small standalone
# Piper TTS synthesis service running on THIS box (audire-videre), not on
# melchior/balthazar. Piper is pure Python + ONNX, CPU-only, no Hailo NPU
# involvement at all (verified by reading hailo-apps' own
# gen_ai_utils/voice_processing/text_to_speech.py -- it never imports
# hailo_platform), so there's no reason to put it on the Hailo nodes and
# every reason not to (avoids any risk to magi-live-detect/magi-llm-api
# there). Deliberately decoupled from /api/llm/chat above: the frontend
# calls this as a second, independent step once it already has response
# text in hand, so this never touches magi_llm_api.py on either Pi.
TTS_API = "http://127.0.0.1:8804"
TTS_TIMEOUT_S = 20
# Live Navigation panel (added 2026-09-17) -- proxies to repositorium's
# magi-tools-api /nav/route (same process/port as the directions LLM tool,
# see tools_api.py's compute_route()), NOT to the LLM chat path. This is a
# dedicated opt-in-GPS UI feature, polled directly by the frontend on a
# timer -- the model never orchestrates it. Stateless passthrough: no
# position data is stored on this box or forwarded anywhere except the
# single repositorium request/response per poll.
TOOLS_API = "http://192.168.8.184:8804"
NAV_ROUTE_TIMEOUT_S = 10

SSH_TIMEOUT = 6

# Nodes and how to reach/control them. Hailo nodes get a detect-control
# script pair; repositorium/edgerunner are health-only (no HAT to control).
NODES = {
    # melchior's live-detect process was promoted to a systemd unit
    # (magi-live-detect, Restart=on-failure) by a concurrent session on
    # 2026-09-15 — control it via systemctl, not the raw fuser-kill script,
    # or systemd would just respawn it out from under a "stop" click.
    "melchior": {
        "host": "192.168.8.175", "role": "Hailo-10H detection node",
        "hailo": True,
        "start_cmd": "sudo systemctl start magi-live-detect",
        "stop_cmd": "sudo systemctl stop magi-live-detect",
        "restart_cmd": "sudo systemctl restart magi-live-detect",
    },
    # balthazar has no systemd unit yet (out of scope for that concurrent
    # session) — still controlled via the raw start/stop scripts, which kill
    # by `fuser /dev/hailo0` rather than process/script name (hailo_apps
    # calls setproctitle(), so a plain pkill -f silently matches nothing).
    "balthazar": {
        "host": "192.168.8.133", "role": "Hailo-10H detection node",
        "hailo": True,
        "start_cmd": "~/magi/balthazar/start_detect.sh",
        "stop_cmd": "~/magi/balthazar/stop_detect.sh",
    },
    "repositorium": {
        "host": "192.168.8.184", "role": "Postgres + Redis backend",
        "hailo": False,
    },
    "edgerunner": {
        "host": "10.0.0.137 (Netgear-switch segment)", "role": "Netgear-switch vantage point / tailnet node",
        "hailo": False,
    },
}


def _ssh(alias, remote_cmd, timeout=SSH_TIMEOUT):
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", alias, remote_cmd],
            capture_output=True, text=True, timeout=timeout + 3,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "ssh timed out"
    except Exception as e:
        return -1, "", str(e)


def _node_health(name):
    cfg = NODES[name]
    t0 = time.time()
    rc, out, err = _ssh(name, "uptime -p 2>/dev/null; cut -d' ' -f1-3 /proc/loadavg")
    latency_ms = round((time.time() - t0) * 1000)
    result = {
        "name": name, "role": cfg["role"], "host": cfg["host"],
        "reachable": rc == 0, "latency_ms": latency_ms if rc == 0 else None,
    }
    if rc == 0 and out:
        lines = out.splitlines()
        result["uptime"] = lines[0] if lines else None
        result["load"] = lines[1] if len(lines) > 1 else None
    else:
        result["error"] = err or "unreachable"

    if cfg.get("hailo"):
        if rc == 0:
            # NOTE: hailo_apps calls setproctitle(), so magi_detect.py shows up in
            # ps/pgrep as "Hailo Detection App" rather than by script name — AND a
            # naive `pgrep -f 'Hailo Detection App'` run over SSH self-matches the
            # wrapping shell's own command line (which contains that same literal
            # string), producing a false "running" positive. Query the actual Hailo
            # device holder instead, same approach the stop_*.sh scripts use.
            rc2, hstat, _ = _ssh(name, "sudo fuser /dev/hailo0 2>/dev/null")
            result["detect_status"] = "running" if hstat.strip() else "stopped"
            _, scan, _ = _ssh(name, "hailortcli scan 2>&1 | grep -c 'Device:'")
            result["hailo_device_present"] = scan.strip() not in ("", "0")
        else:
            result["detect_status"] = "unknown"
            result["hailo_device_present"] = None
    return result


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/nodes")
def api_nodes():
    results = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_node_health, name): name for name in NODES}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                results[name] = {"name": name, "reachable": False, "error": str(e)}
    return jsonify(nodes=results)


@app.post("/api/detect/<node>/<action>")
def api_detect_control(node, action):
    if node not in NODES or not NODES[node].get("hailo"):
        return jsonify(ok=False, error="unknown or non-Hailo node"), 404
    if action not in ("start", "stop", "restart"):
        return jsonify(ok=False, error="action must be start/stop/restart"), 400

    cfg = NODES[node]
    if action == "stop":
        cmd = cfg["stop_cmd"]
    elif action == "restart" and "restart_cmd" in cfg:
        cmd = cfg["restart_cmd"]
    else:  # start, or restart on a node whose start script already stops-then-starts internally
        cmd = cfg["start_cmd"]

    rc, out, err = _ssh(node, cmd, timeout=15)
    return jsonify(ok=(rc == 0), returncode=rc, stdout=out[-2000:], stderr=err[-2000:])


@app.get("/api/topology")
def api_topology():
    return jsonify(
        segments=[
            {
                "name": "magi-cluster LAN (192.168.8.0/24)",
                "gateway": "GL-iNet router 192.168.8.1 -> Ubiquiti 2.5G switch 192.168.8.155",
                "nodes": ["melchior (192.168.8.175)", "balthazar (192.168.8.133)",
                          "repositorium (192.168.8.184)", "audire-videre (192.168.8.136, this dashboard)"],
            },
            {
                "name": "Netgear GS308EPP segment (10.0.0.0/24, Xfinity-modem side)",
                "gateway": "10.0.0.214",
                "nodes": ["edgerunner (10.0.0.137, also on Tailscale tail944f9e.ts.net)",
                          "D555 camera power splitter (switch port 5, data path unused -- camera is USB-direct to audire-videre)"],
            },
        ],
        edges=[
            {"from": "audire-videre", "to": "melchior", "via": "RTP/H264 over UDP :5000 (D555 color feed)"},
            {"from": "melchior", "to": "repositorium", "via": "Redis XADD magi:detections"},
            {"from": "balthazar", "to": "repositorium", "via": "Redis XADD magi:detections"},
            {"from": "repositorium", "to": "repositorium", "via": "magi-worker consumes stream -> Postgres events"},
            {"from": "audire-videre", "to": "repositorium", "via": "dashboard data API :8801 (this app)"},
            {"from": "edgerunner", "to": "*", "via": "Tailscale tailnet vantage point (separate network segment)"},
        ],
    )


@app.get("/api/data/recent")
def api_data_recent():
    try:
        r = requests.get(f"{DATA_API}/api/events/recent", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify(error=str(e), events=[]), 502


@app.get("/api/data/stats")
def api_data_stats():
    try:
        r = requests.get(f"{DATA_API}/api/events/stats", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify(error=str(e)), 502


@app.get("/api/data/stream-info")
def api_data_stream_info():
    try:
        r = requests.get(f"{DATA_API}/api/stream/info", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify(error=str(e)), 502


@app.get("/api/failover/status")
def api_failover_status():
    """Proxy to repositorium's data API for magi:heartbeat:* keys (see
    magi-failover.md) -- read-only, same pattern as the other /api/data/*
    proxies above."""
    try:
        r = requests.get(f"{DATA_API}/api/failover/status", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify(error=str(e)), 502


# Manual failover override -- mirrors magi_failover.py's own `ctl` mode
# (running the exact same start_role/stop_role code, including balthazar's
# hailo-ollama coupling) so a manual click and the automatic watchdog can
# never fight each other or leave hailo-ollama stopped incorrectly. Node's
# own magi_failover.py file must exist there already (it does, deployed
# alongside magi-heartbeat/magi-failover services).
FAILOVER_CTL_NODES = {"melchior": "melchior", "balthazar": "balthazar"}


@app.post("/api/failover/<node>/<action>/<role>")
def api_failover_ctl(node, action, role):
    if node not in FAILOVER_CTL_NODES:
        return jsonify(ok=False, error="unknown node"), 404
    if action not in ("start", "stop"):
        return jsonify(ok=False, error="action must be start/stop"), 400
    if role not in ("vision", "language"):
        return jsonify(ok=False, error="role must be vision/language"), 400

    remote_dir = f"~/magi/{node}"
    cmd = f"python3 {remote_dir}/magi_failover.py ctl {action} {role}"
    rc, out, err = _ssh(node, cmd, timeout=25)
    return jsonify(ok=(rc == 0), returncode=rc, stdout=out[-2000:], stderr=err[-2000:])


def _llm_failover_device():
    """If balthazar (the default) is down, check /api/failover/status and
    return melchior only if melchior's language role is actually
    running/healthy there right now (i.e. it genuinely took over via
    failover, or was manually force-started) -- no point routing to a
    melchior that isn't serving language. Returns None if no healthy
    fallback is available (caller then just reports balthazar's own error)."""
    try:
        r = requests.get(f"{DATA_API}/api/failover/status", timeout=5)
        r.raise_for_status()
        status = r.json()
        lang = status.get("devices", {}).get("melchior", {}).get("roles", {}).get("language", {})
        if lang.get("running") and lang.get("status") in ("healthy", "stale"):
            return "melchior"
    except Exception as e:
        app.logger.warning("failover status check failed while picking LLM fallback: %s", e)
    return None


@app.post("/api/openwebui/<action>")
def api_openwebui_control(action):
    """On-demand toggle for balthazar's Open WebUI/hailo-ollama (see
    ~/magi/balthazar/start_openwebui.sh / stop_openwebui.sh, and
    magi_failover.py's `ctl openwebui` mode) -- since the 2026-09-16 role
    flip, balthazar's normal duty is magi-llm-api, so starting Open WebUI
    means stopping magi-llm-api first (same chip-sharing conflict the vision
    failover coupling logic was built around), and stopping it restores
    magi-llm-api. Mirrors api_detect_control's SSH-a-script pattern above."""
    if action not in ("start", "stop"):
        return jsonify(ok=False, error="action must be start/stop"), 400
    cmd = f"~/magi/balthazar/{action}_openwebui.sh"
    rc, out, err = _ssh("balthazar", cmd, timeout=20)
    return jsonify(ok=(rc == 0), returncode=rc, stdout=out[-2000:], stderr=err[-2000:])


@app.post("/api/llm/chat")
def api_llm_chat():
    """Proxy to a node's local Hailo-10H LLM API — 'offload to a physical
    agent instead of cloud Claude'. Defaults to balthazar (its normal duty
    since the 2026-09-16 role flip); falls back to melchior only if
    balthazar is unreachable/erroring AND melchior's language role shows
    healthy in /api/failover/status (i.e. it's actually taken over). Simple
    try-then-check-status fallback, not a load balancer — normal duty always
    tries first."""
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    if not prompt:
        return jsonify(error="missing 'prompt'"), 400

    # Optional lat/lon (added 2026-09-18 for the find_nearby tool, "find me
    # directions to the nearest X") -- a one-time navigator.geolocation
    # .getCurrentPosition() reading the frontend attaches to this specific
    # chat message (static/index.html's submitChat()), separate from the
    # existing Live Navigation panel's continuous watchPosition(). Passed
    # straight through to whichever node's magi_llm_api.py handles the
    # request; not validated here beyond type coercion -- that node's
    # find_nearby runner treats missing/invalid lat/lon as "no location
    # available" rather than erroring, same as if this dashboard were never
    # in the path at all (e.g. a future Telegram bot calling melchior/
    # balthazar's /chat directly with its own location source).
    llm_payload = {"prompt": prompt, "session_id": session_id}
    try:
        raw_lat, raw_lon = body.get("lat"), body.get("lon")
        if raw_lat is not None and raw_lon is not None:
            llm_payload["lat"] = float(raw_lat)
            llm_payload["lon"] = float(raw_lon)
    except (TypeError, ValueError):
        pass

    device = LLM_DEFAULT_DEVICE
    try:
        r = requests.post(
            f"{LLM_API_BY_DEVICE[device]}/chat",
            json=llm_payload,
            timeout=LLM_CHAT_TIMEOUT_S,
        )
        payload = r.json()
        payload["served_by"] = device
        return jsonify(payload), r.status_code
    except requests.exceptions.Timeout:
        return jsonify(error="timeout", detail=f"{device}'s LLM did not respond within {LLM_CHAT_TIMEOUT_S}s", served_by=device), 504
    except Exception as first_err:
        fallback = _llm_failover_device()
        if not fallback:
            return jsonify(error=str(first_err), served_by=device), 502
        try:
            r = requests.post(
                f"{LLM_API_BY_DEVICE[fallback]}/chat",
                json=llm_payload,
                timeout=LLM_CHAT_TIMEOUT_S,
            )
            payload = r.json()
            payload["served_by"] = fallback
            payload["failover"] = True
            return jsonify(payload), r.status_code
        except Exception as fallback_err:
            return jsonify(
                error=f"{device} failed ({first_err}); fallback {fallback} also failed ({fallback_err})",
                served_by=None,
            ), 502


@app.get("/api/llm/history/<session_id>")
def api_llm_history(session_id):
    """Proxy to repositorium's magi-conversation-api for loading a session's
    stored chat thread on page load / reload, so the dashboard can render a
    real scrollable conversation instead of only ever showing the latest
    response. Read-only passthrough; response shape from upstream is
    {"session_id": ..., "turns": [{"role", "content", "created_at"}, ...]}
    oldest-first."""
    limit = request.args.get("limit", 50, type=int)
    limit = max(1, min(limit, 200))
    try:
        r = requests.get(
            f"{CONVERSATION_API}/conversations/{session_id}",
            params={"limit": limit},
            timeout=CONVERSATION_HISTORY_TIMEOUT_S,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify(error=str(e), session_id=session_id, turns=[]), 502


@app.delete("/api/llm/session/<session_id>")
def api_llm_clear_session(session_id):
    """Proxy for the dashboard's 'New conversation' button — clears a
    session's stored history on whichever node currently owns language duty
    (repositorium's magi-conversation-api actually owns the Postgres table,
    so hitting either node's forwarder works, but we try the default first)."""
    for device in (LLM_DEFAULT_DEVICE, "melchior"):
        try:
            r = requests.delete(f"{LLM_API_BY_DEVICE[device]}/session/{session_id}", timeout=SSH_TIMEOUT)
            return jsonify(r.json()), r.status_code
        except Exception as e:
            last_err = e
    return jsonify(error=str(last_err)), 502


@app.post("/api/tts")
def api_tts():
    """Proxy to magi-tts.service (Piper, running locally on audire-videre --
    see TTS_API comment above). Takes the same {"text": ...} the frontend
    already has in hand from a rendered chat response and returns the raw
    audio/wav bytes straight through for the browser's <audio> element."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify(error="missing 'text'"), 400
    try:
        r = requests.post(f"{TTS_API}/tts", json={"text": text}, timeout=TTS_TIMEOUT_S)
        if r.status_code != 200:
            return jsonify(error="tts service error", detail=r.text[:1000]), r.status_code
        return Response(r.content, content_type="audio/wav")
    except requests.exceptions.Timeout:
        return jsonify(error="timeout", detail=f"tts did not respond within {TTS_TIMEOUT_S}s"), 504
    except Exception as e:
        return jsonify(error=str(e)), 502


@app.post("/api/nav/route")
def api_nav_route():
    """Proxy to repositorium's magi-tools-api /nav/route for the dashboard's
    Live Navigation panel (opt-in browser GPS -> self-hosted OSRM routing,
    periodically re-polled as position updates -- see static/index.html's
    startNavigation()/navMaybeRoute()). Deliberately separate from
    /api/llm/chat above: this is never routed through the LLM tool-calling
    system, just a plain stateless HTTP passthrough of whatever lat/lon the
    browser just read from navigator.geolocation. Nothing is logged or
    persisted here beyond this single request/response."""
    body = request.get_json(silent=True) or {}
    try:
        r = requests.post(f"{TOOLS_API}/nav/route", json=body, timeout=NAV_ROUTE_TIMEOUT_S)
        return jsonify(r.json()), r.status_code
    except requests.exceptions.Timeout:
        return jsonify(ok=False, error="timeout", detail=f"routing did not respond within {NAV_ROUTE_TIMEOUT_S}s"), 504
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 502


@app.get("/api/camera/mjpeg")
def api_camera_mjpeg():
    def gen():
        with requests.get(f"{MJPEG_SOURCE}/mjpeg", stream=True, timeout=10) as upstream:
            for chunk in upstream.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
    try:
        return Response(stream_with_context(gen()),
                         content_type="multipart/x-mixed-replace; boundary=frame")
    except Exception as e:
        return jsonify(error=str(e)), 502


@app.get("/api/camera/snapshot.jpg")
def api_camera_snapshot():
    try:
        r = requests.get(f"{MJPEG_SOURCE}/snapshot.jpg", timeout=5)
        return Response(r.content, content_type="image/jpeg")
    except Exception as e:
        return jsonify(error=str(e)), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, threaded=True)
