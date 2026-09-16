#!/usr/bin/env python3
"""magi-llm-api — HTTP wrapper exposing this node's Hailo-10H local LLM
(Qwen2.5-Coder-1.5B-Instruct) as a `POST /chat` endpoint for the magi-cluster
dashboard's "offload to a physical agent" feature.

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
   the system prompt, `tool_parsing.parse_function_call()` to pull a
   `<tool_call>{...}</tool_call>` JSON block out of the raw generation, and
   `tool_execution.execute_tool_call()` to dispatch to the real Python tool
   function. Only the **math** and **weather** tools are wired in (both
   pure-Python / network-only — no GPIO). The hardware tools
   (rgb_led/servo/elevator) are deliberately never imported here, so there's
   no path to real hardware from this service.

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
    "write correct, working code when asked for coding help."
)

# --- Conversation history (repositorium's magi-conversation-api) ---
CONV_API = "http://192.168.8.184:8803"
CONV_API_TIMEOUT_S = 5
MAX_HISTORY_MESSAGES = 20  # ~10 turns of user+assistant
MAX_HISTORY_CHARS = 6000   # crude char budget (~1500 tokens) for replayed history

_lock = threading.Lock()
_state = {
    "llm": None,
    "vdevice": None,
    "initialized": False,
    "tools_lookup": None,
    "system_prompt_text": None,
}


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
    from hailo_apps.python.gen_ai_apps.agent_tools_example import system_prompt as at_system_prompt
    from hailo_apps.python.gen_ai_apps.agent_tools_example.tools import math as math_tool
    from hailo_apps.python.gen_ai_apps.agent_tools_example.tools import weather as weather_tool

    tools = [
        {"name": math_tool.name, "tool_def": math_tool.TOOLS_SCHEMA[0],
         "description": math_tool.description, "runner": math_tool.run},
        {"name": weather_tool.name, "tool_def": weather_tool.TOOLS_SCHEMA[0],
         "description": weather_tool.description, "runner": weather_tool.run},
    ]
    tool_prompt_text = at_system_prompt.create_system_prompt(tools)
    _state["tools_lookup"] = {t["name"]: t for t in tools}
    _state["system_prompt_text"] = BASE_SYSTEM_PROMPT + "\n\n" + tool_prompt_text

    _state["initialized"] = True
    logger.info(
        "LLM ready in %.1fs (tools: %s)",
        time.perf_counter() - t0, ", ".join(_state["tools_lookup"].keys()),
    )


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
            logger.info("Tool call detected: %s args=%s", tool_used, tool_call.get("arguments"))
            try:
                tool_result = tool_execution.execute_tool_call(tool_call, tools_lookup)
            except Exception as e:
                logger.exception("tool execution raised")
                tool_result = {"ok": False, "error": str(e)}
            logger.info("Tool '%s' result: %s", tool_used, tool_result)

            # Feed the tool result back so the model produces a natural-language
            # reply, same two-step pattern as agent.py's feed_tool_result().
            tool_msg = message_formatter.messages_tool(json.dumps(tool_result))
            try:
                final_text = _generate([tool_msg], prefix="")
            except Exception as e:
                logger.exception("post-tool generation failed")
                # Fall back to a plain rendering of the tool result rather than failing the request.
                if tool_result.get("ok"):
                    final_text = str(tool_result.get("result") or tool_result.get("error") or "")
                else:
                    final_text = f"(tool '{tool_used}' failed: {tool_result.get('error')})"

        # The small on-device model occasionally degenerates to a near-empty
        # reply (observed: a single stray "<" token) after context-priming,
        # both on the plain path and after tool-result feedback. One bounded
        # fallback regeneration (fresh context, same system+history prime,
        # plain user message) fixes this in practice without unbounded retry.
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
