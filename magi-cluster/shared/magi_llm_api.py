#!/usr/bin/env python3
"""magi-llm-api — HTTP wrapper exposing this node's Hailo-10H local LLM
(Qwen2.5-Coder-1.5B-Instruct) as a `POST /chat` endpoint for the magi-cluster
dashboard's "offload to a physical agent" feature.

Shared script, deployed identically to melchior and balthazar (dedup
refactor, 2026-09-16) — this file has no device-specific config at all (it
doesn't need DEVICE/PEER; the Hailo/HailoRT calls are local to whichever
node runs it), so no per-node config file entry is required here.

Built on the exact code path proven working in the magi-local-llm-feasibility
research (Session 4, 2026-09-16): `hailo_platform.genai.LLM` +
`gen_ai_utils.llm_utils.streaming.generate_and_stream_response`, the same
calls `agent_tools_example/testing/harness.py`'s `AgentTestHarness` and the
interactive agent itself use — NOT a reimplementation, NOT a shell-out to the
CLI.

v2 (2026-09-16, same day, follow-up session) adds two things on top of the v1
one-shot Q&A/code-gen path:

1. **Tool calling.** Reuses agent_tools_example's own machinery verbatim:
   `system_prompt.create_system_prompt()` to embed tool schemas as text in
   the system prompt (schema/name/description text still comes straight from
   the vendored `tools/{math,weather}` modules — unchanged, so what's
   described to the model hasn't moved), `tool_parsing.parse_function_call()`
   to pull a `<tool_call>{...}</tool_call>` JSON block out of the raw
   generation, and `tool_execution.execute_tool_call()` to dispatch the call.

   v3 (2026-09-17): tool *execution* itself no longer runs locally. Each
   tool's "runner" is now a thin HTTP call to a new central tool-execution
   API on repositorium (`magi-tools-api.service`, port 8804,
   ~/magi/dashboard/tools_api.py) rather than the vendored tools' own local
   `run()` functions. This closes the same drift risk the 2026-09-16 dedup
   session found and fixed for magi_detect.py/magi_heartbeat.py/etc: melchior
   and balthazar each have their own separate ~/hailo-apps checkout, and the
   weather tool specifically has already needed two in-place hotfixes there
   (Session 10's location-hallucination fix, Session 11's include_rain
   silent-omission fix) that live outside this repo and would be silently
   lost on a future reinstall/`git pull` on either node — see
   magi-local-llm-feasibility.md. repositorium's tools_api.py is now the
   single source of truth for tool *logic*; both nodes' copies of the
   vendored tools/{math,weather,directions} modules are only still imported
   here for their `name`/`TOOLS_SCHEMA`/`description` metadata (what gets
   shown to the model), never for their `run()` functions anymore. Only the
   **math**, **weather**, and **directions** tools are wired in (all
   pure-Python / network-only — no GPIO). The hardware tools
   (rgb_led/servo/elevator) are deliberately never imported here, so there's
   no path to real hardware from this service.

   **directions** (added 2026-09-17, see magi-osrm-routing.md): geocodes via
   Nominatim then routes via a self-hosted OSRM instance also running on
   repositorium (osrm-backend.service, port 5000) — the /tools/directions
   endpoint on repositorium's tools_api calls that OSRM instance over
   localhost/LAN itself, so this node only ever talks to the one tools_api
   HTTP endpoint, same as math/weather.

2. **Conversation history.** Sessions are opaque IDs the dashboard generates
   client-side. History is stored in repositorium's Postgres via a small
   HTTP wrapper (`magi-conversation-api.service`, repositorium:8803) — this
   service has no direct Postgres credentials itself, matching the existing
   architecture where only repositorium talks to Postgres directly. Each
   request: load prior turns for the session (bounded window, see
   MAX_HISTORY_MESSAGES/MAX_HISTORY_CHARS below — a simplified stand-in for
   agent_tools_example's own context_manager 80%-capacity trim, adapted
   because this service can't keep a persistent per-session LLM context
   across HTTP requests from potentially different callers), replay them
   into a freshly cleared context alongside the tool-aware system prompt,
   generate the new turn, then append both the user and assistant turns
   back to Postgres.

Critical constraint (see magi-local-llm-feasibility.md "running_parallel.md"
+ Session 4's transient under-voltage/throttle observation): a Hailo-10H can
only run ONE GenAI session at a time, and sustained generation concurrent
with the live YOLO detection pipeline can strain a node's current power
supply. This service therefore:
  - serializes all /chat requests behind a single lock (non-blocking
    acquire — a second concurrent request gets a clear 429 "busy" response
    instead of queueing/piling up or being silently delayed)
  - does no aggressive auto-retry
  - a tool-calling turn costs up to two generate() calls (the initial
    response, then a second pass to turn the tool result into a reply) —
    still one request under the same lock, no extra concurrency
"""
import io
import json
import logging
import threading
import time
from contextlib import redirect_stdout

