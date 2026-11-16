#!/usr/bin/env python3
"""Regenerate everything from hardware.yaml: validation report, netlist (JSON + CSV), WireViz YAML, SkiDL script, SVG + PDF diagrams.
Needs: Python 3, PyYAML, matplotlib.   Usage: python3 build_all.py   (writes into ./out)"""
import os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hwlib, gen_formats, render_diagrams

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)
H, parts, nets, nc = hwlib.load()
err, warn, info, checks = hwlib.validate(H, parts, nets, nc)
lines = [f"VALIDATION REPORT  rev {H['meta']['revision']}", f"parts {len(parts)}  nets {len(nets)}  nodes {sum(len(n['nodes']) for n in nets)}", ""]
lines += [f"ERROR   {e}" for e in err] or ["errors: none"]
lines += [""] + [f"WARNING {w}" for w in warn] + [""] + [f"INFO    {i}" for i in info] + [""]
bad = [c for c in checks if not c[1]]
lines.append(f"cross-checks against the written pin tables: {len(checks)} run, {len(bad)} failed")
lines += [f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{d}]") for name, ok, d in checks]
open(os.path.join(OUT, "validation.txt"), "w").write("\n".join(lines) + "\n")
hwlib.write_netlist(H, parts, nets, OUT)
wv = gen_formats.write_wireviz(parts, nets, OUT)
gen_formats.write_skidl(OUT)
pages = render_diagrams.build(OUT)
print("validation:", len(err), "errors,", len(warn), "warnings,", len(checks), "cross-checks,", len(bad), "failed")
print("wireviz:", wv)
print("diagram pages:", pages)
for f in sorted(os.listdir(OUT)):
    if f.endswith((".json", ".csv", ".yaml", ".pdf", ".svg", ".txt", ".py")):
        print(f"  {hashlib.sha256(open(os.path.join(OUT, f), 'rb').read()).hexdigest()[:12]}  {os.path.getsize(os.path.join(OUT, f)):>8}  {f}")
sys.exit(1 if err or bad else 0)
