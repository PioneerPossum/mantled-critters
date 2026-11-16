"""Load hardware.yaml, validate it, and write the netlist (JSON + CSV).
Pure Python + PyYAML. Everything else (diagrams, WireViz, SkiDL) is generated from the structures built here."""
import csv, json, os, re
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PROTOGEN = os.path.dirname(HERE)


def read_src(name):
    """Pin tables live in ./sources/ inside the tarball, or in the protogen folder on the deck."""
    for d in (os.path.join(HERE, "sources"), PROTOGEN):
        fp = os.path.join(d, name)
        if os.path.exists(fp):
            return open(fp).read()
    raise FileNotFoundError(name)


def load(path=None):
    path = path or os.path.join(HERE, "hardware.yaml")
    H = yaml.safe_load(open(path))
    parts = {}
    for pid, p in H["parts"].items():
        pins = p["pins"]
        if isinstance(pins, dict):
            pins = {str(k): str(v) for k, v in pins.items()}      # pin id -> label
        else:
            pins = {str(x): str(x) for x in pins}
        parts[pid] = dict(p, id=pid, pins=pins)
    nets = []
    for n in H["nets"]:
        nodes = []
        for nd in n["nodes"]:
            ref, pin = str(nd).split(".", 1)
            nodes.append((ref, pin))
        nets.append({"name": n["n"], "cls": n["cls"], "status": n["st"], "nodes": nodes})
    nc = set(tuple(str(x).split(".", 1)) for x in H.get("no_connect", []))
    return H, parts, nets, nc


