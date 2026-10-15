"""One-shot OSC verification pass against Arena (UDP 7000). Sends one value per type to
harmless targets, spaced 4 s apart, and records send timestamps. The Legion session reads
back each target over REST. Run only after the Legion confirms its watcher is ready.
Hypothesis A: float params take normalized 0..1 over min..max. B: native units."""
import json, socket, sys, time
from gateway import osc_msg

TARGET = ("192.168.8.147", 7000)
# (id, address, argtype, value, expectation A, note)
STEPS = [
 ("T01", "/composition/dashboard/link7", "f", 0.5, "link7 -> 0.5 (range 0..1)", "float 0..1"),
 ("T02", "/composition/layers/12/video/opacity", "f", 0.5, "L12 opacity -> 0.5", "float 0..1, empty layer"),
 ("T03", "/composition/layers/12/video/effects/transform/rotationz", "f", 0.75, "A: rotationz -> 90 (range -180..180); B: 0.75", "scaling test"),
 ("T04", "/composition/layers/10/clips/5/transport/controls/speed", "f", 0.3, "A: speed -> 3.0 (range 0..10); B: 0.3", "scaling test"),
 ("T05", "/composition/video/effects/twitcheffect/globaltwitch", "f", 0.5, "twitch globaltwitch -> 0.5 (visible ~4 s); effect-name addressing", "effect addressing"),
 ("T06", "/composition/layers/12/video/effects/transform/bypassed", "i", 1, "bypassed -> True", "bool int"),
 ("T07", "/composition/layers/12/video/effects/transform/bypassed", "i", 0, "bypassed -> False", "bool int"),
 ("T08", "/composition/layers/12/video/effects/transform/bypassed", "f", 1.0, "bypassed -> True", "bool float"),
 ("T09", "/composition/layers/12/video/effects/transform/bypassed", "f", 0.0, "bypassed -> False", "bool float"),
 ("T10", "/composition/layers/10/clips/5/connect", "i", 1, "L10C5 connected -> Connected", "clip trigger int"),
 ("T11", "/composition/layers/9/clips/5/connect", "f", 1.0, "L9C5 connected -> Connected", "clip trigger float"),
 ("T12", "/composition/columns/10/connect", "i", 1, "column 10 connected state changes", "column trigger int"),
 ("T13", "/composition/columns/9/connect", "f", 1.0, "column 9 connected state changes", "column trigger float"),
 ("T14", "/composition/decks/4/select", "i", 1, "deck 4 selected -> True", "deck int"),
 ("T15", "/composition/decks/2/select", "f", 1.0, "deck 2 selected -> True", "deck float"),
 ("T16", "/composition/decks/3/select", "i", 1, "deck 3 selected -> True (restore)", "deck restore"),
]

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log = []
    for sid, addr, t, v, exp, note in STEPS:
        ts = time.time()
        sock.sendto(osc_msg(addr, v if t == "f" else int(v)), TARGET)
        rec = {"id": sid, "t": round(ts, 3), "clock": time.strftime("%H:%M:%S", time.localtime(ts)),
               "addr": addr, "type": t, "value": v, "expect": exp, "note": note}
        log.append(rec); print(json.dumps(rec), flush=True)
        time.sleep(4)
    json.dump(log, open("verify_osc_sent.json", "w"), indent=1)
    print("done", len(log), "steps")

if __name__ == "__main__":
    if "--go" not in sys.argv:
        print("refusing: pass --go (run only after the Legion watcher is ready)"); sys.exit(1)
    main()
