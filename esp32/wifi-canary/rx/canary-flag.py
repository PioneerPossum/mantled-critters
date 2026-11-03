#!/usr/bin/env python3
"""canary-flag: turn the Wi-Fi canary receiver log + a resolver probe into ONE flag file.

The canary board latches a fault on its LED until reset; nothing in the WC1 packet says so.
This is the receiver-side flag: derived from what the receiver already logs, and it CLEARS
itself after two clean SUMMARY windows (down fast, up slow).

  flag file : ~/.cache/net-diag/canary.flag   (key=value, replaced atomically)
  history   : ~/.cache/net-diag/canary-flag.log   (state changes only)

  state=OK|WARN|FAULT   worst of canary= and resolver=
  canary=...            from the last two SUMMARY lines + SILENT/BACK + staleness
  resolver=...          3 uncached lookups x (system, home router, 1.1.1.1) per probe

Usage: canary-flag.py --once | --watch [SEC=60] | --selftest      Stdlib + dig; safe with python3 -I.
--watch prints a line only when the state changes (suits Monitor).
"""
import datetime
import os
import re
import subprocess
import sys
import tempfile
import time

DIR = os.path.expanduser("~/.cache/net-diag")
FLAG = os.path.join(DIR, "canary.flag")
HIST = os.path.join(DIR, "canary-flag.log")
RESOLVERS = [("system", None), ("router", "192.168.8.1"), ("1.1.1.1", "1.1.1.1")]
STALE_S = 600             # no SUMMARY for this long (receiver or canary down)
WARN_PCT = 90             # one lost tcp probe in 30 is 97%: noise
FAULT_PCT = 50
RANK = {"OK": 0, "WARN": 1, "FAULT": 2}
PROBES = ("gw", "ping", "dnsr", "dnsp", "tcp")


def parse_summary(line):
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) SUMMARY \[(\w+)\] cycles=(\d+) lost=(\d+) (.*)", line)
    if not m:
        return None
    ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    d = {"ts": ts, "id": m.group(2), "lost": int(m.group(4)), "pct": {}, "rssi": None}
    for k, p in re.findall(r"\b(gw|ping|dnsr|dnsp|tcp)=(\d+)%", m.group(5)):
        d["pct"][k] = int(p)
    r = re.search(r"rssi=(-?\d+)/(-?\d+)", m.group(5))
    if r:
        d["rssi"] = min(int(r.group(1)), int(r.group(2)))
    return d


def judge_summary(s):
    """-> (state, reason) for one SUMMARY window."""
    bad = {k: v for k, v in s["pct"].items() if v < WARN_PCT}
    worst = min(s["pct"].values()) if s["pct"] else 100
    txt = ",".join("%s=%d%%" % kv for kv in sorted(bad.items()))
    if worst < FAULT_PCT:
        return "FAULT", txt
    if bad:
        return "WARN", txt
    if s["lost"] >= 3:
        return "WARN", "lost=%d" % s["lost"]
    if s["rssi"] is not None and s["rssi"] < -80:
        return "WARN", "rssi=%d" % s["rssi"]
    return "OK", ""