def validate(H, parts, nets, nc):
    """Returns (errors, warnings, infos, checks) where checks are cross-checks against the written pin tables."""
    err, warn, info, checks = [], [], [], []
    seen = {}
    for n in nets:
        if len(n["nodes"]) < 2:
            warn.append(f"net {n['name']} has only {len(n['nodes'])} node")
        for ref, pin in n["nodes"]:
            if ref not in parts:
                err.append(f"net {n['name']}: unknown part {ref}")
                continue
            if pin not in parts[ref]["pins"]:
                err.append(f"net {n['name']}: part {ref} has no pin '{pin}' (pins: {list(parts[ref]['pins'])})")
                continue
            key = (ref, pin)
            if key in seen and seen[key] != n["name"]:
                err.append(f"pin {ref}.{pin} is in two nets: {seen[key]} and {n['name']}")
            seen[key] = n["name"]
    # GPIO rules per MCU
    reserved = {int(k): v for k, v in H.get("reserved", {}).items()}
    caution = {int(k): v for k, v in H.get("caution", {}).items()}
    for ref, p in parts.items():
        if p["kind"] != "mcu":
            continue
        used = sorted(int(pin[4:]) for (r, pin) in seen if r == ref and pin.startswith("GPIO"))
        for g in used:
            if g in reserved:
                err.append(f"{ref}.GPIO{g} is reserved: {reserved[g]}")
            if g in caution:
                warn.append(f"{ref}.GPIO{g}: {caution[g]}")
        free = [int(x[4:]) for x in p["pins"] if x.startswith("GPIO") and int(x[4:]) not in used and int(x[4:]) not in reserved]
        info.append(f"{ref}: {len(used)} GPIO used {used}; free (broken out, not reserved): {sorted(free)}")
    # I2C address uniqueness per bus
    buses = {}
    for pid, p in parts.items():
        if p.get("addr") and p.get("bus"):
            buses.setdefault(p["bus"], {}).setdefault(p["addr"], []).append(pid)
    for bus, a in buses.items():
        for addr, who in a.items():
            if len(who) > 1:
                err.append(f"I2C bus {bus}: address {addr} used by {who}")
        info.append(f"I2C bus {bus}: " + ", ".join(f"{addr}={'/'.join(who)}" for addr, who in sorted(a.items())))
    # unconnected pins
    for pid, p in parts.items():
        if p["kind"] == "mcu":
            continue
        for pin, label in p["pins"].items():
            if (pid, pin) not in seen and (pid, pin) not in nc:
                warn.append(f"unconnected pin {pid}.{pin} ({label})")
    # cross-check HUB75 against visor-pin-table.md (section 1 table)
    try:
        txt = read_src("visor-pin-table.md")
        rows = re.findall(r"^\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*([\w ]+?)\s*\|", txt, re.M)
        net_of = {(r, p): n["name"] for n in nets for (r, p) in n["nodes"]}
        gpio_of = {}
        for n in nets:
            gp = [pin for (r, pin) in n["nodes"] if r == "S3V" and pin.startswith("GPIO")]
            for (r, pin) in n["nodes"]:
                if r == "PANEL1_IN" and gp:
                    gpio_of[pin] = gp[0]
        for pin, sig, gp in rows[:16]:
            if gp.strip().isdigit():
                want = f"GPIO{gp.strip()}"
                ok = gpio_of.get(pin) == want
                checks.append((f"HUB75 ribbon pin {pin} {sig} -> {want} (visor-pin-table.md)", ok, f"netlist has {gpio_of.get(pin)}"))
            elif gp.strip() == "GND":
                ok = net_of.get(("PANEL1_IN", pin)) == "GND_V"
                checks.append((f"HUB75 ribbon pin {pin} {sig} -> GND (visor-pin-table.md)", ok, f"netlist net {net_of.get(('PANEL1_IN', pin))}"))
    except Exception as e:
        warn.append(f"could not cross-check HUB75 against visor-pin-table.md: {e}")
    # cross-check wrist pins against the AS WIRED line of ui-board-pin-table.md
    try:
        txt = read_src("ui-board-pin-table.md")
        line = re.search(r"Keypad rows R1\.\.R4 = ([\d, ]+) \| cols C1\.\.C4 = ([\d, ]+) \| encoder CLK (\d+), DT (\d+), SW (\d+) \| buzzer (\d+)[^|]*\| TM1637 CLK (\d+), DIO (\d+)", txt)
        if line:
            rows_ = [int(x) for x in line.group(1).split(",")]
            cols_ = [int(x) for x in line.group(2).split(",")]
            expect = {f"KEYPAD.R{i+1}": f"GPIO{g}" for i, g in enumerate(rows_)}
            expect.update({f"KEYPAD.C{i+1}": f"GPIO{g}" for i, g in enumerate(cols_)})
            expect.update({"ENC.CLK": f"GPIO{line.group(3)}", "ENC.DT": f"GPIO{line.group(4)}", "ENC.SW": f"GPIO{line.group(5)}",
                           "BUZZ.S": f"GPIO{line.group(6)}", "TM.CLK": f"GPIO{line.group(7)}", "TM.DIO": f"GPIO{line.group(8)}"})
            mcu_pin = {}
            for n in nets:
                gp = [pin for (r, pin) in n["nodes"] if r == "S3W" and pin.startswith("GPIO")]
                for (r, pin) in n["nodes"]:
                    if gp and r != "S3W":
                        mcu_pin[f"{r}.{pin}"] = gp[0]
            for k, want in expect.items():
                checks.append((f"wrist {k} -> {want} (ui-board-pin-table.md 'AS WIRED')", mcu_pin.get(k) == want, f"netlist has {mcu_pin.get(k)}"))
        else:
            warn.append("AS WIRED line not found in ui-board-pin-table.md (cross-check skipped)")
    except Exception as e:
        warn.append(f"could not cross-check wrist pins: {e}")
    return err, warn, info, checks


def part_status(pid, nets):
    st = {n["status"] for n in nets for (r, _) in n["nodes"] if r == pid}
    for s in ("temporary", "planned", "optional", "built"):
        if s in st:
            return s if not (s == "built" and len(st) > 1) else "mixed"
    return "planned"


def write_netlist(H, parts, nets, outdir):
    os.makedirs(outdir, exist_ok=True)
    doc = {
        "meta": H["meta"],
        "boards": H["boards"],
        "parts": {pid: {"title": p["title"], "kind": p["kind"], "board": p.get("board"), "pins": p["pins"],
                        **({"addr": p["addr"]} if p.get("addr") else {}), **({"note": p["note"]} if p.get("note") else {})}
                  for pid, p in parts.items()},
        "nets": [{"name": n["name"], "class": n["cls"], "status": n["status"], "nodes": [f"{r}.{p}" for r, p in n["nodes"]]} for n in nets],
    }
    with open(os.path.join(outdir, "netlist.json"), "w") as f:
        json.dump(doc, f, indent=1)
    with open(os.path.join(outdir, "netlist.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["net", "class", "status", "part", "pin", "pin_label", "part_title"])
        for n in nets:
            for (r, p) in n["nodes"]:
                w.writerow([n["name"], n["cls"], n["status"], r, p, parts[r]["pins"][p], parts[r]["title"]])
    return doc
