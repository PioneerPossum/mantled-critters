#!/usr/bin/env python3
"""Receive the Legion's VBAN music stream, record it to FLAC and analyse it.

- Listens on UDP 6980 for VBAN audio stream VBAN_STREAM (default "magi-stream"),
  48 kHz / 2 ch / 16-bit PCM.
- Records to FLAC on the NVMe via ffmpeg, one file per hour, new file after a
  gap of GAP_S seconds with no audio.
- Tracks tempo (aubio), RMS and peak level. Every 2 s it SETs magi:audio:latest
  and XADDs to the Redis stream magi:audio; every 10 s it inserts an
  events row (device 'legion', event_type 'audio_stats') into Postgres.
- Optionally sends smoothed BPM to Resolume Arena over OSC
  (/composition/tempocontroller/tempo, normalised: v = (BPM - 20) / 480).
  OSC_ENABLE=0 (default) only logs what it would send.

Credentials come from ~/.config/magi/worker.env, same as magi_worker.py.
"""
import json
import os
import socket
import statistics
import struct
import subprocess
import time
from collections import deque
from datetime import datetime

import aubio
import numpy as np
import psycopg2
import redis
from beat_emit import BeatClock, BeatSink
from tempo_track import TempoTracker


def load_env(path):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


load_env(os.path.expanduser("~/.config/magi/worker.env"))
load_env(os.path.expanduser("~/.config/magi/audio.env"))

VBAN_PORT = int(os.environ.get("VBAN_PORT", "6980"))
VBAN_STREAM = os.environ.get("VBAN_STREAM", "magi-stream")
REC_DIR = os.environ.get("REC_DIR", "/home/sinewave/512_VideoStick/audio/magi-stream")
GAP_S = float(os.environ.get("GAP_S", "5"))
OSC_ENABLE = os.environ.get("OSC_ENABLE", "0") == "1"
OSC_HOST, OSC_PORT = os.environ.get("OSC_TARGET", "192.168.8.147:7000").split(":")
BPM_MIN = float(os.environ.get("BPM_MIN", "80"))
BPM_MAX = float(os.environ.get("BPM_MAX", "180"))
SILENCE_DB = float(os.environ.get("SILENCE_DB", "-50"))
CONF_MIN = float(os.environ.get("CONF_MIN", "0.1"))
WARMUP_S = float(os.environ.get("WARMUP_S", "15"))
HOLD_S = float(os.environ.get("HOLD_S", "10"))
SUSPECT_HOLD_S = float(os.environ.get("SUSPECT_HOLD_S", "25"))
BEAT_ENABLE = os.environ.get("BEAT_ENABLE", "0") == "1"   # off by default; see beat_emit.py
BEAT_UDP = os.environ.get("BEAT_UDP", "")                 # "host:port" or empty (Redis only)
BEAT_DELAY_S = float(os.environ.get("BEAT_DELAY_S", "0"))

RATE, CHANNELS = 48000, 2
HOP, WIN = 512, 1024

r = redis.Redis(host=os.environ["REDIS_HOST"], password=os.environ["REDIS_PASSWORD"], decode_responses=True)
PG_DSN = (
    f"host={os.environ['PG_HOST']} dbname={os.environ['PG_DB']} "
    f"user={os.environ['PG_USER']} password={os.environ['PG_PASSWORD']}"
)
conn = psycopg2.connect(PG_DSN)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("INSERT INTO devices (name, kind) VALUES ('legion', 'audio-source') ON CONFLICT (name) DO NOTHING")
    cur.execute("SELECT id FROM devices WHERE name = 'legion'")
    LEGION_ID = cur.fetchone()[0]


def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def db(x):
    return 20 * np.log10(max(x, 1e-9))


def fold_bpm(bpm):
    """Fold half/double-tempo detections into [BPM_MIN, BPM_MAX]."""
    if bpm <= 0:
        return 0.0
    while bpm < BPM_MIN:
        bpm *= 2
    while bpm > BPM_MAX:
        bpm /= 2
    return bpm


def osc_tempo(sock, bpm):
    def pad(b):
        return b + b"\0" * (4 - len(b) % 4)
    v = min(max((bpm - 20) / 480, 0.0), 1.0)
    msg = pad(b"/composition/tempocontroller/tempo") + pad(b",f") + struct.pack(">f", v)
    sock.sendto(msg, (OSC_HOST, int(OSC_PORT)))