def judge_canary(lines, now):
    """lines: log lines in time order (today + maybe yesterday)."""
    events = [l for l in lines if " SILENT " in l or " BACK " in l]
    sums = [s for s in (parse_summary(l) for l in lines) if s]
    if events and " SILENT " in events[-1]:
        return "FAULT", "canary silent (" + events[-1].split(" SILENT ", 1)[1][:40] + ")"
    if not sums:
        return "WARN", "no SUMMARY yet"
    if now - sums[-1]["ts"] > STALE_S:
        return "FAULT", "no SUMMARY for %d min" % ((now - sums[-1]["ts"]) // 60)
    # worst of the last two windows: a fault clears only after two clean ones
    res = [judge_summary(s) for s in sums[-2:]]
    # board-side latch (receiver v3 logs FLT once per boot; a later START of the same id = it was reset)
    latched = {}
    for l in lines:
        m = re.search(r"\[(\w+)\] (FLT|START)\b", l)
        if m:
            latched[m.group(1)] = m.group(2) == "FLT"
    ids = sorted(i for i, v in latched.items() if v)
    if ids:
        res.append(("WARN", "board %s latched a fault (reset it to clear)" % ",".join(ids)))
    st, why = max(res, key=lambda r: RANK[r[0]])
    return st, why


def probe_resolvers(runner=None):
    """-> dict name -> True/False (one fresh uncached label per resolver)."""
    out = {}
    for name, ip in RESOLVERS:
        label = "cf%d%d.example.com" % (os.getpid(), int(time.time() * 1000) % 100000)
        cmd = ["dig", "+time=2", "+tries=1", "+noall", "+comments"] + (["@" + ip] if ip else []) + [label, "A"]
        try:
            txt = (runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=8).stdout))(cmd)
        except Exception:
            txt = ""
        m = re.search(r"status: (\w+)", txt)
        out[name] = bool(m and m.group(1) in ("NOERROR", "NXDOMAIN"))
    return out


class ResolverState:
    """A resolver counts as down after 2 consecutive failed probes; up again after 1 good one."""
    def __init__(self):
        self.miss = {n: 0 for n, _ in RESOLVERS}

    def update(self, res):
        for n, ok in res.items():
            self.miss[n] = 0 if ok else self.miss[n] + 1
        down = [n for n, c in self.miss.items() if c >= 2]
        if len(down) == len(self.miss):
            return "FAULT", "all resolvers failing"
        if down:
            return "WARN" if "system" not in down else "FAULT", "resolver down: " + ",".join(down)
        return "OK", ""


def read_log_lines(now):
    lines = []
    for off in (1, 0):
        day = datetime.date.fromtimestamp(now - off * 86400).isoformat()
        try:
            with open(os.path.join(DIR, "wifi-canary-%s.log" % day)) as f:
                lines += f.read().splitlines()
        except OSError:
            pass
    return lines


def combine(canary, resolver):
    st = max((canary[0], resolver[0]), key=lambda s: RANK[s])
    why = "; ".join(w for s, w in (canary, resolver) if s != "OK" and w)
    return st, why


def write_flag(st, canary, resolver, since, now):
    os.makedirs(DIR, exist_ok=True)
    body = "state=%s\nsince=%s\nreason=%s\ncanary=%s\nresolver=%s\nupdated=%s\n" % (
        st, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since)),
        combine(canary, resolver)[1], canary[0], resolver[0], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".canary.flag.")
    with os.fdopen(fd, "w") as f:
        f.write(body)
    os.replace(tmp, FLAG)


def step(rs, prev, now, runner=None):
    """one evaluation; returns (state, reason, canary, resolver, changed)"""
    canary = judge_canary(read_log_lines(now), now)
    resolver = rs.update(probe_resolvers(runner))
    st, why = combine(canary, resolver)
    return st, why, canary, resolver, st != prev


def main(argv):
    if "--selftest" in argv:
        return selftest()
    watch = "--watch" in argv
    every = 60
    if watch and argv.index("--watch") + 1 < len(argv) and argv[argv.index("--watch") + 1].isdigit():
        every = int(argv[argv.index("--watch") + 1])
    rs, prev, since = ResolverState(), None, time.time()
    while True:
        now = time.time()
        st, why, canary, resolver, changed = step(rs, prev, now)
        if changed:
            since = now
            msg = "%s state=%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), st, why)
            print(msg, flush=True)
            if prev is not None:
                with open(HIST, "a") as f:
                    f.write(msg + "\n")
            prev = st
        write_flag(st, canary, resolver, since, now)
        if not watch:
            return 0
        time.sleep(every)


