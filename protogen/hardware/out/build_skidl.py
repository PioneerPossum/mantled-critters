#!/usr/bin/env python3
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