class Recorder:
    def __init__(self):
        os.makedirs(REC_DIR, exist_ok=True)
        self.proc = None

    def write(self, pcm):
        if self.proc is None:
            pattern = os.path.join(REC_DIR, "%Y-%m-%d_%H-%M-%S.flac")
            self.proc = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-f", "s16le", "-ar", str(RATE),
                 "-ac", str(CHANNELS), "-i", "pipe:0", "-c:a", "flac", "-f", "segment",
                 "-segment_time", "3600", "-strftime", "1", pattern],
                stdin=subprocess.PIPE,
            )
            log(f"recording started in {REC_DIR}")
        self.proc.stdin.write(pcm)

    def close(self):
        if self.proc is not None:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
            self.proc = None
            log("recording closed (gap)")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    sock.bind(("0.0.0.0", VBAN_PORT))
    sock.settimeout(1.0)
    osc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log(f"listening on UDP {VBAN_PORT} for '{VBAN_STREAM}', OSC {'ON' if OSC_ENABLE else 'dry-run'} -> {OSC_HOST}:{OSC_PORT}")

    rec = Recorder()
    tempo = aubio.tempo("default", WIN, HOP, RATE)
    clock = BeatClock(RATE, BEAT_DELAY_S)
    beat_sink = BeatSink(BEAT_UDP, r)
    log(f"beat events {'ON' if BEAT_ENABLE else 'off'} (udp {BEAT_UDP or 'none'}, redis magi:audio:beat)")
    buf = np.zeros(0, dtype=np.float32)
    last_pkt = 0.0
    last_frame = None
    lost = 0
    beats = 0
    sq_sum, n_samp, peak = 0.0, 0, 0.0
    bpm_hist = deque()
    last_pub = last_db = time.time()
    last_sent_bpm, last_sent_t = None, 0.0
    started = time.time()
    tracker = TempoTracker(BPM_MIN, BPM_MAX, conf_min=CONF_MIN, hold_s=HOLD_S, warmup_s=WARMUP_S, start=started)
    try:   # continuity across restarts: last tempo we sent to Arena
        v = r.get("magi:audio:tempo_sent")
        if v:
            tracker.current = float(v)
    except Exception:
        pass

    while True:
        now = time.time()
        try:
            d, addr = sock.recvfrom(2048)
        except socket.timeout:
            d = None
        if d and len(d) > 28 and d[:4] == b"VBAN" and (d[4] >> 5) == 0:
            name = d[8:24].split(b"\0")[0].decode(errors="replace")
            ch, fmt = d[6] + 1, d[7] & 0x07
            if name == VBAN_STREAM and ch == CHANNELS and fmt == 1:
                frame = struct.unpack("<I", d[24:28])[0]
                if last_frame is not None and frame > last_frame + 1:
                    lost += frame - last_frame - 1
                last_frame = frame
                pcm = d[28:]
                last_pkt = now
                rec.write(pcm)
                s = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                mono = s.reshape(-1, CHANNELS).mean(axis=1)
                sq_sum += float(np.dot(mono, mono))
                n_samp += mono.size
                peak = max(peak, float(np.abs(s).max()) if s.size else 0.0)
                buf = np.concatenate([buf, mono])
                while buf.size >= HOP:
                    hop = buf[:HOP]
                    is_beat = tempo(hop)[0]
                    clock.fed(HOP)
                    if is_beat:
                        beats += 1
                        if BEAT_ENABLE:
                            b = clock.beat(now, tempo.get_last(), float(np.dot(hop, hop)))
                            b.update(sent=time.time(), bpm=round(float(tempo.get_bpm()), 2),
                                     conf=round(min(max(float(tempo.get_confidence()), 0.0), 1.0), 2))
                            beat_sink.emit(b)
                    buf = buf[HOP:]
        if rec.proc is not None and now - last_pkt > GAP_S:
            rec.close()

        active = now - last_pkt < GAP_S
        if now - last_pub >= (2.0 if active else 30.0):  # idle: heartbeat every 30 s only
            rms_db = db((sq_sum / n_samp) ** 0.5) if n_samp else -120.0
            raw_bpm = float(tempo.get_bpm())
            conf = float(tempo.get_confidence())
            if not 0.0 <= conf <= 2.0:  # aubio emits garbage confidence during transitions
                conf = 0.0
            bpm = fold_bpm(raw_bpm)
            if active and rms_db > SILENCE_DB and conf >= CONF_MIN and bpm > 0:
                bpm_hist.append((now, bpm))
            while bpm_hist and now - bpm_hist[0][0] > 8:
                bpm_hist.popleft()
            smooth = statistics.median(b for _, b in bpm_hist) if len(bpm_hist) >= 4 else None
            stats = {
                "ts": datetime.now().isoformat(timespec="seconds"), "active": active,
                "bpm_raw": round(raw_bpm, 2), "bpm": round(bpm, 2),
                "bpm_smooth": round(smooth, 2) if smooth else None, "confidence": round(conf, 3),
                "rms_db": round(rms_db, 1), "peak_db": round(db(peak), 1),
                "beats": beats, "lost_frames": lost,
            }
            r.set("magi:audio:latest", json.dumps(stats), ex=30)
            r.xadd("magi:audio", {"json": json.dumps(stats)}, maxlen=20000, approximate=True)
            # TempoTracker (tempo_track.py): metric sub-pulses (3/4, 2/3, 4/3, 3/2 of the sent tempo) are
            # ignored, quiet breakdowns are skipped, a new tempo must be steady (10 s, 90 s for big jumps).
            new_bpm = tracker.update(now, raw_bpm, conf, rms_db, active)
            if new_bpm:
                r.set("magi:audio:tempo_sent", f"{new_bpm:.2f}")
                if OSC_ENABLE:
                    osc_tempo(osc, new_bpm)
                log(f"{'OSC sent' if OSC_ENABLE else 'OSC dry-run'} tempo {new_bpm:.2f} BPM "
                    f"(raw {raw_bpm:.1f}, conf {conf:.2f}, rms {rms_db:.1f} dB)")
            if now - last_db >= 10.0 and active:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO events (device_id, event_type, data) VALUES (%s, 'audio_stats', %s)",
                        (LEGION_ID, json.dumps(stats)),
                    )
                last_db = now
            sq_sum, n_samp, peak, beats = 0.0, 0, 0.0, 0
            last_pub = now


if __name__ == "__main__":
    main()
