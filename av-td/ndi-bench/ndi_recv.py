#!/usr/bin/env python3
"""NDI test receiver: connect to one source by name, decode, count frames.
usage: ndi_recv.py SOURCE_SUBSTRING SECONDS WARMUP SENDER_IP"""
import sys, time
import NDIlib as ndi

want, SECS, WARM, ip = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
assert ndi.initialize()
fc = ndi.FindCreate(); fc.extra_ips = ip; fc.show_local_sources = False
f = ndi.find_create_v2(fc)
src = None; t = time.time()
if ":" in want:
    src = ndi.Source(); src.ndi_name = "X (" + want + ")"; src.url_address = want
while src is None and time.time() - t < 20 and ':' not in want:
    ndi.find_wait_for_sources(f, 1000)
    for s in ndi.find_get_current_sources(f):
        if s.ndi_name.endswith("(" + want + ")"):
            src = s
if src is None:
    print(f"{want}: SOURCE NOT FOUND", flush=True); sys.exit(1)
rc = ndi.RecvCreateV3()
rc.color_format = ndi.RECV_COLOR_FORMAT_FASTEST
rc.bandwidth = ndi.RECV_BANDWIDTH_HIGHEST
r = ndi.recv_create_v3(rc)
ndi.recv_connect(r, src)
ndi.find_destroy(f)
start = time.time(); n = 0; counting = False; tstart = None; res = None; p0 = None
while time.time() - start < SECS + WARM:
    ty, v, a, m = ndi.recv_capture_v2(r, 1000)
    if ty == ndi.FRAME_TYPE_VIDEO:
        if res is None: res = f"{v.xres}x{v.yres}"
        if not counting and time.time() - start >= WARM:
            counting = True; tstart = time.time(); n = 0
            p0 = ndi.recv_get_performance(r)
        n += 1
        ndi.recv_free_video_v2(r, v)
    elif ty == ndi.FRAME_TYPE_AUDIO:
        ndi.recv_free_audio_v2(r, a)
    elif ty == ndi.FRAME_TYPE_METADATA:
        ndi.recv_free_metadata(r, m)
el = time.time() - (tstart or time.time())
try:
    tot, drop = ndi.recv_get_performance(r)
    t0, d0 = p0
    dropped = drop.video_frames - d0.video_frames
except Exception as e:
    dropped = f"n/a ({e})"
print(f"RESULT {want} {res} frames={n} secs={el:.1f} fps={n/max(el,1e-9):.2f} dropped={dropped}", flush=True)
ndi.recv_destroy(r); ndi.destroy()
