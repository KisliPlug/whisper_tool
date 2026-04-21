"""Screen capture controller — serial state machine for video + screenshot.

Hotkey handlers post events (`post_video_toggle`, `post_screenshot`) onto
the controller's queue; a dedicated worker thread drains the queue and
owns all Tk windows (selection overlay, border indicator) so Tk only
touches one thread at a time.
"""

import queue
import threading
from datetime import datetime
from pathlib import Path

import pyperclip
from PIL import Image

from . import sounds
from .selector import capture_primary_screen, select_region
from .video import VideoRecorder, BorderIndicator
from .exporter import export_all


class ScreenController:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.output_dir = Path(config.output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._events: queue.Queue = queue.Queue()
        self._worker = None
        self._state = "idle"  # or "recording_video"
        self._recorder = None
        self._indicator = None
        self._rec_region = None

    # ── Public API ────────────────────────────────────────────────────
    def start(self):
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self):
        self._events.put("__shutdown__")
        if self._worker is not None:
            self._worker.join(timeout=5.0)

    def post_video_toggle(self):
        self._events.put("video")

    def post_screenshot(self):
        self._events.put("screenshot")

    # ── Worker loop ───────────────────────────────────────────────────
    def _run(self):
        while True:
            try:
                ev = self._events.get(timeout=0.2)
            except queue.Empty:
                continue
            if ev == "__shutdown__":
                if self._state == "recording_video":
                    try:
                        self._finish_video()
                    except Exception as e:
                        self.log.error("VIDEO_ERROR  | shutdown cleanup failed: %s", e)
                return
            if ev == "video":
                try:
                    self._handle_video()
                except Exception as e:
                    self.log.error("VIDEO_ERROR  | %s", e)
                    self._state = "idle"
            elif ev == "screenshot":
                try:
                    self._handle_screenshot()
                except Exception as e:
                    self.log.error("SHOT_ERROR   | %s", e)

    # ── Video ─────────────────────────────────────────────────────────
    def _handle_video(self):
        if self._state == "idle":
            region = select_region()
            if region is None:
                self.log.info("VIDEO_CANCEL | selection aborted")
                return
            self._rec_region = region
            self._recorder = VideoRecorder(region, fps=self.config.video_fps)
            self._recorder.start()
            self._indicator = BorderIndicator(region)
            self._indicator.start()
            sounds.video_start()
            self._state = "recording_video"
            self.log.info(
                "VIDEO_START  | region=%dx%d at (%d,%d)",
                region[2], region[3], region[0], region[1],
            )
        elif self._state == "recording_video":
            self._finish_video()

    def _finish_video(self):
        sounds.video_stop()
        if self._indicator is not None:
            self._indicator.stop()
            self._indicator = None
        frames, fps = self._recorder.stop()
        self._recorder = None
        self._state = "idle"
        if not frames:
            self.log.warning("VIDEO_EMPTY  | no frames captured")
            self._rec_region = None
            return
        duration = len(frames) / fps
        out_dir = self.output_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            export_all(frames, fps, out_dir)
        except Exception as e:
            self.log.error("VIDEO_ERROR  | export failed: %s", e)
        clip_note = ""
        try:
            pyperclip.copy(str(out_dir))
        except Exception as e:
            clip_note = f" (clipboard failed: {e})"
        self.log.info(
            "VIDEO_SAVED  | frames=%d duration=%.2fs region=%dx%d path=%s%s",
            len(frames), duration,
            self._rec_region[2], self._rec_region[3], out_dir, clip_note,
        )
        self._rec_region = None

    # ── Screenshot ────────────────────────────────────────────────────
    def _handle_screenshot(self):
        if self._state != "idle":
            self.log.info("SHOT_IGNORED | video recording in progress")
            return
        try:
            frozen, mon = capture_primary_screen()
        except Exception as e:
            self.log.error("SHOT_ERROR   | capture failed: %s", e)
            return
        region = select_region(frozen_image=frozen, monitor=mon)
        if region is None:
            self.log.info("SHOT_CANCEL  | selection aborted")
            return
        x, y, w, h = region
        lx = max(0, x - mon["left"])
        ly = max(0, y - mon["top"])
        rx = min(frozen.shape[1], lx + w)
        ry = min(frozen.shape[0], ly + h)
        cropped = frozen[ly:ry, lx:rx]
        if cropped.size == 0:
            self.log.error("SHOT_ERROR   | empty region after crop")
            return
        out_dir = self.output_dir / ("screenshot_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        out_dir.mkdir(parents=True, exist_ok=True)
        png_path = out_dir / "screenshot.png"
        try:
            Image.fromarray(cropped).save(png_path)
            sounds.shutter()
        except Exception as e:
            self.log.error("SHOT_ERROR   | save failed: %s", e)
            return
        clip_note = ""
        try:
            pyperclip.copy(str(png_path))
        except Exception as e:
            clip_note = f" (clipboard failed: {e})"
        self.log.info(
            "SHOT_SAVED   | region=%dx%d file=%s%s",
            region[2], region[3], png_path, clip_note,
        )
