#!/usr/bin/env python3
"""sense: sensor detection + request/feedback inbox for the cybrdek deck (stdlib only).

  sense scan [--bus N] [--probe] [--no-save] [--json]   detect what is plugged in, write a findings report
  sense baseline                                          scan and record today's devices as "built in"
  sense last                                              print the newest findings report
  sense req "text"                                        leave a request (Gil, on the deck)
  sense reqs [--all] [--json]                             list open requests (sessions read this over ssh)
  sense show ID | reply ID "text" [--by NAME] [--close] | close ID
  sense hint                                              one line for the login prompt (silent if nothing new)

Safety: only the software I2C bus (3, GPIO22/23) is allowed (allow-list); every other bus is refused (0/1/20/21
carry the power chip, HDMI and the camera). --probe reads a few well-known chip-ID registers: no data writes, but
i2cget first sends the register-pointer byte, so do not point it at parts you do not know.
Data lives in ~/sensor-desk (override with SENSE_DIR): findings/, requests.jsonl, baseline.json.
"""
import argparse, datetime as dt, fcntl, glob, json, os, re, subprocess, sys

BASE = os.environ.get("SENSE_DIR", os.path.expanduser("~/sensor-desk"))
FIND = os.path.join(BASE, "findings")
REQ = os.path.join(BASE, "requests.jsonl")
BASELINE = os.path.join(BASE, "baseline.json")
LATEST_JSON = os.path.join(FIND, "latest.json")
DEFAULT_BUSES = [3]
ALLOWED_BUSES = {3}  # allow-list: only the software bus on GPIO22/23 is ever scanned

# 7-bit address -> parts that commonly live there (address match is a hint, not proof)
I2C_HINTS = {
    0x0D: ["QMC5883L magnetometer"], 0x10: ["VEML7700 light"], 0x18: ["MCP9808 temp", "LIS3DH accel"],
    0x19: ["LIS3DH accel"], 0x1D: ["ADXL345 accel (alt)"], 0x1E: ["HMC5883L / LSM303 magnetometer"],
    0x23: ["BH1750 light"], 0x29: ["VL53L0X / VL53L1X distance", "TSL2591 light"],
    0x38: ["AHT10 / AHT20 temp+humidity", "PCF8574A expander"], 0x39: ["APDS-9960 gesture/light", "TSL2561 light"],
    0x3C: ["SSD1306 / SH1106 OLED"], 0x3D: ["SSD1306 OLED (alt)"],
    0x40: ["INA219 power", "HTU21D / Si7021 humidity", "PCA9685 PWM"], 0x41: ["INA219 (alt)"],
    0x44: ["SHT3x / SHT4x temp+humidity"], 0x45: ["SHT3x (alt)"],
    0x48: ["ADS1115 / ADS1015 ADC", "TMP102 / TMP117 temp", "PCF8591"], 0x49: ["ADS1115 (alt)", "TSL2561"],
    0x4A: ["ADS1115 (alt)"], 0x4B: ["ADS1115 (alt)"], 0x53: ["ADXL345 accel", "EEPROM"],
    0x5A: ["MLX90614 IR temp", "CCS811 air quality", "MPR121 touch"], 0x5B: ["CCS811 (alt)", "MPR121"],
    0x5C: ["BH1750 (alt)", "AM2320 temp+humidity"], 0x60: ["MCP4725 DAC", "Si1145 light"],
    0x61: ["SCD30 CO2"], 0x62: ["SCD40 / SCD41 CO2"], 0x68: ["MPU-6050 / 9250 IMU", "ICM-20948 IMU", "DS3231 / DS1307 RTC"],
    0x69: ["MPU-6050 (alt)", "ICM-20948 (alt)"], 0x6A: ["LSM6DS3 / LSM6DSO IMU"], 0x6B: ["LSM6DS3 (alt)"],
    0x70: ["TCA9548A mux", "HT16K33 display"], 0x76: ["BME280 / BMP280 / BME680 env", "MS5611 pressure"],
    0x77: ["BME280 / BMP280 / BMP180 / BME680 env"],
}
for a in range(0x20, 0x28):
    I2C_HINTS.setdefault(a, []).append("PCF8574 / MCP23017 I/O expander")
for a in range(0x50, 0x58):
    I2C_HINTS.setdefault(a, []).append("AT24Cxx EEPROM")

