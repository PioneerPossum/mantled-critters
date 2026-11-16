#!/usr/bin/env python3
"""End-to-end test of the control board -> visor command path.
Sends each command from the control board (ui-node serial `cmd ...`), waits for the board's own
"cmd #N ok" confirmation AND the visor's matching "WL cmd ..." log line. Opens ports with pyserial
defaults (explicit DTR/RTS resets these boards).  Run: python3 -I tools/command_test.py"""
import os, sys, glob, time, re
for p in glob.glob(os.path.expanduser("~/.platformio/penv/lib/python*/site-packages")):
    sys.path.insert(0, p)
import serial
ui  = serial.Serial(glob.glob("/dev/serial/by-id/usb-Espressif*02:00:00:00:00:02*")[0], 115200, timeout=0.05)
vis = serial.Serial(glob.glob("/dev/serial/by-id/usb-Espressif*02:00:00:00:00:01*")[0], 115200, timeout=0.05)
uilog, vislog = [], []
def pump(secs):
    end = time.time() + secs
    while time.time() < end:
        for port, log in ((ui, uilog), (vis, vislog)):
            d = port.read(4096)
            if d: log.extend(d.decode(errors="replace").replace("\r", "").split("\n"))
pump(1.5)
ui.write(b"s\n"); pump(1.5)
link = [l for l in uilog if "link " in l]
print("control board:", link[-1][:150] if link else "no status line")

tests = [  # (serial command, expected text in the visor log)
    ("cmd face 2",     "WL cmd face -> 2/"),
    ("cmd face 3",     "WL cmd face -> 3/"),
    ("cmd next",       "WL cmd next face"),
    ("cmd face 1",     "WL cmd face -> 1/"),
    ("cmd bright 60",  "WL cmd brightness 60"),
    ("cmd bright 255", "WL cmd brightness 255"),
    ("cmd vol 30",     "WL cmd volume 30"),
    ("cmd mute 1",     "WL cmd mute 1"),
    ("cmd mute 0",     "WL cmd mute 0"),
    ("cmd boop 1",     "WL cmd boop #1"),
    ("cmd boop",       "WL cmd boop (next in pool)"),
]
results = []
for cmd, expect in tests:
    n_ui, n_vis = len(uilog), len(vislog)
    ui.write((cmd + "\n").encode())
    wait = 7.0 if "boop" in cmd else 2.5          # a boop animation blocks the visor loop for a few seconds
    pump(wait)
    new_ui  = [l for l in uilog[n_ui:] if "cmd #" in l]
    new_vis = [l for l in vislog[n_vis:] if l.startswith("WL cmd")]
    ok_ui   = any("ok" in l and "FAILED" not in l for l in new_ui)
    ok_vis  = any(expect in l for l in new_vis)
    results.append((cmd, ok_ui, ok_vis, new_ui[-1] if new_ui else "-", new_vis[-1] if new_vis else "-"))

ui.write(b"s\n"); pump(1.5)
final = [l for l in uilog if "cmds ok" in l][-1]
ui.close(); vis.close()
print("\n== per command (control board confirmed / visor executed)")
for cmd, a, b, lu, lv in results:
    print(f"  {'PASS' if a and b else 'FAIL'}  {cmd:<16} ui-confirm={a} visor-ran={b}   | {lu[:42]} | {lv[:48]}")
print("\ncontrol board final:", final[:170])
resets = sum(1 for l in vislog if "rst:0x" in l or "Guru" in l or "Backtrace" in l)
print("visor resets/panics seen:", resets)
allok = all(a and b for _, a, b, _, _ in results) and resets == 0
print("RESULT:", "PASS" if allok else "FAIL")
sys.exit(0 if allok else 1)
