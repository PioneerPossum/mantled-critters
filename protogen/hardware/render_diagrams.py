"""Render the wiring diagrams (SVG per page + one multi-page PDF) from hardware.yaml with matplotlib only.
Run via build_all.py. Pages: 1 system overview, 2 visor matrix+IO, 3 visor I2C/audio, 4 wrist module, 5 tables."""
import os, textwrap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle, FancyBboxPatch
import hwlib

plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "path", "pdf.fonttype": 42})

C_12V, C_5V, C_3V3, C_GND = "#D9570B", "#C8102E", "#C99700", "#1A1A1A"
C_SIG, C_I2C, C_I2S, C_RADIO = "#1D5FD0", "#0F8B8D", "#8E44AD", "#CC00AA"
BG, BOX, EDGE, TXT, DIM = "#FAF8F2", "#FFFFFF", "#55524A", "#1b1b19", "#8a867a"
ROW = 0.205

H, parts, nets, nc = hwlib.load()
netof = {(r, p): n for n in nets for (r, p) in n["nodes"]}


def ncolor(n):
    name, cls = n["name"], n["cls"]
    if cls == "gnd": return C_GND
    if name.startswith("12V"): return C_12V
    if name.startswith("5V") or name.startswith("USBC"): return C_5V
    if name.startswith("3V3"): return C_3V3
    return {"i2c": C_I2C, "i2s": C_I2S}.get(cls, C_SIG)


def page(W, Hh):
    fig = plt.figure(figsize=(W, Hh), facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(Hh, 0); ax.axis("off")
    return fig, ax


def T(ax, x, y, s, size=8, color=TXT, ha="left", va="center", weight="normal", style="normal", family=None):
    kw = {"family": family} if family else {}
    ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, fontweight=weight, fontstyle=style, **kw)


def line(ax, pts, color, lw=1.4, dashed=False, z=2):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color, lw=lw, ls=(0, (4, 3)) if dashed else "-", solid_capstyle="round", zorder=z)


def arrow(ax, p1, p2, color, lw=1.8, dashed=False, label=None, lab_off=(0, -0.14), size=7):
    ax.annotate("", xy=p2, xytext=p1, arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, ls=(0, (4, 3)) if dashed else "-", shrinkA=0, shrinkB=0), zorder=3)
    if label:
        T(ax, (p1[0] + p2[0]) / 2 + lab_off[0], (p1[1] + p2[1]) / 2 + lab_off[1], label, size=size, color=color, ha="center")