# chip-ID probes: addr set -> (register, {value: name}); i2cget reads, never writes data
PROBES = [
    ({0x76, 0x77}, 0xD0, {0x60: "BME280", 0x58: "BMP280", 0x61: "BME680/688", 0x55: "BMP180"}),
    ({0x68, 0x69}, 0x75, {0x68: "MPU-6050", 0x70: "MPU-6500", 0x71: "MPU-9250", 0x73: "MPU-9255"}),
    ({0x68, 0x69}, 0x00, {0xEA: "ICM-20948"}),
    ({0x29}, 0xC0, {0xEE: "VL53L0X"}),
    ({0x18, 0x19}, 0x0F, {0x33: "LIS3DH"}),
    ({0x6A, 0x6B}, 0x0F, {0x69: "LSM6DS3", 0x6C: "LSM6DSO", 0x6A: "LSM6DS3TR-C"}),
]


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def now():
    return dt.datetime.now().astimezone()


def parse_i2cdetect(out):
    """[(addr, token)] for every cell that is not '--'. Cells are fixed 3-char columns after 'NN:'."""
    hits = []
    for line in out.splitlines()[1:]:
        m = re.match(r"^([0-9a-f]0):", line)
        if not m:
            continue
        base = int(m.group(1), 16)
        for i in range(16):
            tok = line[3 + 3 * i: 6 + 3 * i].strip()
            if tok and tok != "--":
                hits.append((base + i, tok))
    return hits


def scan_i2c(bus, probe):
    rc, out = run(["i2cdetect", "-y", str(bus)], timeout=20)
    if rc != 0:
        return {"bus": bus, "error": (out or "i2cdetect failed").strip()[:200], "devices": []}
    devs = []
    for addr, tok in parse_i2cdetect(out):
        row = next((l for l in out.splitlines() if l.startswith(f"{addr & 0xF0:02x}:")), "")
        d = {"addr": f"0x{addr:02x}", "busy": tok == "UU", "hints": I2C_HINTS.get(addr, []), "ids": [],
             "evidence": [f"`i2cdetect -y {bus}` row: `{row.strip()}`"]}
        if probe and not d["busy"]:
            for addrs, reg, table in PROBES:
                if addr in addrs:
                    rc2, val = run(["i2cget", "-y", str(bus), f"0x{addr:02x}", f"0x{reg:02x}"], timeout=5)
                    try:
                        v = int(val.strip(), 16)
                    except ValueError:
                        continue
                    d["evidence"].append(f"`i2cget -y {bus} 0x{addr:02x} 0x{reg:02x}` -> 0x{v:02x}")
                    if v in table:
                        d["ids"].append(table[v])
        devs.append(d)
    return {"bus": bus, "devices": devs}


def scan_usb():
    rc, out = run(["lsusb"])
    res = []
    for line in out.splitlines():
        m = re.match(r"Bus (\d+) Device (\d+): ID ([0-9a-f]{4}:[0-9a-f]{4})\s*(.*)", line)
        if m and "root hub" not in m.group(4).lower():
            res.append({"id": m.group(3), "name": m.group(4), "bus": m.group(1), "dev": m.group(2)})
    return res


def scan_serial():
    res = []
    for dev in sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")):
        rc, out = run(["udevadm", "info", "-q", "property", "-n", dev], timeout=5)
        p = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        res.append({"dev": dev, "id": f"{p.get('ID_VENDOR_ID', '?')}:{p.get('ID_MODEL_ID', '?')}",
                    "name": (p.get("ID_MODEL_FROM_DATABASE") or p.get("ID_MODEL") or "").replace("_", " "),
                    "serial": p.get("ID_SERIAL_SHORT", "")})
    return res


def scan_all(buses, probe):
    spi = sorted(glob.glob("/dev/spidev*"))
    w1 = sorted(os.path.basename(p) for p in glob.glob("/sys/bus/w1/devices/*") if "bus_master" not in p)
    video = [{"dev": os.path.basename(p), "name": open(os.path.join(p, "name")).read().strip()}
             for p in sorted(glob.glob("/sys/class/video4linux/video*")) if os.path.exists(os.path.join(p, "name"))]
    return {"time": now().isoformat(timespec="seconds"), "host": os.uname().nodename,
            "i2c": [scan_i2c(b, probe) for b in buses], "usb": scan_usb(), "serial": scan_serial(),
            "spi": spi, "onewire": w1, "video": video}


