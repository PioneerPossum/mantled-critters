---
name: magi-nvenc-recorder
description: audire-videre recorder now encodes hevc_nvenc via a private ffmpeg 7.1/PyAV 15.1 build (driver 550 can't do NVENC API 13); rollback + gotchas
metadata:
  type: project
---

2026-10-04: magi-ndi-recorder switched from CPU x264 to hevc_nvenc (T1000), ~2 GB/h at NVENC_CQ=32 (cq 26 was ~7 GB/h; x264 CRF21 had been ~2.4-2.9 GB/h, not the 8 GB/h earlier notes claimed).

**Why a private build:** driver 550.163.01 (newest in Kali apt) only offers NVENC API 12.2; PyAV wheels 15-18 bundle ffmpeg built for API 13 (driver 570+). No system ffmpeg binary exists. Older wheels lack NVENC or have no py3.14 wheel. Driver upgrade (570+ .run) rejected: kernel 7.1.5 compat unknown, mixes with Debian DKMS, initramfs/LVM boot history ([[audire-videre-boot-lvm]]).

**Layout:** ~/magi-build/{prefix (nv-codec-headers 12.2.72.0, x264, ffmpeg 7.1.1 shared), venv-nvenc (av 15.1.0 from source, ndi-python), build.sh}. Needs LD_LIBRARY_PATH=~/magi-build/prefix/lib (set in the drop-in). Recorder script has VIDEO_CODEC/NVENC_PRESET/NVENC_CQ env (default libx264); backup ndi_recorder.py.bak-x264-20261004.
**Drop-in:** ~/.config/systemd/user/magi-ndi-recorder.service.d/nvenc.conf. Rollback = delete it, daemon-reload, restart.
**Gotchas:** /tmp is a 436 MB volume (scratch venvs filled it); no passwordless sudo on audire-videre; if the driver is ever upgraded to 570+, the stock venv/wheels would work again.
See [[magi-av-mixer-idea]] for the AV rig context.

## 2026-10-04 update: live page backend + Live tab (see also [[magi-av-mixer-idea]])
- **MediaMTX v1.21.1** in ~/magi-live (user unit `magi-live-mediamtx`): WebRTC/WHEP :8889, ICE UDP :8189, RTSP ingest 127.0.0.1:8554 only, path `live`; MoQ/HLS/RTMP/SRT off. Viewers read-only from anywhere, publish from localhost only.
- **Publisher** `~/magi-video/ndi_live.py` (user unit `magi-ndi-live`, venv-nvenc + LD_LIBRARY_PATH): own NDI receiver (HIGHEST, so Legion sends 2x ~108 Mbit/s) -> 1280x720@30 h264_nvenc 3.5M cbr no B-frames + Opus 128k (ffmpeg native opus, strict experimental) in ONE RTSP session. Audio comes from VBAN relayed by ndi_recorder.py to 127.0.0.1:6981 (VBAN_FWD_PORT; the relay is why only the recorder owns UDP 6980). Idle when NDI source absent => WHEP 404 = "No signal". Backup of recorder before relay edit: ndi_recorder.py.bak-prelive-20261004.
- Gotcha fixed: PyAV encoder pts is in codec time base 1/FPS; setting stream.time_base=1/1000 is ignored -> duplicate pts -> RTSP mux EINVAL. Use frame counter pts.
- **Dashboard:** LIVE tab at http://192.168.8.136:8090/static/nerv.html#live (static/live.js, nerv.html/js/css edits, *.bak.20261004_pre-live backups); WHEP URL derived from page hostname. Verified with an aiortc WHEP client: 201, connected, ~24-30 fps video + 50/s audio.
- **Open:** tailnet access (Tailscale stopped on audire-videre; would also need webrtcAdditionalHosts); browser-side playback/A-V offset check on Legion (asked legion-resolume); firewall not inspectable without sudo; BPM tuning next.
