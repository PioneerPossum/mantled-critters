import re, subprocess, sys, tempfile, os
path = sys.argv[1]
src = open(path).read()
m = re.search(r"def c\(s\):\n(?:    .*\n)+", src)
ns = {}; exec(m.group(0), ns); c = ns["c"]
tests = ["plain", "x?ab", 'a"1', "back\\1f", 'Dum$my"pw?\\1', "ends?", "q?9z", "caféa1", "??=ab", "a'b", "tab\there1", "%s%d", "\\x41B", "p@ss w0rd!#"]
bad = 0
d = tempfile.mkdtemp(prefix="esc-")
for t in tests:
    lit = c(t)
    open(d + "/t.c", "w").write('#include <stdio.h>\nstatic const char s[] = %s;\nint main(){for(unsigned i=0;i<sizeof(s)-1;i++)printf("%%02x",(unsigned char)s[i]);return 0;}\n' % lit)
    r = subprocess.run(["gcc", "-Wall", "-Werror", "-o", d + "/t", d + "/t.c"], capture_output=True, text=True)
    if r.returncode:
        print("COMPILE FAIL", repr(t), r.stderr.strip()[:100]); bad += 1; continue
    out = subprocess.run([d + "/t"], capture_output=True, text=True).stdout
    ok = out == t.encode("utf-8").hex()
    if not ok: bad += 1
    print(("PASS " if ok else "FAIL "), repr(t), "->", lit if len(lit) < 60 else lit[:57] + "...")
print("ESC_TEST %s (%d bad) for %s" % ("OK" if not bad else "FAILED", bad, path))
sys.exit(1 if bad else 0)