def keys(scan):
    k = set()
    for bus in scan["i2c"]:
        for d in bus["devices"]:
            k.add(f"i2c:{bus['bus']}:{d['addr']}")
    for u in scan["usb"]:
        k.add(f"usb:{u['id']}")
    for s in scan["serial"]:
        k.add(f"serial:{s['dev']}")
    for s in scan["spi"]:
        k.add(f"spi:{s}")
    for w in scan["onewire"]:
        k.add(f"w1:{w}")
    return k


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def render(scan, prev, base):
    k, pk, bk = keys(scan), (keys(prev) if prev else None), (keys(base) if base else None)
    L = [f"# Sensor scan {scan['time']} on {scan['host']}", ""]
    if pk is not None:
        new, gone = sorted(k - pk), sorted(pk - k)
        L += ["## Changes since the previous scan", f"- New: {', '.join(new) if new else 'none'}",
              f"- Removed: {', '.join(gone) if gone else 'none'}", ""]
    if bk is not None:
        extra = sorted(k - bk)
        L += ["## Not in the built-in baseline", ", ".join(extra) if extra else "nothing (only built-in hardware)", ""]
    L.append("## Devices")
    n = 0
    for bus in scan["i2c"]:
        if bus.get("error"):
            L += ["", f"### I2C bus {bus['bus']}: scan failed", f"- Evidence: {bus['error']}"]
        elif not bus["devices"]:
            L += ["", f"### I2C bus {bus['bus']}: nothing answered",
                  "- Open questions: wiring, sensor power, SDA=GPIO22 / SCL=GPIO23, pull-ups (4.7k external for real modules)"]
        for d in bus["devices"]:
            n += 1
            if d["busy"]:
                chip, conf = "claimed by a kernel driver (UU)", "n/a"
            elif d["ids"]:
                chip, conf = " / ".join(d["ids"]), "high (chip-ID register read)"
            elif d["hints"]:
                chip, conf = "unconfirmed; candidates: " + "; ".join(d["hints"]), "low (address match only)"
            else:
                chip, conf = "unknown", "none"
            L += ["", f"### I2C bus {bus['bus']} {d['addr']}", f"- Detected chip: {chip}",
                  "- Evidence: " + "; ".join(d["evidence"]), f"- Confidence: {conf}",
                  "- Open questions: " + ("which part is wired here? run `sense scan --probe`" if conf.startswith("low") else "none")]
    for u in scan["usb"]:
        n += 1
        L += ["", f"### USB {u['id']}", f"- Detected chip: {u['name'] or 'unnamed'}",
              f"- Evidence: `lsusb` Bus {u['bus']} Device {u['dev']}", "- Confidence: high (USB descriptor)", "- Open questions: none"]
    for sdev in scan["serial"]:
        n += 1
        L += ["", f"### Serial {sdev['dev']}", f"- Detected chip: {sdev['id']} {sdev['name']}".rstrip(),
              f"- Evidence: `udevadm info -q property -n {sdev['dev']}` (serial {sdev['serial'] or 'none'})",
              "- Confidence: high (USB ids)", "- Open questions: which board is on this port? check with the ESP session before any flashing"]
    for sp in scan["spi"]:
        n += 1
        L += ["", f"### SPI {sp}", "- Detected chip: bus only (no chip select probe)", f"- Evidence: `ls {sp}`",
              "- Confidence: n/a", "- Open questions: which device uses this chip select?"]
    for w in scan["onewire"]:
        n += 1
        L += ["", f"### 1-Wire {w}", "- Detected chip: 1-Wire slave (family = prefix of the id)", "- Evidence: /sys/bus/w1/devices",
              "- Confidence: medium", "- Open questions: none"]
    if scan["video"]:
        L += ["", "### Video devices", "- " + "; ".join(v["dev"] + " " + v["name"] for v in scan["video"])]
    if not n:
        L += ["", "No devices detected."]
    L.append("")
    L.append("_Address hints are guesses from a table; confirm with a chip-ID read (`sense scan --probe`) or the datasheet._")
    return "\n".join(L) + "\n"


def cmd_scan(a, baseline_mode=False):
    buses = a.bus or DEFAULT_BUSES
    bad = [b for b in buses if b not in ALLOWED_BUSES]
    if bad:
        sys.exit(f"refusing bus {bad}: only {sorted(ALLOWED_BUSES)} is allowed (the others carry the power chip, HDMI, camera).")
    scan = scan_all(buses, getattr(a, "probe", False))
    prev, base = load(LATEST_JSON), load(BASELINE)
    text = render(scan, prev, base if not baseline_mode else None)
    if getattr(a, "json", False):
        print(json.dumps(scan, indent=1))
    else:
        print(text, end="")
    if baseline_mode:
        os.makedirs(BASE, exist_ok=True)
        with open(BASELINE, "w") as f:
            json.dump(scan, f, indent=1)
        print(f"baseline saved: {len(keys(scan))} built-in item(s)", file=sys.stderr)
    if not getattr(a, "no_save", False):
        os.makedirs(FIND, exist_ok=True)
        stamp = now().strftime("%Y%m%d-%H%M%S")
        with open(os.path.join(FIND, f"{stamp}-scan.md"), "w") as f:
            f.write(text)
        for name, data in (("latest.md", text), ("latest.json", json.dumps(scan, indent=1))):
            tmp = os.path.join(FIND, name + ".tmp")
            with open(tmp, "w") as f:
                f.write(data)
            os.replace(tmp, os.path.join(FIND, name))


