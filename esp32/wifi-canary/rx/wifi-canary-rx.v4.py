#!/usr/bin/env python3
"""wifi-canary-rx: receiver for the ESP32 "Wi-Fi canary" (UDP 9631).

Packet (one ASCII datagram, <=250 bytes):
  WC1 id=a1b2c3 seq=412 up=4120 rssi=-58 bssid=.. ip=.. gw=3 ping=14 dnsr=9 dnsp=12 tcp=20 out=0 rst=1
probe values are ms, or -1 = that probe failed.

v4: the latest accepted packet per device is also kept as Redis HASH wifi-canary:last:<id> (TTL 120 s).
Optional trailing flt=0|1 (device latched-fault flag): one FLT line per boot, re-armed on START.
Logs only state changes, SILENT/BACK, device START / out>0, and a 5-min SUMMARY
to ~/.cache/net-diag/wifi-canary-YYYY-MM-DD.log.   Stdlib only; safe with python3 -I.
Run `--selftest` for the canned state-machine test (no sockets, temp log dir).
"""
import datetime
import ipaddress
import json
import os
import queue
import signal
import socket
import statistics
import sys
import threading
import time

PORT = 9631
MAX_LEN = 250
SILENCE_S = 45
SUMMARY_S = 300
FAIL_CONFIRM = 2          # consecutive failing cycles before a FAIL is logged
KEEP_DAYS = 30
LAST_KEY = "wifi-canary:last:%s"   # Redis HASH: latest accepted packet per device
LAST_TTL_S = 120                      # a dead board's entry expires
LAST_FIELDS = ("ip", "seq", "up", "rssi", "bssid", "out", "rst", "flt", "gw", "ping", "dnsr", "dnsp", "tcp")
PUB_S = 60                # one aggregate event per device per window goes to Redis (when enabled)
CLEAN_EVERY_S = 3600
PROBES = ("gw", "ping", "dnsr", "dnsp", "tcp")
ALLOWED_NETS = [ipaddress.ip_network(n) for n in
                ("192.168.8.0/24", "192.168.10.0/24", "127.0.0.0/8")]
# 127.0.0.0/8 is allowed only so the receiver can be tested locally.


def src_allowed(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in ALLOWED_NETS)


def parse_packet(data):
    """Return dict of fields, or None if malformed."""
    if not data or len(data) > MAX_LEN:
        return None
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    parts = text.split()
    if len(parts) < 2 or parts[0] != "WC1":
        return None
    f = {}
    for tok in parts[1:]:
        if "=" not in tok:
            return None
        k, v = tok.split("=", 1)
        if not k or not v:
            return None
        f[k] = v
    if "id" not in f or "seq" not in f:
        return None
    try:
        int(f["id"], 16)
    except ValueError:
        return None
    out = {"id": f["id"].lower(), "bssid": f.get("bssid", ""), "ip": f.get("ip", "")}
    for k in ("seq", "up", "rssi", "out", "rst", "flt") + PROBES:
        if k in f:
            try:
                out[k] = int(f[k])
            except ValueError:
                return None
    if out["seq"] < 0 or any(out.get(k, 0) < -1 for k in PROBES):
        return None
    if out.get("flt", 0) not in (0, 1):
        return None
    return out


