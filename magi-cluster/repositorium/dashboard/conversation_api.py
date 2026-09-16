#!/usr/bin/env python3
"""magi-conversation-api — runs on repositorium only.

Small Postgres-backed store for the dashboard's local-LLM chat history, so
melchior's magi-llm-api.py (which has no direct Postgres access/credentials
itself) can load/save multi-turn conversation state per browser session
without needing DB creds shipped to melchior. Mirrors the existing
data_api.py pattern exactly: same ~/.config/magi/worker.env credential file,
same "small HTTP wrapper on the box with local Postgres access" shape.
Unlike data_api.py this service does have write endpoints (POST/DELETE) --
that file's own "no write endpoints" note is specific to that read-only
dashboard-metrics API, not a project-wide rule; this is a narrowly scoped,
separate service for exactly one purpose (chat turn storage).

Table: llm_conversations (session_id text, role text, content text,
created_at timestamptz default now()). Created on startup if missing.
"""
import os
import time

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request

ENV_PATH = os.path.expanduser("~/.config/magi/worker.env")
DEFAULT_LOAD_LIMIT = 20  # messages (~10 turns), matches magi_llm_api.py's history window


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


ENV = load_env(ENV_PATH)

app = Flask(__name__)


def pg_conn():
    return psycopg2.connect(
        host=ENV["PG_HOST"], dbname=ENV["PG_DB"],
        user=ENV["PG_USER"], password=ENV["PG_PASSWORD"],
        connect_timeout=5,
    )


def ensure_schema():
    conn = pg_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            create table if not exists llm_conversations (
                id bigserial primary key,
                session_id text not null,
                role text not null,
                content text not null,
                created_at timestamptz not null default now()
            )
            """
        )
        cur.execute(
            "create index if not exists llm_conversations_session_idx "
            "on llm_conversations (session_id, created_at)"
        )
        conn.commit()
    finally:
        conn.close()


@app.get("/health")
def health():
    return jsonify(ok=True, ts=time.time())


@app.get("/conversations/<session_id>")
def get_conversation(session_id):
    limit = request.args.get("limit", DEFAULT_LOAD_LIMIT, type=int)
    limit = max(1, min(limit, 200))
    conn = pg_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Take the most recent `limit` rows, then return them oldest-first
        # so the caller can feed them straight into the LLM in order.
        cur.execute(
            """
            select role, content, created_at
            from (
                select role, content, created_at
                from llm_conversations
                where session_id = %s
                order by created_at desc
                limit %s
            ) recent
            order by created_at asc
            """,
            (session_id, limit),
        )
        rows = cur.fetchall()
        for r in rows:
            r["created_at"] = r["created_at"].isoformat()
        return jsonify(session_id=session_id, turns=rows)
    finally:
        conn.close()


@app.post("/conversations/<session_id>")
def append_turn(session_id):
    body = request.get_json(silent=True) or {}
    role = (body.get("role") or "").strip()
    content = body.get("content")
    if role not in ("user", "assistant"):
        return jsonify(error="role must be 'user' or 'assistant'"), 400
    if not content or not isinstance(content, str):
        return jsonify(error="missing 'content'"), 400
    conn = pg_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "insert into llm_conversations (session_id, role, content) values (%s, %s, %s)",
            (session_id, role, content),
        )
        conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


@app.delete("/conversations/<session_id>")
def clear_conversation(session_id):
    conn = pg_conn()
    try:
        cur = conn.cursor()
        cur.execute("delete from llm_conversations where session_id = %s", (session_id,))
        conn.commit()
        return jsonify(ok=True, deleted=cur.rowcount)
    finally:
        conn.close()


if __name__ == "__main__":
    ensure_schema()
    app.run(host="0.0.0.0", port=8803, threaded=True)
