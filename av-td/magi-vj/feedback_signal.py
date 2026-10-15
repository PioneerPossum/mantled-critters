"""Turn operator + crowd feedback into a bounded 'intensity multiplier' for the mood engine.

Records come from the Redis stream magi:vj:feedback. Each has: source ("operator" | "crowd"), weight, and (new)
intensity/sentiment in [-1, 1]. Operator feedback is WEIGHTED (signed-in user, weight 0.5/1/2); crowd reactions
are only INDICATED: each carries a tiny weight and the whole crowd contribution is capped, so a crowd can nudge
the visuals but never override the operator. Effects decay with TAU_S. The engine multiplies its tier-1 targets by
the result and STILL applies its own hard caps afterwards, so 'more' can never exceed a cap.
"""
import math

TAU_S = 60.0
OP_GAIN, CROWD_GAIN = 0.40, 0.50
CROWD_CAP, MULT_MIN, MULT_MAX = 0.30, 0.55, 1.40
CROWD_WEIGHT, OP_WEIGHTS = 0.05, {1: 0.5, 2: 1.0, 3: 2.0}

# reaction -> (intensity, sentiment)
REACTIONS = {"like": (0.0, 1.0), "dislike": (-0.5, -1.0), "more": (1.0, 0.5), "less": (-1.0, -0.5)}
# operator category -> (intensity, sentiment); suggest can override intensity
CATEGORY = {"good": (0.0, 1.0), "too_much": (-1.0, -0.5), "too_little": (1.0, -0.3), "bad_timing": (0.0, -0.5),
            "missed_opportunity": (0.0, -0.3), "note": (0.0, 0.0)}
SUGGEST_INTENSITY = {"more_intensity": 1.0, "less_intensity": -1.0, "calmer_clip": -0.5}


def classify(category=None, suggest="", reaction=None):
    """-> (intensity, sentiment) for a feedback item."""
    if reaction:
        return REACTIONS.get(reaction, (0.0, 0.0))
    i, s = CATEGORY.get(category, (0.0, 0.0))
    return SUGGEST_INTENSITY.get(suggest, i), s


def clip(x, lo, hi):
    return max(lo, min(hi, x))


class FeedbackMeter:
    def __init__(self):
        self.items = []     # (t, source, weight, intensity, sentiment)

    def add(self, rec, now=None):
        try:
            t = float(rec.get("t") or now)
            w = float(rec.get("weight", 1.0))
            i, s = float(rec.get("intensity", 0.0)), float(rec.get("sentiment", 0.0))
        except (TypeError, ValueError):
            return
        src = "operator" if rec.get("source") in ("operator", "review-page") else "crowd"
        self.items.append((t, src, clip(w, 0.0, 2.0), clip(i, -1, 1), clip(s, -1, 1)))
        self.items = self.items[-400:]

    def _sum(self, now, src, field):
        tot = 0.0
        for t, sr, w, i, s in self.items:
            age = now - t
            if sr == src and 0 <= age < 5 * TAU_S:
                tot += w * (i if field == "i" else s) * math.exp(-age / TAU_S)
        return tot

    def components(self, now):
        op = clip(self._sum(now, "operator", "i"), -1.0, 1.0)
        crowd = clip(self._sum(now, "crowd", "i"), -CROWD_CAP, CROWD_CAP)
        return op, crowd

    def multiplier(self, now):
        op, crowd = self.components(now)
        return clip(1.0 + OP_GAIN * op + CROWD_GAIN * crowd, MULT_MIN, MULT_MAX)

    def crowd_sentiment(self, now):
        """-1..1 mood of the crowd over the last 60 s (weights ignored: a head-count of feelings)."""
        pos = neg = 0
        for t, sr, w, i, s in self.items:
            if sr == "crowd" and 0 <= now - t < 60:
                pos += s > 0; neg += s < 0
        n = pos + neg
        return (pos - neg) / n if n else 0.0
