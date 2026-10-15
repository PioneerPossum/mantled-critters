"""Low-latency beat events for magi-audio.

aubio reports a beat when it sees one in a hop (~10.7 ms). This turns that into a wall-clock
timestamp and publishes it right away, instead of the 2 s stats cycle.

Sinks (all optional, off unless BEAT_ENABLE=1):
  - UDP JSON datagram to BEAT_UDP ("host:port"), e.g. the Legion for Arena beat-phase sync
  - Redis PUBLISH on magi:audio:beat plus SET magi:audio:beat_last (ex 5)
Payload: {"t": <epoch s of the beat>, "sent": <epoch s>, "n": <beat counter>, "bpm": <aubio bpm>,
          "conf": <0..1>, "bar_guess": <0..3 or null>}

LIMITS: aubio gives beats, not downbeats. bar_guess is only a guess (the beat position in a
4-beat window with the strongest onset energy, once it has been consistent for 4 bars) and is
null until then. Do not treat it as ground truth.
"""
import json
import socket
from collections import deque


class BeatClock:
    def __init__(self, rate, delay_s=0.0):
        self.rate = rate
        self.delay_s = delay_s        # extra fixed latency (VBAN transport + aubio), tune against audio
        self.samples = 0              # mono samples fed to aubio so far
        self.n = 0
        self.energy = deque(maxlen=16)   # onset energy of the last 16 beats
        self.votes = deque(maxlen=4)

    def fed(self, hop_samples):
        self.samples += hop_samples

    def beat(self, now, beat_sample, energy):
        """beat_sample: aubio's tempo.get_last() (sample index in the stream). Returns the beat dict."""
        age = (self.samples - beat_sample) / self.rate if beat_sample else 0.0
        age = min(max(age, 0.0), 0.25) + self.delay_s     # aubio reports 0..few hops late; clamp nonsense
        self.n += 1
        self.energy.append(energy)
        return {"t": now - age, "n": self.n, "bar_guess": self._bar_guess()}

    def _bar_guess(self):
        """Bar position of the beat just counted: 0 = guessed downbeat, 1..3 = later beats."""
        if len(self.energy) < 16:
            return None
        e = list(self.energy)
        start = self.n - len(e) + 1                      # beat number of e[0]
        sums = [sum(e[i::4]) for i in range(4)]
        best = max(range(4), key=lambda i: sums[i])
        if sums[best] < 1.3 * (sum(sums) - sums[best]) / 3:   # no clearly stronger beat in the bar
            self.votes.clear()
            return None
        downbeat = (start + best) % 4                    # beat numbers congruent to this are downbeats
        self.votes.append(downbeat)
        if len(self.votes) < 4 or len(set(self.votes)) != 1:
            return None
        return (self.n - downbeat) % 4


class BeatSink:
    def __init__(self, udp_target, redis_client):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if udp_target else None
        self.addr = None
        if udp_target:
            host, port = udp_target.split(":")
            self.addr = (host, int(port))
        self.r = redis_client

    def emit(self, payload):
        data = json.dumps(payload, separators=(",", ":"))
        if self.sock:
            try:
                self.sock.sendto(data.encode(), self.addr)
            except OSError:
                pass
        if self.r is not None:
            try:
                self.r.publish("magi:audio:beat", data)
                self.r.set("magi:audio:beat_last", data, ex=5)
            except Exception:
                pass
