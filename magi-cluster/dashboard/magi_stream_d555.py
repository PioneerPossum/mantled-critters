#!/usr/bin/env python3
"""Stream the D555's color feed from audire-videre to melchior as RTP/H264
over UDP, so melchior's hailo_apps GStreamerDetectionApp can consume it via
`--input udp://:<port>` (its SOURCE_PIPELINE builds a standard
`udpsrc ! application/x-rtp,encoding-name=H264,payload=96 ! rtph264depay !
h264parse ! avdec_h264` receiver for that scheme — see
~/hailo-apps/hailo_apps/python/core/gstreamer/gstreamer_helper_pipelines.py
on melchior).

No system GStreamer H264 encoder/RTSP server was available on audire-videre
(no sudo to apt-install gstreamer1.0-plugins-ugly/bad), so this uses PyAV
(`pip install --user --break-system-packages av`), which ships its own
libx264 encoder and can mux directly to an RTP/UDP destination — no system
package changes required.

Usage:
    python3 magi_stream_d555.py [--host MELCHIOR_IP] [--port 5000]
                                 [--width 640] [--height 360] [--fps 30]
                                 [--bitrate 800000]

Stop with Ctrl-C.
"""
import argparse
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import av
import cv2
import numpy as np
import pyrealsense2 as rs

DEFAULT_HOST = "192.168.8.175"  # melchior
DEFAULT_PORT = 5000
DEFAULT_MJPEG_PORT = 8092

# Shared latest-frame buffer for the MJPEG preview server, fed by the same
# capture loop that feeds the RTP stream to melchior (one RealSense device,
# one pipeline — no second device handle opened, since the D555 doesn't like
# concurrent pipelines on the same stream).
_latest_jpeg = None
_latest_jpeg_lock = threading.Lock()


class _MJPEGHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass  # keep the main capture-loop log clean

    def do_GET(self):
        if self.path == "/snapshot.jpg":
            with _latest_jpeg_lock:
                buf = _latest_jpeg
            if buf is None:
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(buf)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(buf)
            return

        if self.path == "/mjpeg":
            self.send_response(200)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=frame"
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    with _latest_jpeg_lock:
                        buf = _latest_jpeg
                    if buf is not None:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(buf)}\r\n\r\n".encode())
                        self.wfile.write(buf)
                        self.wfile.write(b"\r\n")
                    time.sleep(1 / 12)  # preview cap ~12fps, independent of capture fps
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self.send_response(404)
        self.end_headers()


def _start_mjpeg_server(port):
    server = ThreadingHTTPServer(("0.0.0.0", port), _MJPEGHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"MJPEG preview server on :{port} (/mjpeg, /snapshot.jpg)")
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=DEFAULT_HOST, help="Receiver IP (melchior)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="Receiver UDP port")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bitrate", type=int, default=800_000, help="H264 target bitrate (bps)")
    ap.add_argument("--mjpeg-port", type=int, default=DEFAULT_MJPEG_PORT,
                     help="HTTP port for the local MJPEG/snapshot preview (0 to disable)")
    args = ap.parse_args()

    if args.mjpeg_port:
        _start_mjpeg_server(args.mjpeg_port)

    print(f"Starting D555 color stream -> rtp://{args.host}:{args.port} "
          f"({args.width}x{args.height}@{args.fps}fps)")

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)
    profile = pipeline.start(config)
    print("RealSense color stream started.")

    output = av.open(f"rtp://{args.host}:{args.port}", mode="w", format="rtp")
    stream = output.add_stream("libx264", rate=args.fps)
    stream.width = args.width
    stream.height = args.height
    stream.pix_fmt = "yuv420p"
    stream.bit_rate = args.bitrate
    stream.options = {
        "preset": "ultrafast",
        "tune": "zerolatency",
        "profile": "baseline",
        # Emit an SPS/PPS-carrying keyframe frequently so a receiver that
        # joins mid-stream (e.g. melchior's pipeline starting after this
        # script) syncs up quickly instead of waiting for a rare I-frame.
        "g": str(args.fps * 2),
        "x264-params": "repeat-headers=1",
    }

    running = True

    def _stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    frame_count = 0
    t0 = time.time()
    try:
        while running:
            frames = pipeline.wait_for_frames(timeout_ms=5000)
            color = frames.get_color_frame()
            if not color:
                continue
            img = np.asanyarray(color.get_data())  # BGR24, HxWx3
            vframe = av.VideoFrame.from_ndarray(img, format="bgr24")
            for packet in stream.encode(vframe):
                output.mux(packet)

            if args.mjpeg_port and frame_count % 2 == 0:  # ~half capture fps is plenty for a preview
                ok, jbuf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    with _latest_jpeg_lock:
                        global _latest_jpeg
                        _latest_jpeg = jbuf.tobytes()

            frame_count += 1
            if frame_count % 150 == 0:
                elapsed = time.time() - t0
                print(f"  {frame_count} frames sent, {frame_count / elapsed:.1f} fps avg")
    except Exception:
        print("Streaming loop error:", file=sys.stderr)
        raise
    finally:
        print("Stopping — flushing encoder and closing streams...")
        try:
            for packet in stream.encode():
                output.mux(packet)
        except Exception:
            pass
        output.close()
        pipeline.stop()
        print("Done.")


if __name__ == "__main__":
    main()
