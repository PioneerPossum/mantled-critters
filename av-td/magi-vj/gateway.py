"""OSC gateway for autonomous Resolume control, with tier enforcement.

Everything the AI wants to do to Arena goes through Gateway.request(). The
gateway is default-deny, applies the three tiers in policy.json, honours the
human-handoff mode and per-parameter locks (the "interaction table"), and logs
every decision so it can later be used as training data.

Modes (Redis key magi:vj:mode):
  auto    AI may send tier 1 and (quantized) tier 2
  assist  AI may send tier 1 only; tier 2 is logged as a proposal, not sent
  manual  human has control; AI sends nothing
Locks (Redis set magi:vj:locks): address patterns owned by the human; the AI
never writes a matching address. Dry-run unless VJ_ENABLE=1.
"""
import json, os, re, socket, struct, time

POLICY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy.json")


def _rx(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"; i += 2
        elif pattern[i] == "*":
            out += "[^/]*"; i += 1
        elif pattern[i] == "{":
            j = pattern.index("}", i)
            out += "(?:" + "|".join(re.escape(a) for a in pattern[i + 1:j].split(",")) + ")"; i = j + 1
        else:
            out += re.escape(pattern[i]); i += 1
    return re.compile("^" + out + "$")


def osc_msg(addr, value):
    def pad(b): return b + b"\0" * (4 - len(b) % 4)
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        return pad(addr.encode()) + pad(b",i") + struct.pack(">i", value)
    return pad(addr.encode()) + pad(b",f") + struct.pack(">f", float(value))


class Gateway:
    def __init__(self, redis=None, target=None, policy_path=POLICY_PATH, enable=None, clock=time.time, mode_override=None):
        p = json.load(open(policy_path))
        self.deny = [_rx(x) for x in p["tier3_deny"]]
        self.t1 = [(_rx(r["pattern"]), r) for r in p["tier1"]["rules"]]
        self.t2 = [(_rx(r["pattern"]), r) for r in p["tier2"]["rules"]]
        self.quant_bars = p["tier2"].get("quantize_bars", 4)
        self.redis = redis
        self.mode_override = mode_override
        self.enable = (os.environ.get("VJ_ENABLE", "0") == "1") if enable is None else enable
        host, port = (target or os.environ.get("VJ_OSC_TARGET", "192.168.8.147:7000")).split(":")
        self.target = (host, int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.clock = clock
        self.last_sent = {}      # addr -> (t, value)
        self.last_fire = {}      # tier2 pattern -> t
        self.queue = []          # (addr, value, source) waiting for a bar boundary
        self.bpm, self.bar_origin = 120.0, clock()

    # -- state ---------------------------------------------------------
    def mode(self):
        if self.mode_override:
            return self.mode_override
        if not self.redis:
            return "auto"
        return self.redis.get("magi:vj:mode") or "manual"   # unknown/missing = safe

    def locked(self, addr):
        if not self.redis:
            return False
        return any(_rx(p).match(addr) for p in self.redis.smembers("magi:vj:locks"))

    def set_tempo(self, bpm, bar_origin=None):
        self.bpm = bpm
        if bar_origin is not None:
            self.bar_origin = bar_origin

    # -- decision ------------------------------------------------------
    def classify(self, addr):
        if any(r.match(addr) for r in self.deny):
            return 3, None
        for rx, rule in self.t1:
            if rx.match(addr):
                return 1, rule
        for rx, rule in self.t2:
            if rx.match(addr):
                return 2, rule
        return 0, None  # not in any allowlist -> default deny

    def request(self, addr, value, source="ai"):
        now = self.clock()
        tier, rule = self.classify(addr)
        decision, reason = self._decide(addr, value, now, tier, rule)
        if reason != "rate limit":      # rate-limit drops are noise, not training data
            self._log(addr, value, source, tier, decision, reason)
        return decision, reason

    def _decide(self, addr, value, now, tier, rule):
        if tier in (0, 3):
            return "denied", "tier3 gated" if tier == 3 else "not allowlisted"
        mode = self.mode()
        if mode == "manual":
            return "denied", "mode manual"
        if self.locked(addr):
            return "denied", "locked by human"
        if self.enable and not rule.get("verified"):
            return "denied", "unverified OSC address/type"
        if tier == 1:
            lo, hi = rule["range"]
            if "max_value" in rule:
                value = min(float(value), rule["max_value"])
            norm = max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))
            prev = self.last_sent.get(addr)
            if prev:
                if now - prev[0] < rule["min_interval_s"]:
                    return "dropped", "rate limit"
                step = rule.get("max_step")
                if step is not None:
                    norm = prev[1] + max(-step, min(step, norm - prev[1]))  # slew limit (normalized)
                if abs(norm - prev[1]) < 0.004:
                    return "dropped", "rate limit"   # unchanged: don't resend (logged as noise-free)
            self._send(addr, norm, now)
            return "sent", f"tier1 norm={norm:.4f}"
        # tier 2
        if mode == "assist":
            return "proposed", "assist mode: not sent"
        if now - self.last_fire.get(rule["pattern"], 0) < rule["cooldown_s"]:
            return "dropped", "cooldown"
        self.queue.append((addr, 1 if rule.get("kind") == "trigger" else int(bool(value)), rule))
        return "queued", "waiting for bar boundary"

    def _send(self, addr, value, now):
        self.last_sent[addr] = (now, value)
        if self.enable:
            self.sock.sendto(osc_msg(addr, value), self.target)

    def tick(self):
        """Call frequently; releases queued tier-2 actions on a quantize boundary."""
        now = self.clock()
        quant = self.quant_bars * 4 * 60.0 / self.bpm
        n_prev = int((self.last_tick - self.bar_origin) // quant) if getattr(self, "last_tick", None) else None
        n_now = int((now - self.bar_origin) // quant)
        self.last_tick = now
        if n_prev is None or n_now == n_prev or not self.queue or self.mode() != "auto":
            return []
        fired, self.queue = self.queue, []
        out = []
        for addr, value, rule in fired:
            if self.locked(addr):
                continue
            self.last_fire[rule["pattern"]] = now
            self._send(addr, value, now)
            self._log(addr, value, "ai", 2, "sent", "tier2 on boundary")
            out.append(addr)
        return out

    def _log(self, addr, value, source, tier, decision, reason):
        rec = {"t": round(self.clock(), 3), "addr": addr, "value": value, "source": source,
               "tier": tier, "decision": decision, "reason": reason, "mode": self.mode(),
               "bpm": self.bpm, "live": self.enable}
        if self.redis:
            self.redis.xadd("magi:vj:actions", {"j": json.dumps(rec)}, maxlen=50000, approximate=True)