import requests
from flask import Flask, jsonify, request as flask_request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("magi_llm_api")

app = Flask(__name__)

MODEL_NAME = "Qwen2.5-Coder-1.5B-Instruct"
TEMPERATURE = 0.2
SEED = 42
MAX_GENERATED_TOKENS = 600  # generous headroom for code-gen answers (Session 4 saw 222-word answers)
GEN_LOCK_TIMEOUT_S = 0  # non-blocking: reject immediately rather than queue

BASE_SYSTEM_PROMPT = (
    "You are a helpful, concise AI assistant running locally on a Hailo-10H "
    "NPU (no cloud model involved). Answer general questions clearly and "
    "write correct, working code when asked for coding help. "
    "When a tool call needs information the user hasn't given you yet (such as "
    "a location) and it isn't clear from the conversation so far, do not guess "
    "or invent a value and do not call the tool — ask the user directly instead, "
    "then wait for their answer."
)

# --- Conversation history (repositorium's magi-conversation-api) ---
CONV_API = "http://192.168.8.184:8803"
CONV_API_TIMEOUT_S = 5
MAX_HISTORY_MESSAGES = 20  # ~10 turns of user+assistant
MAX_HISTORY_CHARS = 6000   # crude char budget (~1500 tokens) for replayed history

# --- Tool execution (repositorium's magi-tools-api, v3 2026-09-17) ---
# Central, single-source-of-truth tool logic — see the module docstring.
# A tool call here is one extra LAN hop (node -> repositorium -> Open-Meteo
# for weather), so the timeout is generous but still well under the overall
# request's own no-queueing/no-retry philosophy: a slow/unreachable
# tools_api just surfaces as a normal tool "error" result fed back to the
# model, exactly the same as any other tool failure this code already
# handled before tool execution moved off-node.
TOOLS_API = "http://192.168.8.184:8804"
TOOLS_API_TIMEOUT_S = 15
# find_nearby (added 2026-09-18) can involve two sequential Overpass API
# calls server-side (default + one-time wider-radius retry, see tools_api.py)
# against a public instance measured taking up to ~25s each during real
# testing, plus an OSRM hop -- generous timeout accordingly.
FIND_NEARBY_TIMEOUT_S = 65

_lock = threading.Lock()
_state = {
    "llm": None,
    "vdevice": None,
    "initialized": False,
    "tools_lookup": None,
    "system_prompt_text": None,
    # Per-request location, set fresh at the top of every chat() call (see
    # below) and read only by _find_nearby_runner(). Safe as shared mutable
    # state (not per-request-scoped) ONLY because _lock above already
    # serializes this whole service to one /chat request in flight at a
    # time -- there is no concurrent request that could see another
    # request's coordinates. If that serialization is ever relaxed, this
    # needs to become real per-request state instead.
    "request_geo": None,
}


