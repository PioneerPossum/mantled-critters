#!/usr/bin/env python3
"""Real Hailo detection pipeline for melchior — publishes detections to the
magi Redis stream via publish_event(), replacing the synthetic test in
magi_publish.py's __main__ block. Based on hailo_apps's stock detection.py
example, with the print-only callback swapped for a Redis publish."""
import os
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import sys
import time

import gi

gi.require_version("Gst", "1.0")
import hailo

sys.path.insert(0, os.path.expanduser("~/magi"))
from magi_publish import publish_event

from hailo_apps.python.pipeline_apps.detection.detection_pipeline import GStreamerDetectionApp
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class
from hailo_apps.python.core.common.hailo_logger import get_logger
import hailo_apps.python.core.gstreamer.gstreamer_app as _gst_app_mod

hailo_logger = get_logger(__name__)

# hailo_apps's DISPLAY_PIPELINE defaults to GST_VIDEO_SINK="autovideosink"
# (hardcoded in core/common/defines.py), which on a headless SSH session with
# no X/Wayland/DRM display resolves through xvimagesink (fails immediately)
# then glimagesink (fails to init GBM/DRM) and stalls the whole pipeline —
# no buffers ever reach app_callback even though the source is flowing fine.
# self.video_sink is read from this module's GST_VIDEO_SINK name at
# GStreamerApp.__init__ time, so monkeypatching it here (before the app is
# constructed) swaps the sink for a real display when one is present, and to
# fakesink when running headless — no edits to the shared hailo_apps files.
if not os.environ.get("DISPLAY"):
    _gst_app_mod.GST_VIDEO_SINK = "fakesink"
    hailo_logger.info("No DISPLAY detected; using fakesink (headless mode).")

DEVICE = "melchior"
PUBLISH_INTERVAL = 1.0  # seconds between publishes per label — at 30fps, publishing
                        # every frame would flood the Redis stream/Postgres events
                        # table with near-duplicate detections of the same object


class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.last_published = {}


def app_callback(element, buffer, user_data):
    if buffer is None:
        hailo_logger.warning("Received None buffer.")
        return

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    now = time.time()
    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()

        last = user_data.last_published.get(label, 0)
        if now - last < PUBLISH_INTERVAL:
            continue
        user_data.last_published[label] = now

        bbox = detection.get_bbox()
        try:
            msg_id = publish_event(
                DEVICE,
                "detection",
                {
                    "class": label,
                    "confidence": round(confidence, 3),
                    "bbox": [bbox.xmin(), bbox.ymin(), bbox.width(), bbox.height()],
                },
            )
            hailo_logger.info(f"published {label} ({confidence:.2f}) as {msg_id}")
        except Exception:
            hailo_logger.exception("Failed to publish detection")

    return


def main():
    hailo_logger.info(f"Starting {DEVICE} real-detection publisher.")
    user_data = user_app_callback_class()
    app = GStreamerDetectionApp(app_callback, user_data)
    app.run()


if __name__ == "__main__":
    main()
