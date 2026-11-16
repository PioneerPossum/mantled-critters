"""Generate WireViz YAML and a SkiDL script from the same data as the netlist.
NOT RUN on the deck (WireViz and SkiDL are not installed there). The t14-runner session is asked to run them and report any errors."""
import html
import os
import yaml


def _esc(text):
    """WireViz puts connector text into graphviz HTML-like labels without escaping, so a plain '->' breaks dot (found by t14-runner)."""
    return html.escape(str(text), quote=False)


def _status_gauge(net):
    return "18 AWG" if net["cls"] in ("power", "gnd") else "22 AWG"


def wireviz_for_board(board, parts, nets):
    """One WireViz document for all parts of `board`. Hub of a net: the MCU GPIO pin if there is one, a rail connector for
    multi-node power nets, else a direct device-to-device wire."""
    mcu = board
    in_board = {pid for pid, p in parts.items() if p.get("board") == board}
    conns, order = {}, {}

    def conn(pid):
        if pid not in conns:
            p = parts[pid]
            ids = list(p["pins"].keys())
            order[pid] = {pin: i + 1 for i, pin in enumerate(ids)}
            conns[pid] = {"type": _esc(p["title"]), "pins": list(range(1, len(ids) + 1)), "pinlabels": [_esc(p["pins"][i]) for i in ids]}
            if p["kind"] == "mcu":
                conns[pid]["hide_disconnected_pins"] = True
            if p.get("note"):
                conns[pid]["notes"] = _esc(p["note"])
        return pid

    rails = {}
    edges = []   # (hubconn, hubpin(int), devconn, devpin(int), gauge)
    for n in nets:
        nodes = [(r, p) for (r, p) in n["nodes"] if r in in_board]
        if len(nodes) < 2:
            continue
        gauge = _status_gauge(n)
        mcu_nodes = [nd for nd in nodes if nd[0] == mcu and nd[1].startswith("GPIO")]
        if n["cls"] in ("power", "gnd") and len(nodes) > 2:
            rname = "RAIL_" + n["name"]
            rails[rname] = {"type": _esc(f"rail {n['name']} ({n['status']})"), "pins": [1], "pinlabels": [_esc(n["name"])]}
            for (r, p) in nodes:
                conn(r)
                edges.append((rname, 1, r, order[r][p], gauge))
        elif mcu_nodes:
            hub = mcu_nodes[0]
            conn(hub[0])
            for (r, p) in nodes:
                if (r, p) == hub:
                    continue
                conn(r)
                edges.append((hub[0], order[hub[0]][hub[1]], r, order[r][p], gauge))
        else:
            a, b = nodes[0], nodes[1]
            conn(a[0]); conn(b[0])
            edges.append((a[0], order[a[0]][a[1]], b[0], order[b[0]][b[1]], gauge))
            for extra in nodes[2:]:
                conn(extra[0])
                edges.append((a[0], order[a[0]][a[1]], extra[0], order[extra[0]][extra[1]], gauge))
    for rname in rails:
        conns[rname] = rails[rname]
    # group edges by (hub, device): one cable per group
    groups = {}
    for (h, hp, d, dp, g) in edges:
        groups.setdefault((h, d, g), []).append((hp, dp))
    cables, connections = {}, []
    for (h, d, g), pairs in groups.items():
        cname = f"W_{d}_{h}".replace("RAIL_", "R_")
        cables[cname] = {"wirecount": len(pairs), "gauge": g}
        connections.append([{h: [hp for hp, _ in pairs]}, {cname: list(range(1, len(pairs) + 1))}, {d: [dp for _, dp in pairs]}])
    doc = {"connectors": conns, "cables": cables, "connections": connections}
    return doc


def write_wireviz(parts, nets, outdir):
    os.makedirs(outdir, exist_ok=True)
    out = []
    for board, fname in (("S3V", "visor.wireviz.yaml"), ("S3W", "wrist.wireviz.yaml")):
        doc = wireviz_for_board(board, parts, nets)
        header = (f"# WireViz source for {board}, generated from hardware.yaml by gen_formats.py (NOT run on the deck).\n"
                  f"# Run:  wireviz {fname}   (writes .svg .png .html .bom.tsv next to it)\n"
                  f"# Pin numbers are positions in each connector's pin list; pinlabels give the real names.\n")
        with open(os.path.join(outdir, fname), "w") as f:
            f.write(header)
            yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=None, width=140)
        out.append((fname, len(doc["connectors"]), len(doc["cables"]), len(doc["connections"])))
    return out


SKIDL_TEMPLATE = '''#!/usr/bin/env python3
"""SkiDL netlist builder for the protogen visor, generated from hardware.yaml (via netlist.json).
NOT run on the deck (SkiDL is not installed there). Every part is a generic template with its real pin names, so this builds
CONNECTIVITY ONLY: no footprints, no symbols from the KiCad libraries.
Run:   python3 build_skidl.py            -> writes protogen_visor.net (KiCad netlist) in this folder
       python3 build_skidl.py --erc      -> also runs SkiDL's ERC (expect noise: every pin is declared BIDIR)
"""
import json, os, sys
from skidl import *

HERE = os.path.dirname(os.path.abspath(__file__))
nl = json.load(open(os.path.join(HERE, "netlist.json")))

REFPFX = {"mcu": "U", "module": "M", "connector": "J", "power": "PS", "passive": "C"}
inst, pinmap = {}, {}
for pid, info in nl["parts"].items():
    ids = list(info["pins"].keys())
    pinmap[pid] = {pin: i + 1 for i, pin in enumerate(ids)}
    pins = [Pin(num=i + 1, name=info["pins"][pin], func=Pin.types.BIDIR) for i, pin in enumerate(ids)]
    tpl = Part(name=pid, tool=SKIDL, dest=TEMPLATE, pins=pins, ref_prefix=REFPFX.get(info["kind"], "X"),
               description=info["title"])
    inst[pid] = tpl(ref=pid, tag=pid)           # tag silences SkiDL's "missing/random tag" warnings (t14-runner)

for n in nl["nets"]:
    net = Net(n["name"])
    for node in n["nodes"]:
        ref, pin = node.split(".", 1)
        net += inst[ref][pinmap[ref][pin]]

if "--erc" in sys.argv:
    ERC()
generate_netlist(file_=os.path.join(HERE, "protogen_visor.net"))
print("wrote protogen_visor.net:", len(nl["parts"]), "parts,", len(nl["nets"]), "nets")
'''


def write_skidl(outdir):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "build_skidl.py"), "w") as f:
        f.write(SKIDL_TEMPLATE)