def _remote_tool_runner(tool_name: str):
    """Return a `runner(args) -> dict` closure matching the exact interface
    `tool_execution.execute_tool_call()` expects from a local tool's own
    `run()` function (dict with 'ok' plus 'result' or 'error') — except this
    one makes an HTTP call to repositorium's central magi-tools-api instead
    of running the logic in this process. Kept as small closures rather than
    reimplementing execute_tool_call() so the surrounding tool-calling flow
    (parse -> dispatch -> feed result back) is completely unchanged; only
    what a runner actually *does* when called has moved off-node.
    """

    def _runner(args: dict) -> dict:
        try:
            r = requests.post(
                f"{TOOLS_API}/tools/{tool_name}", json=args, timeout=TOOLS_API_TIMEOUT_S,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("Remote tool '%s' call to %s failed: %s", tool_name, TOOLS_API, e)
            return {"ok": False, "error": f"Tool service unavailable ({tool_name}): {e}"}

    return _runner


def _find_nearby_runner(args: dict) -> dict:
    """Dedicated runner for the find_nearby tool (added 2026-09-18) --
    NOT built via the generic _remote_tool_runner() factory above, because
    this tool needs real lat/lon injected from a channel the model never
    touches, rather than passing through whatever arguments the model
    generated verbatim.

    This is the reliability fix the task called for: the find_nearby tool's
    schema (see the vendored tools/find_nearby module) does not ask the
    model for lat/lon at all, precisely so it can't hallucinate coordinates
    the way the weather tool once hallucinated a city name (Session 10,
    magi-local-llm-feasibility.md). Instead, the dashboard frontend grabs a
    one-time navigator.geolocation.getCurrentPosition() reading when the
    user sends a chat message and includes it as top-level lat/lon fields on
    the POST /chat request body; chat() below stores that in
    _state['request_geo'] before generation starts, and this runner reads it
    at call time and merges it into the args actually sent to
    repositorium's /tools/find_nearby -- overwriting anything the model
    itself put there, not just filling gaps.

    If the frontend never captured a location this turn (denied permission,
    browser API unavailable, or a caller -- e.g. a future Telegram bot --
    that doesn't send lat/lon at all), this returns a clear, honest error
    without ever calling out to repositorium, matching the weather tool's
    "ask, don't guess" pattern rather than silently failing or making up a
    location.
    """
    geo = _state.get("request_geo")
    if not geo:
        return {
            "ok": False,
            "error": (
                "I don't have your location — try enabling location access, or tell me a "
                "location to search near."
            ),
        }
    payload = {"query": args.get("query"), "lat": geo["lat"], "lon": geo["lon"]}
    try:
        r = requests.post(
            f"{TOOLS_API}/tools/find_nearby", json=payload, timeout=FIND_NEARBY_TIMEOUT_S,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("Remote tool 'find_nearby' call to %s failed: %s", TOOLS_API, e)
        return {"ok": False, "error": f"Tool service unavailable (find_nearby): {e}"}


def _ensure_initialized() -> None:
    if _state["initialized"]:
        return

    from hailo_platform import VDevice
    from hailo_platform.genai import LLM

    from hailo_apps.python.core.common.core import resolve_hef_path
    from hailo_apps.python.core.common.defines import AGENT_APP, HAILO10H_ARCH

    hef_path = resolve_hef_path(hef_path=MODEL_NAME, app_name=AGENT_APP, arch=HAILO10H_ARCH)
    if not hef_path:
        raise RuntimeError("Failed to resolve HEF path for " + MODEL_NAME)

    logger.info("Initializing VDevice + LLM (hef=%s) ...", hef_path)
    t0 = time.perf_counter()

    params = VDevice.create_params()
    params.group_id = "SHARED"  # matches agent_tools_example: coexist with magi-live-detect's vdevice group
    vdevice = VDevice(params)
    llm = LLM(vdevice, str(hef_path))

    _state["vdevice"] = vdevice
    _state["llm"] = llm

    # Build the tool-calling system prompt using agent_tools_example's own
    # generator, with only math + weather registered (no GPIO tools loaded).
    # NOTE (v3, 2026-09-17): these vendored modules are imported for their
    # name/TOOLS_SCHEMA/description metadata ONLY (what gets shown to the
    # model — unchanged) — their own run() functions are never called here
    # anymore. See _remote_tool_runner() below: actual execution is now a
    # thin HTTP call to repositorium's central magi-tools-api.
    from hailo_apps.python.gen_ai_apps.agent_tools_example import system_prompt as at_system_prompt
    from hailo_apps.python.gen_ai_apps.agent_tools_example.tools import math as math_tool
    from hailo_apps.python.gen_ai_apps.agent_tools_example.tools import weather as weather_tool
    from hailo_apps.python.gen_ai_apps.agent_tools_example.tools import directions as directions_tool
    from hailo_apps.python.gen_ai_apps.agent_tools_example.tools import find_nearby as find_nearby_tool

    tools = [
        {"name": math_tool.name, "tool_def": math_tool.TOOLS_SCHEMA[0],
         "description": math_tool.description, "runner": _remote_tool_runner("math")},
        {"name": weather_tool.name, "tool_def": weather_tool.TOOLS_SCHEMA[0],
         "description": weather_tool.description, "runner": _remote_tool_runner("weather")},
        {"name": directions_tool.name, "tool_def": directions_tool.TOOLS_SCHEMA[0],
         "description": directions_tool.description, "runner": _remote_tool_runner("directions")},
        {"name": find_nearby_tool.name, "tool_def": find_nearby_tool.TOOLS_SCHEMA[0],
         "description": find_nearby_tool.description, "runner": _find_nearby_runner},
    ]
    tool_prompt_text = at_system_prompt.create_system_prompt(tools)
    _state["tools_lookup"] = {t["name"]: t for t in tools}
    _state["system_prompt_text"] = BASE_SYSTEM_PROMPT + "\n\n" + tool_prompt_text

    _state["initialized"] = True
    logger.info(
        "LLM ready in %.1fs (tools: %s)",
        time.perf_counter() - t0, ", ".join(_state["tools_lookup"].keys()),
    )


def _location_mentioned_by_user(location: str, prompt_text: str, history: list) -> bool:
    """Guard against the model hallucinating a real-sounding but invented city
    for the weather tool (observed live: "New York City, USA" on a
    location-less prompt) — the tool's own placeholder-string safety net
    (tools/weather/tool.py) can't catch this since a hallucinated real city
    passes its checks fine. This is the second layer: only let a weather
    tool call through if the location the model chose was actually mentioned
    by the user themselves somewhere in this turn's prompt or their own prior
    turns in this session's history — not a perfect NER match, just a
    case-insensitive substring check on the location name (and, if given,
    the part before a comma, e.g. "Chicago" out of "Chicago, USA"), which is
    enough to reject a location conjured from nowhere.
    """
    if not location:
        return False
    needle = location.split(",")[0].strip().lower()
    if not needle:
        return False
    haystacks = [prompt_text or ""]
    for turn in history:
        if turn.get("role") == "user":
            haystacks.append(turn.get("content") or "")
    return any(needle in h.lower() for h in haystacks)


def _load_history(session_id: str):
    """Fetch prior turns for a session from repositorium's conversation API.
    Returns a list of {"role": ..., "content": ...} dicts, oldest first,
    trimmed to MAX_HISTORY_CHARS from the most-recent end. Best-effort: any
    failure (repositorium unreachable, etc.) just means no history is used,
    it does not fail the request."""
    if not session_id:
        return []
    try:
        r = requests.get(
            f"{CONV_API}/conversations/{session_id}",
            params={"limit": MAX_HISTORY_MESSAGES},
            timeout=CONV_API_TIMEOUT_S,
        )
        r.raise_for_status()
        turns = r.json().get("turns", [])
    except Exception as e:
        logger.warning("Failed to load history for session %s: %s", session_id, e)
        return []

    # Trim from the oldest end if the replayed text would be too large.
    kept = []
    total_chars = 0
    for turn in reversed(turns):  # newest first for the budget pass
        content = turn.get("content", "")
        total_chars += len(content)
        if total_chars > MAX_HISTORY_CHARS and kept:
            break
        kept.append(turn)
    kept.reverse()  # back to oldest-first for replay
    return [{"role": t["role"], "content": t["content"]} for t in kept]


def _save_turn(session_id: str, role: str, content: str) -> None:
    if not session_id:
        return
    try:
        requests.post(
            f"{CONV_API}/conversations/{session_id}",
            json={"role": role, "content": content},
            timeout=CONV_API_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning("Failed to save %s turn for session %s: %s", role, session_id, e)


@app.get("/health")
def health():
    return jsonify(
        ok=True, initialized=_state["initialized"], busy=_lock.locked(),
        tools=list(_state["tools_lookup"].keys()) if _state["tools_lookup"] else [],
    )


@app.post("/chat")
def chat():
    body = flask_request.get_json(silent=True) or {}
    prompt_text = (body.get("prompt") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    if not prompt_text:
        return jsonify(error="missing 'prompt'"), 400

    # Optional lat/lon (added 2026-09-18 for the find_nearby tool) -- a
    # one-time navigator.geolocation.getCurrentPosition() reading the
    # dashboard frontend attaches to this specific chat message, distinct
    # from the separate Live Navigation panel's continuous watchPosition().
    # Not required: a caller that never sends these (an older frontend, a
    # future Telegram bot without location access, geolocation denied) just
    # means find_nearby will decline with a clear "I don't have your
    # location" message rather than erroring or guessing -- see
    # _find_nearby_runner() above. Always explicitly set (to None if absent)
    # rather than left stale, so a request without location can never
    # accidentally reuse a previous request's coordinates.
    request_geo = None
    try:
        raw_lat, raw_lon = body.get("lat"), body.get("lon")
        if raw_lat is not None and raw_lon is not None:
            request_geo = {"lat": float(raw_lat), "lon": float(raw_lon)}
    except (TypeError, ValueError):
        request_geo = None

    got_lock = _lock.acquire(blocking=False)
    if not got_lock:
        return jsonify(
            error="busy",
            detail="this node's Hailo-10H can only run one LLM session at a time "
                   "(HailoRT limitation) and a request is already in progress. "
                   "Try again shortly.",
        ), 429

    _state["request_geo"] = request_geo  # see the comment above; read only by _find_nearby_runner()
    try:
        t0 = time.perf_counter()
        try:
            _ensure_initialized()
        except Exception as e:
            logger.exception("LLM init failed")
            return jsonify(error=f"init failed: {e}"), 500

        from hailo_apps.python.gen_ai_apps.gen_ai_utils.llm_utils import (
            context_manager, message_formatter, streaming, tool_execution, tool_parsing,
        )

        llm = _state["llm"]
        tools_lookup = _state["tools_lookup"]

        # Fresh context every request (this process may serve unrelated
        # sessions back to back) — history is replayed explicitly below
        # rather than kept live in the LLM's own context across requests.
        try:
            llm.clear_context()
        except Exception as e:
            logger.warning("clear_context failed (continuing anyway): %s", e)

        history = _load_history(session_id) if session_id else []

        try:
            context_messages = [message_formatter.messages_system(_state["system_prompt_text"])]
            for turn in history:
                if turn["role"] == "user":
                    context_messages.append(message_formatter.messages_user(turn["content"]))
                else:
                    context_messages.append(message_formatter.messages_assistant(turn["content"]))
            context_manager.add_to_context(llm, context_messages, logger)

            # Reuse agent_tools_example's own 80%-capacity check to log (not
            # act on, given the small bounded window above already keeps us
            # well under it in practice) whether we're approaching context
            # limits — matches context_manager.is_context_full's pattern.
            if context_manager.is_context_full(llm, context_threshold=0.8, logger_instance=logger):
                logger.warning(
                    "Context usage >=80%% after replaying %d history messages for session %s",
                    len(history), session_id or "(none)",
                )
        except Exception as e:
            logger.warning("Failed to prime context with history (continuing without it): %s", e)

        def _generate(prompt_messages, prefix="", seed=SEED, temperature=TEMPERATURE):
            f = io.StringIO()
            with redirect_stdout(f):  # this module prints tokens as it streams; keep it out of our stdout/journal
                return streaming.generate_and_stream_response(
                    llm=llm,
                    prompt=prompt_messages,
                    temperature=temperature,
                    seed=seed,
                    max_tokens=MAX_GENERATED_TOKENS,
                    prefix=prefix,
                    show_raw_stream=True,
                    token_callback=None,
                )

        try:
            raw_response = _generate([message_formatter.messages_user(prompt_text)])
        except Exception as e:
            logger.exception("generation failed")
            return jsonify(error=f"generation failed: {e}"), 500

        tool_used = None
        tool_result = None
        final_text = raw_response

        tool_call = tool_parsing.parse_function_call(raw_response)
        if tool_call is not None:
            tool_used = tool_call.get("name")
            tool_args = tool_call.get("arguments") or {}
            logger.info("Tool call detected: %s args=%s", tool_used, tool_args)
            if tool_used == "weather" and not _location_mentioned_by_user(
                tool_args.get("location", ""), prompt_text, history,
            ):
                logger.warning(
                    "Weather tool called with location %r not found anywhere in the "
                    "user's own words this session -- treating as a hallucinated "
                    "location, rejecting instead of executing", tool_args.get("location"),
                )
                tool_result = {
                    "ok": False,
                    "error": (
                        "'location' is required and was empty, missing, or a placeholder — "
                        "there is no default location. Do not guess or invent a city. Ask the "
                        "user which location they mean, then call this tool again once they've "
                        "told you."
                    ),
                }
            else:
                try:
                    tool_result = tool_execution.execute_tool_call(tool_call, tools_lookup)
                except Exception as e:
                    logger.exception("tool execution raised")
                    tool_result = {"ok": False, "error": str(e)}
            logger.info("Tool '%s' result: %s", tool_used, tool_result)

            def _tool_result_fallback_text():
                # Honest, always-valid fallback: render the tool's own real
                # result rather than anything the model generated.
                if tool_result.get("ok"):
                    return str(tool_result.get("result") or tool_result.get("error") or "")
                err = str(tool_result.get("error") or "")
                # Special-case: weather tool's own missing/placeholder-location
                # safety net (see tools/weather/tool.py) — surface this as an
                # actual clarifying question to the user rather than the raw
                # dev-facing error string, since this is the expected/handled
                # "ask instead of guess" path, not a real failure.
                if tool_used == "weather" and "no default location" in err.lower():
                    return "Sure — which city (or city, country) would you like the weather for?"
                return f"(tool '{tool_used}' failed: {err})"

            # Root-cause fix (2026-09-17): do NOT run a second generation call
            # to "prettify" the tool result into natural language. Every tool
            # this project has (math/weather/directions) already returns a
            # well-formed, natural-language-ready result string, and this
            # second _generate() call is the sole source of an entire
            # recurring bug class on this small 1.5B on-device model across
            # multiple prior sessions: degenerating to a single stray '<'
            # character (2026-09-16), re-hallucinating a brand-new unexecuted
            # <tool_call>{...}</tool_call> block that leaked to the user raw
            # (2026-09-16), and — the shape that prompted this fix — emitting
            # a hallucinated, wrong, irrelevant tool-schema blob wrapped in
            # <code>...</code> tags for a *successful* math call (2026-09-17,
            # reproduced 3/3 live on melchior: tool_result correctly
            # '12 + 15 = 27', but response was a garbage weather-schema dump).
            # That last shape didn't match parse_function_call()'s expected
            # <tool_call>{...}</tool_call> shape, so it slipped through the
            # prior degenerate/re-hallucination detectors undetected — yet
            # another narrow patch would only have covered this one new shape.
            # Skipping the second generation call entirely removes the whole
            # bug class at its root instead: it also cuts a full ~10-45s
            # generation call from every tool-using request, and removes the
            # "Context usage >=80%"/degenerate-response log noise for the
            # tool-use path entirely. Kept unconditional (success AND
            # failure) rather than only on success, since the tool's own
            # result/error text (via _tool_result_fallback_text(), which
            # already special-cases the weather "ask for location" case into
            # a natural clarifying question) is always valid and this was
            # already the existing fallback for every failure mode above.
            final_text = _tool_result_fallback_text()

        else:
            # No tool was used at all (plain Q&A/code-gen path). The small
            # on-device model occasionally degenerates to a near-empty reply
            # (observed: a single stray "<" token) after context-priming.
            # One bounded fallback regeneration (fresh context, same
            # system+history prime, different seed/temp) fixes this in
            # practice without unbounded retry. Safe to restart from the
            # original prompt ONLY here, because there's no tool-aware
            # framing that could cause the retry to hallucinate a tool call
            # (that failure mode is handled separately above).
            if len(final_text.strip()) < 5:
                logger.warning("Degenerate short response %r detected, retrying once with fresh context + different seed", final_text)
                try:
                    llm.clear_context()
                    context_manager.add_to_context(llm, context_messages, logger)
                    # Same seed/temp would deterministically reproduce the same
                    # degenerate output, so the fallback attempt varies both.
                    final_text = _generate(
                        [message_formatter.messages_user(prompt_text)],
                        seed=SEED + 1,
                        temperature=max(TEMPERATURE, 0.7),
                    )
                except Exception as e:
                    logger.warning("Fallback regeneration failed: %s", e)

        elapsed = time.perf_counter() - t0
        logger.info("chat request served in %.2fs (prompt=%r, tool=%s)", elapsed, prompt_text[:80], tool_used)

        _save_turn(session_id, "user", prompt_text)
        _save_turn(session_id, "assistant", final_text)

        return jsonify(
            response=final_text,
            elapsed_s=round(elapsed, 2),
            tool_used=tool_used,
            tool_result=tool_result,
        )
    finally:
        _state["request_geo"] = None  # never let a request's location outlive its own request
        _lock.release()


@app.delete("/session/<session_id>")
def clear_session(session_id):
    """Proxy-delete a session's history (dashboard's 'New conversation' button)."""
    try:
        r = requests.delete(f"{CONV_API}/conversations/{session_id}", timeout=CONV_API_TIMEOUT_S)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify(error=str(e)), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8802, threaded=True)
