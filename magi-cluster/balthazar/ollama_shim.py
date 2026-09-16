#!/usr/bin/env python3
"""Thin compatibility shim between Open WebUI (or any standard Ollama client)
and hailo-ollama, whose request parser is stricter than real Ollama's API.
Fixes two known incompatibilities before forwarding:
  - drops an empty "tools" array (hailo-ollama 500s on it; real Ollama allows it)
  - normalizes "keep_alive" duration strings (e.g. "5m") to seconds (hailo-ollama
    only accepts a plain number; real Ollama accepts both)
Everything else passes through untouched, streaming included.
"""
import re
import requests
from flask import Flask, request, Response

UPSTREAM = "http://localhost:8000"
app = Flask(__name__)

DURATION_RE = re.compile(r"^(\d+)([smh])$")


def fix_payload(data: dict) -> dict:
    if isinstance(data.get("tools"), list) and len(data["tools"]) == 0:
        del data["tools"]
    ka = data.get("keep_alive")
    if isinstance(ka, str):
        m = DURATION_RE.match(ka)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            data["keep_alive"] = n * {"s": 1, "m": 60, "h": 3600}[unit]
        else:
            del data["keep_alive"]
    return data


@app.route("/<path:path>", methods=["GET", "POST", "DELETE", "HEAD"])
def proxy(path):
    upstream_url = f"{UPSTREAM}/{path}"
    json_body = None
    if request.method == "POST" and request.is_json:
        json_body = fix_payload(request.get_json())

    upstream_resp = requests.request(
        method=request.method,
        url=upstream_url,
        json=json_body,
        params=request.args,
        stream=True,
        timeout=120,
    )

    def generate():
        for chunk in upstream_resp.iter_content(chunk_size=4096):
            if chunk:
                yield chunk

    return Response(
        generate(),
        status=upstream_resp.status_code,
        content_type=upstream_resp.headers.get("Content-Type", "application/json"),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, threaded=True)
