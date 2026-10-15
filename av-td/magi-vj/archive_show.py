#!/usr/bin/env python3
"""Archive a VJ show for review: action log + music stats (from Redis on repositorium), the recorder's
HEVC segments (hardlinked so the recorder's 256 GB cap can never delete them) and a browser-friendly
H.264 720p60 proxy cut to the show window. Metadata goes to shows/<id>/ (small files, safe for git);
media goes to ~/recordings/shows/<id>/ (large, never committed).
Usage: archive_show.py --id show-1 --title "..." --start "2026-10-05 19:29:01" --end "2026-10-05 19:34:02" [--no-video] [--status needs_review]"""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REC = Path.home() / "recordings" / "ndi"
FF = Path.home() / "magi-video" / "ffmpeg-probe" / "root"
ENV = dict(os.environ, LD_LIBRARY_PATH=f"{FF}/usr/lib/x86_64-linux-gnu")
PAD = 10.0                      # seconds of lead-in / lead-out around the show

def epoch(s): return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()

def segments():
    out = []
    for p in sorted(REC.glob("*.mkv")):
        m = re.match(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.mkv", p.name)
        if m: out.append((datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S").timestamp(), p))
    return out

def seg_len(p):
    r = subprocess.run([f"{FF}/usr/bin/ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, env=ENV)
    try: return float(r.stdout.strip())
    except ValueError: return 600.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True); ap.add_argument("--title", required=True)
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--status", default="needs_review"); ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    t0, t1 = epoch(a.start), epoch(a.end)
    meta = HERE / "shows" / a.id; meta.mkdir(parents=True, exist_ok=True)
    media = Path.home() / "recordings" / "shows" / a.id; media.mkdir(parents=True, exist_ok=True)

    p = subprocess.run(["ssh", "repositorium", "python3", "~/magi/vj/vjctl.py", "export", str(t0 - PAD), str(t1 + PAD)],
                       capture_output=True, text=True, timeout=60)
    ex = json.loads(p.stdout)
    live = [x for x in ex["actions"] if x.get("live")]
    (meta / "actions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in ex["actions"]))
    (meta / "audio.jsonl").write_text("".join(json.dumps(x) + "\n" for x in ex["audio"]))
    fbp = meta / "feedback.jsonl"
    if ex["feedback"] and not fbp.exists():     # keep feedback already given (e.g. Gil's live remark) with the show
        fbp.write_text("".join(json.dumps(dict(x, t_wall=x.get("t"), kind="remark")) + "\n" for x in ex["feedback"]))
    t2 = [x for x in live if x["tier"] == 2 and x["decision"] == "sent"]

    show = {"id": a.id, "title": a.title, "start": a.start, "end": a.end, "t0": t0, "t1": t1, "duration_s": t1 - t0,
            "status": a.status, "flags": ["for-review"] if a.status == "needs_review" else [], "note": a.note,
            "counts": {"actions_total": len(ex["actions"]), "live": len(live), "tier2_sent": len(t2), "audio_rows": len(ex["audio"])},
            "archived_at": time.time(), "video": None}
    if not a.no_video:
        segs = segments(); want = []
        for st, path in segs:
            en = st + seg_len(path)
            if en > t0 - PAD and st < t1 + PAD: want.append((st, path))
        if not want: sys.exit("no recorder segments cover the show window")
        (media / "segments").mkdir(exist_ok=True)
        linked = []
        for st, path in want:
            dst = media / "segments" / path.name
            if not dst.exists(): os.link(path, dst)           # same filesystem: instant, survives the recorder's cleanup
            linked.append(path.name)
        first = want[0][0]; offset = max(0.0, (t0 - PAD) - first); length = (t1 - t0) + 2 * PAD
        lst = media / "concat.txt"; lst.write_text("".join(f"file '{media / 'segments' / p.name}'\n" for _, p in want))
        out = media / "proxy.mp4"
        cmd = [f"{FF}/usr/bin/ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ss", f"{offset:.3f}", "-t", f"{length:.3f}",
               "-vf", "scale=1280:720", "-r", "60", "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "28", "-b:v", "0",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
        if r.returncode != 0: sys.exit("ffmpeg failed: " + r.stderr[-400:])
        show["video"] = {"file": "proxy.mp4", "dir": str(media), "size_mb": round(out.stat().st_size / 1e6, 1),
                         "clip_start": first + offset, "clip_duration": length, "master_segments": linked, "sync_accuracy_s": 1.5}
    (meta / "show.json").write_text(json.dumps(show, indent=1))
    print(json.dumps(show, indent=1))

main()
