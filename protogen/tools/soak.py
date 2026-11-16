#!/usr/bin/env python3
"""Passive soak of the visor wireless stream: reads the receiver for N seconds, injects nothing.
Run: python3 -I tools/soak.py [seconds]   Prints min/avg rx rate, lost, stale intervals, uptime continuity."""
import os, sys, glob, time, re
for p in glob.glob(os.path.expanduser("~/.platformio/penv/lib/python*/site-packages")):
    sys.path.insert(0, p)
import serial
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 150
v = serial.Serial(glob.glob("/dev/serial/by-id/usb-Espressif*02:00:00:00:00:01*")[0], 115200, timeout=0.2)
t0 = time.time(); buf = b""
while time.time() - t0 < DUR: buf += v.read(4096)
v.close()
txt = buf.decode(errors="replace").replace("\r", "")
rows = re.findall(r"WL up=(\d+)s rx=(\d+) \((\d+)/s\) lost=(\d+) bad=(\d+) foreign=(\d+) link=(\w+) seq=(\d+) mic1=(\d+) pk=(\d+).* heap=(\d+)K", txt)
if not rows: print("no WL lines"); sys.exit(1)
ups = [int(r[0]) for r in rows]; rates = [int(r[2]) for r in rows[1:]]
stale = sum(1 for r in rows if r[6] != "UP")
print(f"lines {len(rows)}  uptime {ups[0]}..{ups[-1]} s  monotonic {all(b>=a for a,b in zip(ups,ups[1:]))}")
print(f"rx rate/s: min {min(rates)} avg {sum(rates)/len(rates):.1f} max {max(rates)}   STALE lines {stale}")
print(f"final: rx {rows[-1][1]} lost {rows[-1][3]} bad {rows[-1][4]} foreign {rows[-1][5]} heap {rows[-1][10]}K  mic1 range {min(int(r[8]) for r in rows)}..{max(int(r[8]) for r in rows)}")
ok = all(b>=a for a,b in zip(ups,ups[1:])) and stale == 0 and min(rates) >= 14 and int(rows[-1][3]) == 0
print("SOAK", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