def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok = ok and cond

    def S(t, gw=100, ping=100, dnsr=100, dnsp=100, tcp=100, lost=0, rssi="-43/-40"):
        return "%s SUMMARY [fffe09] cycles=30 lost=%d gw=%d%%/3ms ping=%d%%/19ms dnsr=%d%%/26ms dnsp=%d%%/26ms tcp=%d%%/21ms rssi=%s drop_bad=0 drop_src=0" % (
            t, lost, gw, ping, dnsr, dnsp, tcp, rssi)

    def T(s):
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))

    now = T("2026-10-26 12:10:00")
    clean = [S("2026-10-26 12:00:00"), S("2026-10-26 12:05:00")]
    check("clean -> OK", judge_canary(clean, now)[0] == "OK")
    check("one tcp miss (97%) is noise", judge_canary(clean[:1] + [S("2026-10-26 12:05:00", tcp=97)], now)[0] == "OK")
    check("dns 83% -> WARN", judge_canary([S("2026-10-26 12:05:00", dnsr=83)], now)[0] == "WARN")
    # the 2026-10-23 pattern: associated but ICMP/DNS dead
    check("assoc but icmp/dns dead -> FAULT",
          judge_canary([S("2026-10-26 12:05:00", ping=0, dnsr=0, dnsp=3)], now)[0] == "FAULT")
    check("fault needs TWO clean windows to clear",
          judge_canary([S("2026-10-26 12:00:00", ping=0, dnsr=0), S("2026-10-26 12:05:00")], now)[0] == "FAULT"
          and judge_canary([S("2026-10-26 11:55:00", ping=0), S("2026-10-26 12:00:00"), S("2026-10-26 12:05:00")], now)[0] == "OK")
    check("SILENT -> FAULT", judge_canary(clean + ["2026-10-26 12:08:00 SILENT no valid packet for 45s (last at 12:07:15)"], now)[0] == "FAULT")
    check("BACK after SILENT clears the silent part",
          judge_canary(["2026-10-26 12:08:00 SILENT no valid packet for 45s (last at 12:07:15)",
                        "2026-10-26 12:09:00 BACK after silent=60s", S("2026-10-26 12:05:00"), S("2026-10-26 12:09:30")], now)[0] == "OK")
    check("stale SUMMARY -> FAULT", judge_canary(clean, T("2026-10-26 12:30:00"))[0] == "FAULT")
    check("weak rssi -> WARN", judge_canary([S("2026-10-26 12:05:00", rssi="-85/-78")], now)[0] == "WARN")
    check("lost>=3 -> WARN", judge_canary([S("2026-10-26 12:05:00", lost=4)], now)[0] == "WARN")
    flt = "2026-10-26 12:03:00 [fffe09] FLT device reports a latched fault (cleared only by its reset) seq=3 up=30 rssi=-58"
    check("FLT line -> WARN until the board restarts", judge_canary(clean + [flt], now)[0] == "WARN")
    check("START after FLT clears it", judge_canary([flt, "2026-10-26 12:04:00 [fffe09] START (seq reset 40->1) up=1 rst=1 rssi=-40"] + clean, now)[0] == "OK")
    check("real log line parses", parse_summary(clean[0])["pct"]["dnsp"] == 100)

    rs = ResolverState()
    good = {"system": True, "router": True, "1.1.1.1": True}
    check("resolver ok", rs.update(good)[0] == "OK")
    check("one failed probe is not enough", rs.update({**good, "system": False})[0] == "OK")
    check("second consecutive fail -> system down is FAULT", rs.update({**good, "system": False})[0] == "FAULT")
    check("one good probe recovers", rs.update(good)[0] == "OK")
    rs2 = ResolverState(); rs2.update({**good, "router": False})
    check("router down alone is WARN", rs2.update({**good, "router": False})[0] == "WARN")
    rs3 = ResolverState(); bad = {k: False for k in good}; rs3.update(bad)
    check("all down -> FAULT", rs3.update(bad)[0] == "FAULT")
    r = probe_resolvers(lambda c: ";; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 1")
    check("NXDOMAIN counts as an answer", all(r.values()))
    r = probe_resolvers(lambda c: ";; connection timed out; no servers could be reached")
    check("timeout counts as a failure", not any(r.values()))
    check("combine takes the worst", combine(("OK", ""), ("WARN", "x"))[0] == "WARN" and combine(("FAULT", "y"), ("OK", ""))[0] == "FAULT")
    print("ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