def hms(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


class Log:
    """Daily log file writer (append, open per write: volume is tiny)."""

    def __init__(self, logdir, clock):
        self.dir = logdir
        self.clock = clock
        os.makedirs(self.dir, exist_ok=True)

    def path(self, ts=None):
        d = datetime.datetime.fromtimestamp(self.clock() if ts is None else ts)
        return os.path.join(self.dir, "wifi-canary-%s.log" % d.strftime("%Y-%m-%d"))

    def write(self, msg):
        ts = self.clock()
        stamp = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        line = "%s %s\n" % (stamp, msg)
        try:
            with open(self.path(ts), "a") as fh:
                fh.write(line)
        except OSError as e:
            sys.stderr.write("log write failed: %s\n" % e)

    def cleanup(self):
        cutoff = self.clock() - KEEP_DAYS * 86400
        try:
            names = os.listdir(self.dir)
        except OSError:
            return
        for n in names:
            if n.startswith("wifi-canary-") and n.endswith(".log"):
                p = os.path.join(self.dir, n)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.unlink(p)
                except OSError:
                    pass


class RedisPub:
    """Optional, non-blocking publisher into the existing magi:detections stream (worker -> Postgres `events`).
    Events go through a bounded queue to a worker thread, so a Redis outage can never stall the UDP loop."""
    STREAM = "magi:detections"
    DEVICE = "wifi-canary"          # must exist in the Postgres `devices` table (it does since 2026-10-24)
    EVENT_TYPE = "wifi_canary"

    def __init__(self, log, host, port, password, client_factory=None, start=True):
        self.log = log
        self.host, self.port, self.password = host, port, password
        self.q = queue.Queue(maxsize=500)
        self.last = {}              # id -> fields; coalesced, so it never grows or delays the stream
        self.last_lock = threading.Lock()
        self.dropped = 0
        self.sent = 0
        self.err_seen = None
        self.stopping = False
        self.client_factory = client_factory or self._make
        self.t = threading.Thread(target=self._run, daemon=True)
        if start:
            self.t.start()

    def _make(self):
        import redis
        return redis.Redis(host=self.host, port=self.port, password=self.password,
                           socket_connect_timeout=3, socket_timeout=3)

    def publish(self, data):
        try:
            self.q.put_nowait(data)
        except queue.Full:
            self.dropped += 1

    def set_last(self, did, fields):
        with self.last_lock:
            self.last[did] = fields

    def flush_last(self, r):
        with self.last_lock:
            pending, self.last = self.last, {}
        for did, fields in pending.items():
            key = LAST_KEY % did
            r.hset(key, mapping=fields)
            r.expire(key, LAST_TTL_S)

    def send_one(self, r, data):
        r.xadd(self.STREAM, {"device": self.DEVICE, "event_type": self.EVENT_TYPE,
                             "data": json.dumps(data, separators=(",", ":"), sort_keys=True)})
        self.sent += 1

    def _run(self):
        r = None
        while not self.stopping:
            try:
                item = self.q.get(timeout=1)
            except queue.Empty:
                item = None
            if self.last:
                try:
                    if r is None:
                        r = self.client_factory()
                    self.flush_last(r)
                except Exception as e:
                    sig = "%s:%s" % (type(e).__name__, str(e)[:80])
                    if sig != self.err_seen:
                        self.log.write("REDIS ERROR (logged once) %s" % sig)
                        self.err_seen = sig
                    r = None
            if item is None:
                continue
            while not self.stopping:
                try:
                    if r is None:
                        r = self.client_factory()
                    self.send_one(r, item)
                    if self.err_seen:
                        self.log.write("REDIS OK again (was: %s)" % self.err_seen)
                        self.err_seen = None
                    break
                except Exception as e:
                    sig = "%s:%s" % (type(e).__name__, str(e)[:80])
                    if sig != self.err_seen:
                        self.log.write("REDIS ERROR (logged once) %s" % sig)
                        self.err_seen = sig
                    r = None
                    time.sleep(5)


class Dev:
    def __init__(self):
        self.last_seq = None
        self.last_up = None
        self.last_out = 0
        self.flt_logged = False                     # FLT line written for this boot
        self.state = {p: None for p in PROBES}      # None / "OK" / "FAIL" (confirmed)
        self.fails = {p: 0 for p in PROBES}         # consecutive failing cycles
        self.first_fail = {p: None for p in PROBES}  # (ts, seq)
        self.reset_window()
        self.reset_agg()

    def reset_agg(self):
        self.a_cycles = 0
        self.a_ok = {p: 0 for p in PROBES}
        self.a_n = {p: 0 for p in PROBES}
        self.a_ms = {p: [] for p in PROBES}
        self.a_rssi = []
        self.a_last = {}

    def reset_window(self):
        self.cycles = 0
        self.lost = 0
        self.ok = {p: 0 for p in PROBES}
        self.bad = {p: 0 for p in PROBES}
        self.ms = {p: [] for p in PROBES}
        self.rssi = []


class Canary:
    def __init__(self, log, clock, pub=None):
        self.log = log
        self.clock = clock
        self.pub = pub
        now = clock()
        self.next_pub = now + PUB_S
        self.devs = {}
        self.last_rx = now          # silence is measured from receiver start too
        self.heard_any = False
        self.silent = False
        self.next_summary = now + SUMMARY_S
        self.next_clean = now + CLEAN_EVERY_S
        self.dropped_bad = 0
        self.dropped_src = 0
        self.dups = 0

    # ---- ingest -----------------------------------------------------
    def handle(self, data, src_ip):
        if not src_allowed(src_ip):
            self.dropped_src += 1
            return "drop-src"
        p = parse_packet(data)
        if p is None:
            self.dropped_bad += 1
            return "drop-bad"
        now = self.clock()
        if self.silent:
            self.log.write("BACK after silent=%ds" % round(now - self.last_rx))
            self._emit({"kind": "back", "silent_s": round(now - self.last_rx)})
            self.silent = False
        self.last_rx = now
        self.heard_any = True
        dev = self.devs.get(p["id"])
        tag = "[%s]" % p["id"]
        if dev is None:
            dev = self.devs[p["id"]] = Dev()
            self.log.write("%s START (new id) seq=%d up=%s rst=%s rssi=%s bssid=%s ip=%s src=%s" % (
                tag, p["seq"], p.get("up", "?"), p.get("rst", "?"), p.get("rssi", "?"),
                p["bssid"], p["ip"], src_ip))
        elif dev.last_seq is not None and (
                p["seq"] < dev.last_seq or
                ("up" in p and dev.last_up is not None and p["up"] + 5 < dev.last_up)):
            self.log.write("%s START (seq reset %d->%d) up=%s rst=%s rssi=%s" % (
                tag, dev.last_seq, p["seq"], p.get("up", "?"), p.get("rst", "?"), p.get("rssi", "?")))
            dev.last_seq = None
            dev.last_out = 0
            dev.flt_logged = False
            dev.state = {k: None for k in PROBES}
            dev.fails = {k: 0 for k in PROBES}
        if dev.last_seq is not None and p["seq"] == dev.last_seq:
            self.dups += 1
            return "dup"
        if dev.last_seq is not None and p["seq"] > dev.last_seq + 1:
            dev.lost += p["seq"] - dev.last_seq - 1
        dev.last_seq = p["seq"]
        dev.last_up = p.get("up", dev.last_up)
        dev.cycles += 1
        if "rssi" in p:
            dev.rssi.append(p["rssi"])
        self._probes(dev, p, tag, now)
        self._agg(dev, p)
        out = p.get("out", 0)
        if out > 0 and out != dev.last_out:
            self.log.write("%s OUT device saw wifi outage out=%ds seq=%d up=%s rssi=%s" % (
                tag, out, p["seq"], p.get("up", "?"), p.get("rssi", "?")))
        dev.last_out = out
        if self.pub is not None:
            f = {k: p[k] for k in LAST_FIELDS if k in p}
            f["rx_ts"] = round(self.clock(), 1)
            f["rx_src"] = src_ip
            self.pub.set_last(p["id"], f)
        if p.get("flt") == 1 and not dev.flt_logged:
            dev.flt_logged = True
            self.log.write("%s FLT device reports a latched fault (cleared only by its reset) seq=%d up=%s rssi=%s" % (
                tag, p["seq"], p.get("up", "?"), p.get("rssi", "?")))
            self._emit({"kind": "flt", "id": p["id"], "seq": p["seq"]})
        return "ok"

    def _emit(self, data):
        if self.pub is not None:
            self.pub.publish(data)

    def _agg(self, dev, p):
        dev.a_cycles += 1
        if "rssi" in p:
            dev.a_rssi.append(p["rssi"])
        for k in PROBES:
            if k in p:
                dev.a_n[k] += 1
                if p[k] >= 0:
                    dev.a_ok[k] += 1
                    dev.a_ms[k].append(p[k])
        dev.a_last = {"id": p["id"], "seq": p["seq"], "bssid": p["bssid"], "ip": p["ip"], "out": p.get("out", 0)}

    def publish_aggregates(self):
        for did, d in self.devs.items():
            if d.a_cycles == 0:
                continue
            probes = {}
            for k in PROBES:
                if d.a_n[k]:
                    probes[k] = {"ok": d.a_ok[k], "n": d.a_n[k],
                                 "med_ms": (statistics.median(d.a_ms[k]) if d.a_ms[k] else None)}
            data = {"kind": "aggregate", "id": did, "window_s": PUB_S, "cycles": d.a_cycles,
                    "bssid": d.a_last.get("bssid"), "ip": d.a_last.get("ip"), "out": d.a_last.get("out", 0),
                    "probes": probes}
            if d.a_rssi:
                data["rssi_min"] = min(d.a_rssi)
                data["rssi_avg"] = round(sum(d.a_rssi) / len(d.a_rssi), 1)
            self._emit(data)
            d.reset_agg()

    def _nums(self, p):
        s = "seq=%d rssi=%s" % (p["seq"], p.get("rssi", "?"))
        for k in PROBES:
            if k in p:
                s += " %s=%d" % (k, p[k])
        return s

    def _probes(self, dev, p, tag, now):
        for k in PROBES:
            if k not in p:
                continue
            v = p[k]
            if v >= 0:
                dev.ok[k] += 1
                dev.ms[k].append(v)
                dev.fails[k] = 0
                if dev.state[k] == "FAIL":
                    ff = dev.first_fail[k]
                    self.log.write("%s %s FAIL->OK down=%ds first_fail=%s %s" % (
                        tag, k, round(now - ff[0]), hms(ff[0]), self._nums(p)))
                    self._emit({"kind": "transition", "id": p["id"], "probe": k, "to": "OK",
                                "down_s": round(now - ff[0]), "seq": p["seq"], "rssi": p.get("rssi")})
                dev.state[k] = "OK"
            else:
                dev.bad[k] += 1
                if dev.fails[k] == 0:
                    dev.first_fail[k] = (now, p["seq"])
                dev.fails[k] += 1
                if dev.fails[k] >= FAIL_CONFIRM and dev.state[k] != "FAIL":
                    prev = dev.state[k] or "?"
                    self.log.write("%s %s %s->FAIL first_fail=%s (%d cycles) %s" % (
                        tag, k, prev, hms(dev.first_fail[k][0]), dev.fails[k], self._nums(p)))
                    dev.state[k] = "FAIL"
                    self._emit({"kind": "transition", "id": p["id"], "probe": k, "to": "FAIL", "from": prev,
                                "cycles": dev.fails[k], "seq": p["seq"], "rssi": p.get("rssi"),
                                "bssid": p.get("bssid")})

    # ---- periodic ---------------------------------------------------
    def tick(self):
        now = self.clock()
        if not self.silent and now - self.last_rx >= SILENCE_S:
            self.silent = True
            self._emit({"kind": "silent", "secs": round(now - self.last_rx), "heard_any": self.heard_any})
            if self.heard_any:
                self.log.write("SILENT no valid packet for %ds (last at %s)" % (
                    round(now - self.last_rx), hms(self.last_rx)))
            else:
                self.log.write("SILENT no valid packet since receiver start (%ds)" % round(now - self.last_rx))
        if self.pub is not None and now >= self.next_pub:
            self.publish_aggregates()
            self.next_pub = now + PUB_S
        if now >= self.next_summary:
            self.summary()
            self.next_summary = now + SUMMARY_S
        if now >= self.next_clean:
            self.log.cleanup()
            self.next_clean = now + CLEAN_EVERY_S

    def summary(self):
        for did, d in self.devs.items():
            if d.cycles == 0:
                continue
            parts = []
            for k in PROBES:
                n = d.ok[k] + d.bad[k]
                if n == 0:
                    continue
                med = ("%dms" % statistics.median(d.ms[k])) if d.ms[k] else "-"
                parts.append("%s=%d%%/%s" % (k, round(100.0 * d.ok[k] / n), med))
            rs = ""
            if d.rssi:
                rs = " rssi=%d/%.0f" % (min(d.rssi), sum(d.rssi) / len(d.rssi))
            self.log.write("SUMMARY [%s] cycles=%d lost=%d %s%s drop_bad=%d drop_src=%d" % (
                did, d.cycles, d.lost, " ".join(parts), rs, self.dropped_bad, self.dropped_src))
            d.reset_window()
        self.dropped_bad = 0
        self.dropped_src = 0


# ---- selftest ------------------------------------------------------
def selftest():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="wcrx-selftest-")
    t = [1_800_000_000.0]
    clock = lambda: t[0]
    log = Log(tmp, clock)
    c = Canary(log, clock)
    seq = [100]

    def pkt(ping=14, dnsr=9, dnsp=12, gw=3, tcp=20, out=0, id_="a1b2c3", s=None, rssi=-58):
        if s is None:
            seq[0] += 1
            s = seq[0]
        return ("WC1 id=%s seq=%d up=%d rssi=%d bssid=02:00:00:00:00:00 ip=192.168.8.173 "
                "gw=%d ping=%d dnsr=%d dnsp=%d tcp=%d out=%d rst=1 future=zzz" % (
                    id_, s, s * 10, rssi, gw, ping, dnsr, dnsp, tcp, out)).encode()

    def send(data, src="192.168.8.173", dt=10):
        t[0] += dt
        return c.handle(data, src)

    def lines():
        p = log.path()
        return open(p).read().splitlines() if os.path.exists(p) else []

    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    # healthy + START
    send(pkt()); send(pkt())
    L = lines()
    check("device START logged once", sum("START (new id)" in x for x in L) == 1)
    check("healthy cycles log nothing else", len(L) == 1)
    # single failure ignored
    send(pkt(ping=-1)); send(pkt())
    check("single fail cycle ignored", not any("->FAIL" in x for x in lines()))
    # two consecutive fails logged
    n0 = len(lines())
    send(pkt(ping=-1, dnsp=-1)); send(pkt(ping=-1, dnsp=-1))
    L = lines()[n0:]
    check("2 fails logged for ping and dnsp", sum("->FAIL" in x for x in L) == 2)
    check("FAIL has first_fail=", all("first_fail=" in x for x in L))
    check("FAIL prev state OK->FAIL", any("ping OK->FAIL" in x for x in L))
    send(pkt(ping=-1, dnsp=-1))
    check("3rd fail not logged again", len(lines()) == n0 + len(L))
    # recovery
    send(pkt())
    L2 = lines()[n0 + len(L):]
    check("recovery logged for both", sum("FAIL->OK" in x for x in L2) == 2)
    check("recovery has down=", all("down=" in x for x in L2))
    # unknown key ignored, extra field ok
    check("unknown key ignored", send(pkt()) == "ok")
    # bad packets
    nb = len(lines())
    for bad in (b"", b"garbage", b"WC1", b"WC1 id=zz seq=1", b"WC1 id=a1 seq=x", b"WC1 id=a1",
                b"WC1 id=a1b2c3 seq=5 ping=abc", b"XX1 id=a1b2c3 seq=5", b"WC1 id=a1b2c3 seq=5 \xff\xfe",
                b"WC1 id=a1b2c3 seq=5 " + b"k=1 " * 80, b"WC1 id=a1b2c3 seq=5 junk"):
        r = send(bad, dt=0)
        if r != "drop-bad":
            check("malformed dropped: %r" % bad[:30], False)
    check("malformed packets dropped & counted", c.dropped_bad == 11)
    check("src outside LAN dropped", send(pkt(), src="8.8.8.8", dt=0) == "drop-src")
    check("src 192.168.9.x dropped", send(pkt(), src="192.168.9.5", dt=0) == "drop-src")
    check("src 192.168.10.x accepted", send(pkt(), src="192.168.10.176") == "ok")
    check("127.0.0.1 accepted (test allowance)", send(pkt(), src="127.0.0.1") == "ok")
    check("dropped packets logged nothing", len(lines()) == nb)
    # out>0
    send(pkt(out=37)); send(pkt(out=37))
    check("out>0 logged once", sum(" OUT " in x for x in lines()) == 1)
    # seq gap -> lost; seq reset -> START
    send(pkt(s=seq[0] + 4)); seq[0] += 4
    send(pkt(s=3), dt=10)
    check("seq reset logs START", any("seq reset" in x for x in lines()))
    seq[0] = 3
    send(pkt(id_="ffeedd", s=1))
    check("new id logs START", sum("START (new id)" in x for x in lines()) == 2)
    # latched-fault flag: once per boot, re-armed by a seq reset, bad value dropped
    send(pkt(id_="ffeedd", s=2) + b" flt=0")
    check("flt=0 logs nothing", not any(" FLT " in x for x in lines()))
    send(pkt(id_="ffeedd", s=3) + b" flt=1"); send(pkt(id_="ffeedd", s=4) + b" flt=1")
    check("flt=1 logged once per boot", sum(" FLT " in x for x in lines()) == 1)
    send(pkt(id_="ffeedd", s=1) + b" flt=1")
    check("flt re-armed after seq reset", sum(" FLT " in x for x in lines()) == 2)
    check("flt=2 is malformed", send(pkt(id_="ffeedd", s=2) + b" flt=2") == "drop-bad")
    check("packet without flt still parses", parse_packet(pkt(id_="ffeedd", s=9)) is not None)
    # v4: latest-packet hash (fake redis, no network)
    class FakeR:
        def __init__(s): s.h, s.ttl, s.calls = {}, {}, 0
        def hset(s, k, mapping): s.h.setdefault(k, {}).update(mapping); s.calls += 1
        def expire(s, k, n): s.ttl[k] = n
    class StubPub:
        def __init__(s): s.last = {}
        def publish(s, d): pass
        def set_last(s, i, f): s.last[i] = f
    sp = StubPub(); c2 = Canary(log, clock, sp)
    c2.handle(pkt(id_="abc123", s=5) + b" flt=1", "192.168.8.173")
    f = sp.last.get("abc123", {})
    check("handle stores latest packet fields", f.get("seq") == 5 and f.get("flt") == 1 and f.get("ip") == "192.168.8.173" and f.get("up") == 50)
    check("latest packet carries rx_ts and rx_src", "rx_ts" in f and f.get("rx_src") == "192.168.8.173")
    c2.handle(b"WC1 id=abc123 seq=oops", "192.168.8.173")
    check("malformed packet does not overwrite latest", sp.last["abc123"]["seq"] == 5)
    rp = RedisPub(log, "x", 0, "pw", client_factory=lambda: None, start=False)
    rp.set_last("abc123", {"seq": 1}); rp.set_last("abc123", {"seq": 2}); rp.set_last("zz", {"seq": 9})
    fr = FakeR(); rp.flush_last(fr)
    check("flush_last coalesces to one write per device", fr.calls == 2 and fr.h["wifi-canary:last:abc123"]["seq"] == 2)
    check("flush_last sets the 120 s expiry", fr.ttl.get("wifi-canary:last:abc123") == 120)
    rp.flush_last(fr)
    check("nothing pending -> no extra write", fr.calls == 2)
    # silence
    nb = len(lines())
    t[0] += 30; c.tick()
    check("no SILENT before 45s", len(lines()) == nb)
    t[0] += 20; c.tick(); c.tick()
    L = lines()
    check("SILENT logged once", sum("SILENT" in x for x in L) == 1)
    t[0] += 60
    send(pkt(s=2, id_="ffeedd"), dt=0)
    L = lines()
    check("BACK logged with duration", any("BACK after silent=" in x for x in L))
    # summary (>=5 min since start already elapsed in fake clock)
    t[0] += 1000
    send(pkt(s=3, id_="ffeedd"), dt=0)
    c.tick()
    S = [x for x in lines() if "SUMMARY" in x]
    check("summary produced", len(S) >= 1)
    check("summary has cycles/pct/ms/rssi", any("cycles=" in x and "ping=" in x and "%/" in x and "rssi=" in x for x in S))
    print("\n--- log produced by selftest ---")
    print("\n".join(lines()))
    # cleanup old logs
    old = os.path.join(tmp, "wifi-canary-2020-01-01.log")
    open(old, "w").write("x\n")
    os.utime(old, (t[0] - 40 * 86400, t[0] - 40 * 86400))
    log.cleanup()
    check("30-day cleanup removes old log", not os.path.exists(old))
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nSELFTEST %s (%d failures)" % ("OK" if not fails else "FAILED", len(fails)))
    return 1 if fails else 0


