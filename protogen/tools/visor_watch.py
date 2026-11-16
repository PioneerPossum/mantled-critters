#!/usr/bin/env python3
"""Passive watcher for the visor S3 (#1). Opens the port with defaults (no reset), timestamps every line to a log,
and exits with a verdict when the board reboots (uptime goes back), goes silent, or leaves USB. Usage: visor_watch.py LOG [max_s]"""
import sys, time, re, os, serial
PORT = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_02:00:00:00:00:01-if00"
log = open(sys.argv[1], "a", buffering=1)
max_s = float(sys.argv[2]) if len(sys.argv) > 2 else 3 * 3600
t0 = time.time(); s = None; buf = b""; last_rx = time.time(); gone_since = None
last_up = None; first_up = None; min_heap = None; last_heap = None; verdict = None; reboot_at = None
def w(msg): log.write(time.strftime("%H:%M:%S ") + msg + "\n")
w("--- watch start")
while True:
    now = time.time()
    if now - t0 > max_s: verdict = "TIME LIMIT reached, board still running"; break
    if reboot_at and now - reboot_at > 12: break
    if s is None:
        if os.path.exists(PORT):
            try: s = serial.Serial(PORT, 115200, timeout=0.5); gone_since = None; last_rx = now; w("--- port open")
            except Exception as e: time.sleep(0.5)
        else:
            gone_since = gone_since or now
            if now - gone_since > 20: verdict = verdict or "USB GONE for 20 s (unplugged, or the S3 lost power)"; break
            time.sleep(0.3)
        continue
    try: d = s.read(4096)
    except Exception as e:
        w(f"--- port error {e}")
        try: s.close()
        except Exception: pass
        s = None; continue
    if d:
        last_rx = now; buf += d
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            t = line.decode("utf-8", "replace").rstrip()
            w(t)
            m = re.search(r"WL up=(\d+)s.*heap=(\d+)K", t)
            if m:
                up, heap = int(m.group(1)), int(m.group(2))
                if first_up is None: first_up = up
                if last_up is not None and up < last_up and not reboot_at:
                    verdict = f"REBOOT: uptime went from {last_up}s back to {up}s"; reboot_at = now
                last_up, last_heap = up, heap
                min_heap = heap if min_heap is None else min(min_heap, heap)
            if re.search(r"Guru|Backtrace|rst:0x|Brownout|panic|abort\(\)|assert", t) and not reboot_at:
                verdict = "CRASH/RESET TEXT: " + t[:120]; reboot_at = now
    elif now - last_rx > 15:
        verdict = f"SILENT: no serial output for 15 s (last uptime {last_up}s), port still present"; break
w("--- verdict: " + str(verdict))
print(f"verdict: {verdict}\nwatched {time.time()-t0:.0f}s, first up={first_up}s last up={last_up}s, heap last={last_heap}K min={min_heap}K\nlog: {sys.argv[1]}")
