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
# melchior's local Hailo-10H LLM API (magi-llm-api.service) — see
# ~/magi/melchior/magi_llm_api.py and magi-local-llm-feasibility.md.
# Serialized on melchior's side (one GenAI session at a time); this proxy
# just forwards and relays whatever status melchior's own busy/error
# handling returns, with a generous timeout since generation can take
# several seconds to ~1 minute (code-gen prompts measured up to ~45s).
LLM_API = "http://192.168.8.175:8802"
LLM_CHAT_TIMEOUT_S = 90

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
                "nodes": ["edgerunner (10.0.0.137, also on Tailscale <your-tailnet>.ts.net)",
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


@app.post("/api/llm/chat")
def api_llm_chat():
    """Proxy to melchior's local Hailo-10H LLM API — 'offload to a physical
    agent instead of cloud Claude'. melchior serializes GenAI requests
    itself (HailoRT allows only one session at a time per chip), so a 429
    from there is passed straight through rather than retried here."""
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    if not prompt:
        return jsonify(error="missing 'prompt'"), 400
    try:
        r = requests.post(
            f"{LLM_API}/chat",
            json={"prompt": prompt, "session_id": session_id},
            timeout=LLM_CHAT_TIMEOUT_S,
        )
        return jsonify(r.json()), r.status_code
    except requests.exceptions.Timeout:
        return jsonify(error="timeout", detail=f"melchior's LLM did not respond within {LLM_CHAT_TIMEOUT_S}s"), 504
    except Exception as e:
        return jsonify(error=str(e)), 502


@app.delete("/api/llm/session/<session_id>")
def api_llm_clear_session(session_id):
    """Proxy for the dashboard's 'New conversation' button — clears a
    session's stored history (melchior forwards this to repositorium's
    magi-conversation-api, which owns the actual Postgres table)."""
    try:
        r = requests.delete(f"{LLM_API}/session/{session_id}", timeout=SSH_TIMEOUT)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify(error=str(e)), 502


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
