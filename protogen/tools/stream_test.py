#!/usr/bin/env python3
"""End-to-end test of the visor wireless sensor stream (sensor-node -> proot-s3-wireless).

Reads BOTH boards at once, injects one synthetic boop (`b`) and one synthetic button
press (`n`) on the sensor node, and checks the receiver's log against pass criteria.
Run:  python3 -I ~/claude-projects/protogen/tools/stream_test.py [seconds]
Needs pyserial (the PlatformIO penv has it). Opens with pyserial defaults; explicitly setting DTR/RTS resets these boards.
"""
import os, sys, glob, time, re
for p in glob.glob(os.path.expanduser("~/.platformio/penv/lib/python*/site-packages")):
    sys.path.insert(0, p)
import serial

SENSOR = "usb-Espressif*02:00:00:00:00:02*"
VISOR  = "usb-Espressif*02:00:00:00:00:01*"
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 30

def opn(pattern):
    path = glob.glob("/dev/serial/by-id/" + pattern)[0]
    return serial.Serial(path, 115200, timeout=0.05)   # defaults: do NOT touch DTR/RTS (setting them resets the S3)

sen, vis = opn(SENSOR), opn(VISOR)
sbuf, vbuf = b"", b""
t0 = time.time(); did_b = did_n = False
while time.time() - t0 < DUR:
    sbuf += sen.read(4096); vbuf += vis.read(4096)
    el = time.time() - t0
    if el > DUR * 0.40 and not did_b: sen.write(b"b\n"); did_b = True
    if el > DUR * 0.60 and not did_n: sen.write(b"n\n"); did_n = True
sen.close(); vis.close()

# ---- DETACH PHASE: nobody reading either port (the real standalone situation) ----
DETACH = int(sys.argv[2]) if len(sys.argv) > 2 else 25
_strip = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
_seq_before = re.findall(rb"STREAM \S+\s+ch\d seq (\d+)", _strip.sub(b"", sbuf))
seq_before = int(_seq_before[-1]) if _seq_before else None
t_detach = time.time()
time.sleep(DETACH)
sen, vis = opn(SENSOR), opn(VISOR)
sb2, vb2 = b"", b""
t1 = time.time()
while time.time() - t1 < 8:
    sb2 += sen.read(4096); vb2 += vis.read(4096)
sen.close(); vis.close()
elapsed_detached = time.time() - t_detach

ansi = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
stext = ansi.sub(b"", sbuf).decode(errors="replace").replace("\r", "")
vtext = vbuf.decode(errors="replace").replace("\r", "")

stats = re.findall(r"WL up=(\d+)s rx=(\d+) \((\d+)/s\) lost=(\d+) bad=(\d+) foreign=(\d+) link=(\w+) seq=(\d+) mic1=(\d+) pk=(\d+) mic2=(\d+) rng=(\d+) boops=(\d+) btns=(\d+) heap=(\d+)K", vtext)
print("== RECEIVER (visor controller), last 3 stats lines")
for l in [x for x in vtext.split("\n") if x.startswith("WL up=")][-3:]: print("  ", l)
print("== RECEIVER events")
for l in [x for x in vtext.split("\n") if x.startswith("WL boop") or x.startswith("WL button")]: print("  ", l)
print("== SENDER, last STREAM / MIC1 / BTN lines")
for key in ("STREAM", "MIC1", "BTN GPIO10", "MIC2"):
    ls = [x for x in stext.split("\n") if x.startswith(key)]
    if ls: print("  ", ls[-1])

res = []
def chk(name, ok, detail=""): res.append((name, ok, detail))
if stats:
    ups = [int(s[0]) for s in stats]
    chk("receiver ran without reset", all(b >= a for a, b in zip(ups, ups[1:])) and "Guru" not in vtext and "Backtrace" not in vtext and "abort()" not in vtext, f"uptime {ups[0]}..{ups[-1]} s")
    rates = [int(s[2]) for s in stats[2:]] or [int(s[2]) for s in stats]
    avg = sum(rates) / len(rates)
    chk("rx rate about 20/s", 14 <= avg <= 24, f"avg {avg:.1f}/s")
    last = stats[-1]; rx, lost = int(last[1]), int(last[3])
    chk("lost packets under 5%", rx > 0 and lost * 100 <= 5 * (rx + lost), f"rx {rx}, lost {lost}")
    chk("link UP", last[6] == "UP", f"link={last[6]}")
    chk("no bad/foreign packets", int(last[4]) == 0 and int(last[5]) == 0, f"bad {last[4]} foreign {last[5]}")
    chk("test boop reached the visor", "WL boop" in vtext, "")
    chk("test button reached the visor", "WL button" in vtext, "")
    m_rx = [int(s[8]) for s in stats[2:]]
    m_tx = [int(x) for x in re.findall(r"lvl (\d+) pk", stext)]
    if m_rx and m_tx:
        chk("mic level follows sender (same order of magnitude)", abs(sum(m_rx)/len(m_rx) - sum(m_tx)/len(m_tx)) <= max(30, 0.6 * sum(m_tx)/len(m_tx)), f"rx avg {sum(m_rx)/len(m_rx):.0f}, tx avg {sum(m_tx)/len(m_tx):.0f}")
else:
    chk("receiver produced WL stats", False, "no WL lines captured")
sm = re.findall(r"ack (\d+) nack (\d+)", stext)
if len(sm) >= 2:
    da = int(sm[-1][0]) - int(sm[0][0]); dn = int(sm[-1][1]) - int(sm[0][1])
    chk("sender acks > 95% (delta over the run)", da > 0 and dn * 100 <= 5 * (da + dn), f"ack +{da} nack +{dn}")
# ---- detach-phase checks ----
post_stats = re.findall(r"WL up=(\d+)s rx=(\d+) \((\d+)/s\).* link=(\w+) seq=(\d+)", vb2.decode(errors="replace"))
post_sender = re.findall(rb"STREAM \S+\s+ch\d seq (\d+)", _strip.sub(b"", sb2))
if post_stats:
    chk("after %ds with nobody reading: link still UP" % DETACH, post_stats[-1][3] == "UP", f"link={post_stats[-1][3]}")
    rate = sum(int(x[2]) for x in post_stats[-2:]) / len(post_stats[-2:])
    chk("after detach: rx rate still about 20/s", 14 <= rate <= 24, f"{rate:.0f}/s")
else:
    chk("after detach: receiver output", False, "no WL lines")
if seq_before is not None and post_sender:
    dseq = int(post_sender[0]) - seq_before
    expect = 20 * (elapsed_detached - 8)
    chk("sender kept streaming at pace while unread", dseq >= 0.8 * expect, f"seq +{dseq} vs about {expect:.0f} expected")
else:
    chk("sender seq readable after detach", False, "no STREAM line")
print("== RESULT")
for n, ok, d in res: print(("  PASS " if ok else "  FAIL ") + n + (f"  [{d}]" if d else ""))
sys.exit(0 if all(ok for _, ok, _ in res) else 1)
