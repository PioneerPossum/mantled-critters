#!/usr/bin/env python3
"""NDI test sender: moving pattern, UYVY, clocked at target fps.
usage: ndi_send.py NAME W H FPS SECONDS"""
import sys, time
import numpy as np
import NDIlib as ndi

name, W, H, FPS, SECS = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5])
K = 60  # distinct frames in the loop
rng = np.random.default_rng(abs(hash(name)) % 2**32)
tex = rng.integers(0, 48, size=(H, W), dtype=np.uint8)  # moderate texture so SpeedHQ has real work
xs = np.arange(W)[None, :]
ys = np.arange(H)[:, None]
frames = []
for k in range(K):
    yv = ((xs * 200 // W + ys * 55 // H + k * 8) % 200 + 16).astype(np.uint8)
    yv = (yv + np.roll(tex, k * 13, axis=1) // 2).clip(16, 235).astype(np.uint8)
    # moving box
    bx = (k * W // K) % (W - W // 6); by = H // 3
    yv[by:by + H // 4, bx:bx + W // 6] = 220
    u = ((xs[:, ::2] * 224 // W + k * 4) % 224 + 16).astype(np.uint8).repeat(H, 0)
    v = ((ys * 224 // H + k * 4) % 224 + 16).astype(np.uint8).repeat(W // 2, 1)
    f = np.empty((H, W * 2), dtype=np.uint8)
    f[:, 0::4] = u; f[:, 1::4] = yv[:, 0::2]; f[:, 2::4] = v; f[:, 3::4] = yv[:, 1::2]
    frames.append(np.ascontiguousarray(f.reshape(H, W, 2)))

assert ndi.initialize()
sc = ndi.SendCreate(); sc.ndi_name = name; sc.clock_video = True
s = ndi.send_create(sc)
vf = ndi.VideoFrameV2()
vf.FourCC = ndi.FOURCC_VIDEO_TYPE_UYVY
vf.frame_rate_N = FPS * 1000; vf.frame_rate_D = 1000
t0 = time.time(); n = 0
while time.time() - t0 < SECS:
    vf.data = frames[n % K]
    ndi.send_send_video_v2(s, vf)
    n += 1
print(f"{name}: sent {n} frames in {time.time()-t0:.1f}s = {n/(time.time()-t0):.1f} fps", flush=True)
ndi.send_destroy(s); ndi.destroy()