def reqs_rw(mutate=None):
    os.makedirs(BASE, exist_ok=True)
    with open(REQ, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        items = [json.loads(l) for l in f if l.strip()]
        if mutate:
            mutate(items)
            f.seek(0)
            f.truncate()
            for it in items:
                f.write(json.dumps(it) + "\n")
        return items


def find(items, rid):
    for it in items:
        if it["id"] == rid:
            return it
    sys.exit(f"no request #{rid}")


def fmt_req(it, full=False):
    s = f"#{it['id']} [{it['status']}] {it['ts'][:16]} {it['from']}: {it['text']}"
    for r in it["replies"]:
        mark = " (new)" if not r["seen"] else ""
        s += f"\n    -> {r['ts'][:16]} {r['by']}{mark}: {r['text']}" if full or not r["seen"] else ""
    return s


def main():
    ap = argparse.ArgumentParser(prog="sense", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "baseline"):
        p = sub.add_parser(name)
        p.add_argument("--bus", type=int, action="append")
        p.add_argument("--probe", action="store_true")
        p.add_argument("--no-save", action="store_true")
        p.add_argument("--json", action="store_true")
    sub.add_parser("last")
    p = sub.add_parser("req"); p.add_argument("text", nargs="+"); p.add_argument("--from", dest="by", default="gil", help="who is asking (default: gil, the person at the deck)")
    p = sub.add_parser("reqs"); p.add_argument("--all", action="store_true"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("show"); p.add_argument("id", type=int)
    p = sub.add_parser("reply"); p.add_argument("id", type=int); p.add_argument("text", nargs="+")
    p.add_argument("--by", default="claude"); p.add_argument("--close", action="store_true")
    p = sub.add_parser("close"); p.add_argument("id", type=int)
    sub.add_parser("hint")
    a = ap.parse_args()

    if a.cmd == "scan":
        cmd_scan(a)
    elif a.cmd == "baseline":
        cmd_scan(a, baseline_mode=True)
    elif a.cmd == "last":
        try:
            print(open(os.path.join(FIND, "latest.md")).read(), end="")
        except OSError:
            sys.exit("no scan yet: run `sense scan`")
    elif a.cmd == "req":
        def add(items):
            items.append({"id": max([i["id"] for i in items] + [0]) + 1, "ts": now().isoformat(timespec="seconds"),
                          "from": a.by, "text": " ".join(a.text), "status": "open", "replies": []})
        print(fmt_req(reqs_rw(add)[-1]))
    elif a.cmd == "reqs":
        items = [i for i in reqs_rw() if a.all or i["status"] == "open" or any(not r["seen"] for r in i["replies"])]
        print(json.dumps(items, indent=1) if a.json else ("\n".join(fmt_req(i) for i in items) or "no open requests"))
    elif a.cmd == "show":
        def seen(items):
            if sys.stdout.isatty():
                for r in find(items, a.id)["replies"]:
                    r["seen"] = True
        items = reqs_rw()
        print(fmt_req(find(items, a.id), full=True))
        reqs_rw(seen)
    elif a.cmd == "reply":
        def rep(items):
            it = find(items, a.id)
            it["replies"].append({"ts": now().isoformat(timespec="seconds"), "by": a.by, "text": " ".join(a.text), "seen": False})
            if a.close:
                it["status"] = "closed"
        reqs_rw(rep)
        print(f"replied to #{a.id}" + (" and closed it" if a.close else ""))
    elif a.cmd == "close":
        reqs_rw(lambda items: find(items, a.id).update(status="closed"))
        print(f"closed #{a.id}")
    elif a.cmd == "hint":
        items = reqs_rw() if os.path.exists(REQ) else []
        o, n = sum(i["status"] == "open" for i in items), sum(not r["seen"] for i in items for r in i["replies"])
        if o or n:
            print(f"sense: {o} open request(s), {n} new reply(ies)  (sense reqs)")


if __name__ == "__main__":
    main()
