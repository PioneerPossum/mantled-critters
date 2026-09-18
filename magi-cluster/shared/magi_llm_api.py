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

_lock = threading.Lock()
_state = {
    "llm": None,
    "vdevice": None,
    "initialized": False,
    "tools_lookup": None,
    "system_prompt_text": None,
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

    tools = [
        {"name": math_tool.name, "tool_def": math_tool.TOOLS_SCHEMA[0],
         "description": math_tool.description, "runner": _remote_tool_runner("math")},
        {"name": weather_tool.name, "tool_def": weather_tool.TOOLS_SCHEMA[0],
         "description": weather_tool.description, "runner": _remote_tool_runner("weather")},
        {"name": directions_tool.name, "tool_def": directions_tool.TOOLS_SCHEMA[0],
         "description": directions_tool.description, "runner": _remote_tool_runner("directions")},
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

    got_lock = _lock.acquire(blocking=False)
    if not got_lock:
        return jsonify(
            error="busy",
            detail="this node's Hailo-10H can only run one LLM session at a time "
                   "(HailoRT limitation) and a request is already in progress. "
                   "Try again shortly.",
        ), 429

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

            # Feed the tool result back so the model produces a natural-language
            # reply, same two-step pattern as agent.py's feed_tool_result().
            tool_msg = message_formatter.messages_tool(json.dumps(tool_result))
            try:
                final_text = _generate([tool_msg], prefix="")
            except Exception as e:
                logger.exception("post-tool generation failed")
                final_text = _tool_result_fallback_text()
            else:
                # Post-tool generation can itself (a) degenerate to a near-empty
                # reply, or (b) re-emit ANOTHER raw <tool_call>{...}</tool_call>
                # block instead of a natural-language summary — the tool-aware
                # system prompt is still primed, so the model just hallucinates
                # a fresh, unexecuted tool call. Neither case should fall
                # through to the generic fresh-context-from-original-prompt
                # retry below: with the tool-aware system prompt still loaded,
                # that retry just reproduces the same failure shape (a newly
                # hallucinated tool_call block served raw to the user — this
                # was the real bug, confirmed live on melchior 2026-09-16).
                # Use the tool's own real result text instead — always valid.
                if len(final_text.strip()) < 5:
                    logger.warning(
                        "Degenerate short post-tool response %r detected; "
                        "using tool result text instead of restarting", final_text,
                    )
                    final_text = _tool_result_fallback_text()
                elif tool_parsing.parse_function_call(final_text) is not None:
                    logger.warning(
                        "Post-tool generation re-emitted a tool_call instead of "
                        "a summary (%r); using tool result text instead", final_text,
                    )
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