# ---- main ----------------------------------------------------------
def main(argv):
    if "--selftest" in argv:
        return selftest()
    port = PORT
    redis_secret, redis_host, redis_port = None, "192.168.8.184", 6379
    logdir = os.path.join(os.path.expanduser("~"), ".cache", "net-diag")
    i = 1
    while i < len(argv):          # --port / --logdir exist for ad-hoc testing only
        if argv[i] == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 1
        elif argv[i] == "--logdir" and i + 1 < len(argv):
            logdir = argv[i + 1]; i += 1
        elif argv[i] == "--redis-secret" and i + 1 < len(argv):
            redis_secret = argv[i + 1]; i += 1
        elif argv[i] == "--redis-host" and i + 1 < len(argv):
            redis_host = argv[i + 1]; i += 1
        elif argv[i] == "--redis-port" and i + 1 < len(argv):
            redis_port = int(argv[i + 1]); i += 1
        elif argv[i] == "--summary" and i + 1 < len(argv):
            global SUMMARY_S
            SUMMARY_S = int(argv[i + 1]); i += 1
        i += 1
    log = Log(logdir, time.time)
    log.cleanup()
    stop = []
    signal.signal(signal.SIGTERM, lambda s, f: stop.append(1))
    signal.signal(signal.SIGINT, lambda s, f: stop.append(1))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)
    pub = None
    if redis_secret:
        pw = None
        try:
            for line in open(os.path.expanduser(redis_secret), encoding="utf-8"):
                if line.startswith("PASS="):
                    pw = line.rstrip("\r\n")[5:]
        except OSError:
            pw = None
        if pw:
            pub = RedisPub(log, redis_host, redis_port, pw)
        else:
            log.write("REDIS publish DISABLED: no PASS in %s (receiver runs normally)" % redis_secret)
    c = Canary(log, time.time, pub)
    log.write("RX started port=%d%s" % (port, (" redis=%s:%d stream=%s" % (redis_host, redis_port, RedisPub.STREAM)) if pub else ""))
    seen_exc = set()
    while not stop:
        try:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                data = None
            except InterruptedError:
                continue
            if data is not None:
                c.handle(data, addr[0])
            c.tick()
        except Exception as e:     # never crash on bad input
            sig = "%s:%s" % (type(e).__name__, e)
            if sig not in seen_exc:
                seen_exc.add(sig)
                log.write("EXCEPTION (logged once) %s" % sig)
            time.sleep(0.05)
    if pub is not None:
        pub.stopping = True
        log.write("REDIS sent=%d dropped=%d" % (pub.sent, pub.dropped))
    log.write("RX stopped (SIGTERM)")
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