def blk(ax, x, y, w, h, lines, dashed=False, edge=EDGE, fill=BOX, size=8.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.08", fc=fill, ec=edge, lw=1.5, ls=(0, (4, 3)) if dashed else "-", zorder=1))
    n = len(lines)
    for i, s in enumerate(lines):
        T(ax, x + w / 2, y + h / 2 + (i - (n - 1) / 2) * 0.2, s, size=size if i == 0 else size - 1, ha="center", weight="bold" if i == 0 else "normal", color=TXT if i == 0 else "#3d3b35")


def title(ax, W, name, sub):
    T(ax, 0.5, 0.45, name, size=19, weight="bold")
    T(ax, 0.5, 0.88, sub, size=9, color="#4d4c48")
    T(ax, W - 0.5, 0.45, f"Protogen visor wiring   rev {H['meta']['revision']}", size=8.5, color=DIM, ha="right")


def legend(ax, y, W):
    x = 0.5
    for name, col in (("12 V", C_12V), ("5 V", C_5V), ("3.3 V", C_3V3), ("GND", C_GND), ("signal", C_SIG), ("I2C", C_I2C), ("I2S audio", C_I2S), ("ESP-NOW radio", C_RADIO)):
        line(ax, [(x, y), (x + 0.45, y)], col, lw=2.6)
        T(ax, x + 0.55, y, name, size=7.5)
        x += 1.5
    line(ax, [(x + 0.2, y), (x + 0.65, y)], EDGE, lw=1.6, dashed=True)
    T(ax, x + 0.75, y, "dashed = planned / temporary / optional   solid = built and tested", size=7.5)


def status_of(pid):
    if parts[pid].get("st"):
        return parts[pid]["st"]
    sig = {n["status"] for n in nets if n["cls"] not in ("power", "gnd") and any(r == pid for (r, _) in n["nodes"])}
    st = sig or {n["status"] for n in nets if any(r == pid for (r, _) in n["nodes"])}
    for s_ in ("temporary", "planned", "optional", "built"):
        if s_ in st:
            return s_
    return "planned"


# ---------------------------------------------------------------------------
# generic board page: MCU pin column on the left, device boxes on the right
# ---------------------------------------------------------------------------
def board_page(name, sub, board, part_ids, notes, W=17.0):
    X_MCU, W_MCU = 0.6, 3.3
    X_DEV, W_DEV = 9.4, 6.9
    y = 1.5
    layout, ypos = [], {}
    for pid in part_ids:
        p = parts[pid]
        pins = list(p["pins"].items())
        tl = textwrap.wrap(p["title"], 62)
        sub_l = []
        if p.get("addr"): sub_l.append("I2C " + p["addr"])
        nl = textwrap.wrap(p.get("note", ""), 98) if p.get("note") else []
        hh = 0.10 + 0.18 * len(tl) + 0.15 * len(sub_l) + 0.14 * len(nl) + 0.06
        h = hh + ROW * len(pins) + 0.07
        layout.append((pid, y, h, hh, tl, sub_l, nl, pins))
        for i, (pin, label) in enumerate(pins):
            ypos[(pid, pin)] = y + hh + ROW * (i + 0.5)
        y += h + 0.16
    # MCU rows: one per signal net that reaches an MCU GPIO and a device on this page
    mcu_rows = []
    for n in nets:
        if n["cls"] not in ("sig", "i2c", "i2s"):
            continue
        gp = [pin for (r, pin) in n["nodes"] if r == board and pin.startswith("GPIO")]
        devs = [(r, p) for (r, p) in n["nodes"] if r in part_ids]
        if gp and devs:
            ys = [ypos[d] for d in devs]
            mcu_rows.append((n, gp[0], devs, ys))
    mcu_rows.sort(key=lambda r: min(r[3]))
    bot_ = (max(max(r[3]) for r in mcu_rows) + 1.45) if mcu_rows else 3
    y_notes = max(y, bot_) + 0.35
    Hh = max(y_notes + 0.2 * len(notes) + 1.0, 6)
    fig, ax = page(W, Hh)
    title(ax, W, name, sub)
    # MCU box
    top = min(r[3][0] for r in mcu_rows) - 0.55 if mcu_rows else 1.8
    bot = max(max(r[3]) for r in mcu_rows) + 1.45 if mcu_rows else 3
    ax.add_patch(FancyBboxPatch((X_MCU, top), W_MCU, bot - top, boxstyle="round,pad=0,rounding_size=0.1", fc="#EFEBDD", ec=EDGE, lw=2, zorder=1))
    T(ax, X_MCU + W_MCU / 2, top + 0.18, H["boards"][board]["title"], size=10, weight="bold", ha="center")
    T(ax, X_MCU + W_MCU / 2, top + 0.40, H["boards"][board]["mcu"], size=7.5, color="#4d4c48", ha="center")
    T(ax, X_MCU + W_MCU / 2, bot - 0.95, "power pins", size=7, color=DIM, ha="center", style="italic")
    for i, (pin, col, txt) in enumerate((("3V3", C_3V3, "3V3  powers the modules"), ("5V", C_5V, "5V  from the logic hub"), ("GND", C_GND, "GND  to the star point"))):
        T(ax, X_MCU + 0.15, bot - 0.72 + 0.22 * i, txt, size=7.5, color=col, weight="bold")
    # device boxes
    for (pid, y0, h, hh, tl, sub_l, nl, pins) in layout:
        st = status_of(pid)
        dashed = st != "built"
        ax.add_patch(Rectangle((X_DEV, y0), W_DEV, h, fc=BOX, ec=EDGE, lw=1.4, ls=(0, (4, 3)) if dashed else "-", zorder=1))
        yy = y0 + 0.10
        for s in tl:
            T(ax, X_DEV + 0.1, yy + 0.09, s, size=8.5, weight="bold"); yy += 0.18
        for s in sub_l:
            T(ax, X_DEV + 0.1, yy + 0.07, s, size=7.5, color=C_I2C, weight="bold"); yy += 0.15
        for s in nl:
            T(ax, X_DEV + 0.1, yy + 0.07, s, size=6.5, color="#6a665a", style="italic"); yy += 0.14
        T(ax, X_DEV + W_DEV - 0.1, y0 + 0.14, st, size=7, color=DIM, ha="right", style="italic")
        for i, (pin, label) in enumerate(pins):
            yr = y0 + hh + ROW * (i + 0.5)
            ptxt = label if pin == label else f"{pin}  {label}"
            T(ax, X_DEV + 0.30, yr, ptxt, size=7.5, family="DejaVu Sans Mono")
            n = netof.get((pid, pin))
            if n is None:
                T(ax, X_DEV + W_DEV - 0.1, yr, "not connected", size=6.5, color=DIM, ha="right", style="italic")
            elif n["cls"] in ("power", "gnd"):
                T(ax, X_DEV + W_DEV - 0.1, yr, n["name"], size=7, color=ncolor(n), ha="right", weight="bold")
            elif n["cls"] == "pwr_sig":
                other = [f"{r}.{parts[r]['pins'][p]}" for (r, p) in n["nodes"] if (r, p) != (pid, pin)]
                T(ax, X_DEV + W_DEV - 0.1, yr, "-> " + ", ".join(other), size=7, color=C_SIG, ha="right")
            else:
                T(ax, X_DEV + W_DEV - 0.1, yr, n["name"], size=7, color=ncolor(n), ha="right")
                ax.add_patch(plt.Circle((X_DEV, yr), 0.045, fc=ncolor(n), ec="none", zorder=4))
    # wires
    k = 0
    for (n, gpio, devs, ys) in mcu_rows:
        col = ncolor(n)
        dashed = n["status"] != "built"
        y_m = min(ys)
        x0 = X_MCU + W_MCU
        T(ax, X_MCU + 0.12, y_m, gpio, size=8, weight="bold", family="DejaVu Sans Mono")
        T(ax, x0 - 0.12, y_m, n["name"], size=7, color=col, ha="right")
        ax.add_patch(plt.Circle((x0, y_m), 0.045, fc=col, ec="none", zorder=4))
        if len(devs) == 1:
            line(ax, [(x0, y_m), (X_DEV, y_m)], col, dashed=dashed)
        else:
            xt = X_DEV - 0.30 - 0.17 * k
            k += 1
            line(ax, [(x0, y_m), (xt, y_m)], col, dashed=dashed)
            line(ax, [(xt, min(ys + [y_m])), (xt, max(ys + [y_m]))], col, dashed=dashed)
            for yd in ys:
                line(ax, [(xt, yd), (X_DEV, yd)], col, dashed=dashed)
                ax.add_patch(plt.Circle((xt, yd), 0.035, fc=col, ec="none", zorder=4))
    yb = y_notes
    for s in notes:
        T(ax, 0.5, yb, s, size=8, color="#3d3b35"); yb += 0.2
    legend(ax, Hh - 0.45, W)
    return fig


# ---------------------------------------------------------------------------
def overview_page():
    W, Hh = 17.0, 11.7
    fig, ax = page(W, Hh)
    title(ax, W, "System overview", "Power chain, the two ESP32-S3 boards and the wireless link between them")
    blk(ax, 0.5, 2.0, 1.9, 0.9, ["USB-C PD", "power bank", "carried on the body"], dashed=True)
    blk(ax, 2.9, 2.0, 2.2, 0.9, ["PD decoy trigger", "DIP = 12 V", "plan 3 A / 36 W max"], dashed=True)
    blk(ax, 5.6, 1.2, 2.3, 7.2, ["ELECTOP 6-way", "ATC/ATO fuse block", "outside the head", "negative bus =", "ground star point"], dashed=True, fill="#EFEBDD")
    arrow(ax, (2.4, 2.45), (2.9, 2.45), C_5V, label="USB-C", lab_off=(0, -0.2))
    arrow(ax, (5.1, 2.45), (5.6, 2.45), C_12V, label="12 V", lab_off=(0, -0.2))
    ports = [(1.9, "F1 3 A"), (3.2, "F2 3 A"), (4.5, "F3 2 A"), (5.8, "F4 1 A"), (7.1, "F5 2 A"), (8.0, "F6 spare")]
    for py, lab in ports:
        T(ax, 7.82, py - 0.14, lab, size=7.5, color=C_12V, ha="right", weight="bold")
    col3 = [(1.45, ["Buck A EPICFLAME", "12 V -> 5 V"]), (2.75, ["Buck B EPICFLAME", "12 V -> 5 V"]), (4.05, ["NULLLAB 1 'logic hub'", "3-20 V in, 5 V out"]),
            (5.35, ["Noctua NF-A4x10 PWM", "12 V fan"]), (6.65, ["NULLLAB 2 'servo bus'", "3-20 V in, 5 V out"])]
    for (cy, ls) in col3:
        blk(ax, 9.0, cy, 2.3, 0.9, ls, dashed=True)
    for (py, _), (cy, _) in zip(ports[:5], col3):
        arrow(ax, (7.9, py), (9.0, cy + 0.45), C_12V, lw=2.2, label="18 AWG", lab_off=(0, -0.13), size=6.5)
    blk(ax, 12.2, 1.45, 2.3, 0.9, ["LED panel 1", "HUB75 64x32, 5 V"])
    blk(ax, 12.2, 2.75, 2.3, 0.9, ["LED panel 2", "HUB75 64x32, 5 V"])
    arrow(ax, (11.3, 1.9), (12.2, 1.9), C_5V, label="5 V + 1000 uF", lab_off=(0, -0.16), size=6.5)
    arrow(ax, (11.3, 3.2), (12.2, 3.2), C_5V, label="5 V + 1000 uF", lab_off=(0, -0.16), size=6.5)
    arrow(ax, (13.35, 2.35), (13.35, 2.75), C_SIG, lw=1.6, label="OUT -> IN ribbon", lab_off=(0.95, 0), size=6.5)
    blk(ax, 12.2, 3.95, 4.3, 3.3, ["S3V: visor controller (ESP32-S3 #1)", "HUB75 -> panel 1 IN (13 GPIO), built",
        "I2C: OLED 1.3 in, VL53L1X, LSM6DS3, BME280, PCA9685", "I2S: INMP441 left + right mics, PCM5102A DAC to IEMs",
        "analog mic MAX4466, boop input, face button", "fan PWM / tach, receives wrist commands", "(see pages 2 and 3)"], fill="#EFEBDD", size=9)
    arrow(ax, (11.3, 4.5), (12.2, 4.5), C_5V, label="5 V", lab_off=(0, -0.16), size=6.5)
    arrow(ax, (15.2, 3.95), (15.2, 1.9), C_SIG, lw=2.0)
    line(ax, [(15.2, 1.9), (14.5, 1.9)], C_SIG, lw=2.0)
    T(ax, 15.3, 3.0, "HUB75 ribbon\n(page 2)", size=7, color=C_SIG)
    arrow(ax, (11.3, 5.8), (12.2, 5.6), C_SIG, label="PWM / tach", lab_off=(0, -0.2), size=6.5)
    blk(ax, 12.2, 7.55, 4.3, 1.05, ["PCA9685 + 4x MG90S vent servos", "5 V servo bus, I2C 0x40, planned"], dashed=True)
    arrow(ax, (11.3, 7.1), (12.2, 8.0), C_5V, label="5 V servo bus", lab_off=(-0.25, 0.28), size=6.5)
    blk(ax, 12.2, 9.2, 4.3, 1.7, ["S3W: wrist module (ESP32-S3 board #2)", "4x4 keypad, KY-040 encoder, TM1637, buzzer, TP223",
        "planned: BME280 (remote air), IMU #2, mic", "(see page 4)"], fill="#EFEBDD", size=9)
    line(ax, [(16.5, 6.6), (16.65, 6.6), (16.65, 10.05)], C_RADIO, lw=1.8, dashed=True)
    arrow(ax, (16.65, 10.05), (16.5, 10.05), C_RADIO, lw=1.8, dashed=True)
    arrow(ax, (16.65, 6.6), (16.5, 6.6), C_RADIO, lw=1.8, dashed=True)
    ax.text(16.82, 8.3, "ESP-NOW 2.4 GHz ch 1: commands + status (both ways)", fontsize=7, color=C_RADIO, rotation=90, ha="center", va="center")
    T(ax, 0.5, 9.2, "Ground: every GND returns to the fuse block negative bus (star point); a Wago 221 is a sub-junction for the PD trigger ground.", size=8, color="#3d3b35")
    T(ax, 0.5, 9.45, "Panels' 5 V is TEMPORARY today (a Pi bonnet in the right ear pod); the bucks, fuse block and hubs are ordered/planned.", size=8, color="#3d3b35")
    T(ax, 0.5, 9.7, "3.3 V rail = the S3's own 3V3 pin (AMS1117), feeding the I2C parts and mics. Never feed 12 V to any 5 V or 3.3 V part; PCA9685 V+ max 6 V.", size=8, color="#3d3b35")
    T(ax, 0.5, 9.95, "Do not feed the S3 5V pin while its USB is plugged into a computer.", size=8, color="#3d3b35")
    legend(ax, Hh - 0.45, W)
    return fig


def tables_page():
    W, Hh = 17.0, 12.0
    fig, ax = page(W, Hh)
    title(ax, W, "GPIO allocation and power table", "Generated from the netlist. S = status (built / planned / temporary / optional)")
    mono = "DejaVu Sans Mono"
    reserved = {int(k): v for k, v in H.get("reserved", {}).items()}
    x = 0.5
    for board in ("S3V", "S3W"):
        yy = 1.5
        T(ax, x, yy, H["boards"][board]["title"] + "   (" + H["boards"][board]["base"] + ")", size=9.5, weight="bold"); yy += 0.34
        T(ax, x, yy, f"{'GPIO':<7}{'net':<12}{'to':<26}{'S':<10}", size=7.5, family=mono, color=DIM, weight="bold"); yy += 0.22
        for pin in parts[board]["pins"]:
            if not pin.startswith("GPIO"):
                continue
            g = int(pin[4:])
            n = netof.get((board, pin))
            if n:
                others = [f"{r}.{parts[r]['pins'][p]}" for (r, p) in n["nodes"] if r != board][:2]
                T(ax, x, yy, f"{pin:<7}{n['name']:<12}{', '.join(others)[:26]:<26}{n['status']:<10}", size=7.2, family=mono, color=ncolor(n))
            else:
                T(ax, x, yy, f"{pin:<7}{'-- free --' if g not in H.get('caution', {}) else '-- free (RGB LED) --' if g == 48 else '-- free --':<12}", size=7.2, family=mono, color=DIM)
            yy += 0.19
        yy += 0.1
        T(ax, x, yy, "reserved, not used as I/O:  " + ", ".join(f"GPIO{k} ({v.split('(')[0].strip()})" for k, v in sorted(reserved.items())), size=6.8, color=DIM); yy += 0.2
        T(ax, x, yy, "(flash/PSRAM pins 26-37 are not broken out on an N16R8)", size=6.8, color=DIM)
        x += 8.4
    yy = 8.1
    T(ax, 0.5, yy, "Power: fuse and wire table (visor)", size=9.5, weight="bold"); yy += 0.34
    rows = [("F1", "3 A", "Buck A EPICFLAME 12 V -> 5 V, 1000 uF 16 V", "panel 1", "18 AWG"), ("F2", "3 A", "Buck B EPICFLAME 12 V -> 5 V, 1000 uF 16 V", "panel 2", "18 AWG"),
            ("F3", "2 A", "NULLLAB 1 logic hub, 5 V", "S3 5V pin", "18 AWG"), ("F4", "1 A", "Noctua NF-A4x10 fan, direct 12 V", "fan", "18 AWG"),
            ("F5", "2 A", "NULLLAB 2 servo bus, 5 V (+470-1000 uF)", "PCA9685 V+, 4 servos", "18 AWG"), ("F6", "-", "spare", "-", "-")]
    T(ax, 0.5, yy, f"{'fuse':<6}{'rating':<8}{'feeds':<48}{'powers':<24}{'wire'}", size=7.5, family=mono, color=DIM, weight="bold"); yy += 0.22
    for r in rows:
        T(ax, 0.5, yy, f"{r[0]:<6}{r[1]:<8}{r[2]:<48}{r[3]:<24}{r[4]}", size=7.5, family=mono, color=C_12V if r[0] != "F6" else DIM); yy += 0.2
    yy += 0.15
    T(ax, 0.5, yy, "Signals use 22 AWG silicone wire. PD trigger: 12 V unloaded must be checked with a meter before connecting anything.", size=8, color="#3d3b35"); yy += 0.3
    T(ax, 0.5, yy, "I2C addresses  visor bus: 0x29 VL53L1X, 0x3C OLED 1.3 in, 0x40 PCA9685, 0x6B LSM6DS3, 0x76 BME280 inside   |   wrist bus: 0x3C OLED (temporary), 0x6A IMU #2, 0x77 BME280 outside", size=8, color="#3d3b35"); yy += 0.3
    T(ax, 0.5, yy, "Validation: 0 errors; HUB75 ribbon and wrist pins cross-checked against visor-pin-table.md and ui-board-pin-table.md (see validation.txt).", size=8, color="#3d3b35")
    legend(ax, Hh - 0.45, W)
    return fig


def build(outdir):
    os.makedirs(outdir, exist_ok=True)
    visor_a = ["PANEL1_IN", "FAN", "BOOP", "FACEBTN", "MIC1"]
    visor_b = ["OLED13", "VL53", "IMU1", "BME_IN", "PCA", "SERVO1", "SERVO2", "SERVO3", "SERVO4", "INMP_L", "INMP_R", "DAC"]
    wrist = ["KEYPAD", "ENC", "BUZZ", "TM", "TP223", "OLED_W", "BME_OUT", "IMU2", "MIC_W"]
    figs = [
        ("01_overview", overview_page()),
        ("02_visor_matrix_io", board_page("Visor controller: LED matrix, fan, inputs", "S3V (ESP32-S3 #1). HUB75 ribbon is built and lights both panels; the rest is planned or optional.", "S3V", visor_a,
            ["HUB75 pin 8 (E) stays open: 1/16 scan, 64x32 panels. Ribbon pin 1 = red stripe. Panel 1 OUT -> panel 2 IN is a straight 16-pin ribbon.",
             "GPIO3 (HUB75 C) is a strapping pin but is the DMA library's default and works. Fan tach is open collector: the firmware uses the internal pull-up."])),
        ("03_visor_i2c_audio", board_page("Visor controller: I2C sensors, microphones, DAC", "I2C on GPIO11 (SDA) / GPIO12 (SCL), 3.3 V. INMP441 pair and the DAC share the I2S clock pins.", "S3V", visor_b,
            ["Several breakouts carry their own I2C pull-ups: six devices in parallel can make the bus too stiff. Check, and remove pull-ups if needed.",
             "Hearing assist: the DAC output to in-ear monitors needs an attenuator and a software limiter (hard volume ceiling, soft start, fast clamp). Not built yet."])),
        ("04_wrist_module", board_page("Wrist module (control surface)", "S3W (ESP32-S3 board #2, green screw-terminal base). Keypad, encoder, TM1637, buzzer and TP223 are built and tested.", "S3W", wrist,
            ["GPIO18 is not broken out on this base. GPIO19/20 are the USB data pins: never wire them. GPIO48 drives an onboard RGB LED: not used as a scanned output.",
             "The OLED here is temporary: it moves to the visor. BME280 (remote air sample) and IMU #2 are planned on this I2C bus."])),
        ("05_tables", tables_page()),
    ]
    for name, fig in figs:
        fig.savefig(os.path.join(outdir, f"visor_wiring_{name}.svg"), facecolor=BG)
    with PdfPages(os.path.join(outdir, "visor_wiring.pdf")) as pdf:
        for name, fig in figs:
            pdf.savefig(fig, facecolor=BG)
        d = pdf.infodict()
        d["Title"] = "Protogen visor wiring"
        d["Subject"] = "Generated from hardware.yaml"
    for _, fig in figs:
        plt.close(fig)
    return [n for n, _ in figs]
