"""Screen capture controller — serial state machine for video + screenshot.

Owns a single, long-lived Tk root that lives for the entire lifetime of
the worker thread. `select_region` and `BorderIndicator` place Toplevel
widgets on this root — NEVER their own Tk() — so all Tk operations
stay on one thread. Cross-thread Tk usage triggers
"Tcl_AsyncDelete: async handler deleted by the wrong thread" panics.

External callers (hotkey handlers) enqueue events via
`post_video_toggle()` / `post_screenshot()`; the worker thread drains
the queue, pumping Tk's event loop between events so border windows
stay responsive.
"""

import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path

import pyperclip
from PIL import Image

from . import sounds
from .selector import capture_primary_screen, select_region
from .video import VideoRecorder, BorderIndicator
from .exporter import export_all
from .electron_annotator import annotate_image


class ScreenController:
    def __init__(self, config, log, notifier=None):
        self.config = config
        self.log = log
        self.notifier = notifier
        self.output_dir = Path(config.output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._events: queue.Queue = queue.Queue()
        self._worker = None
        self._running = False
        self._root: tk.Tk | None = None
        self._state = "idle"  # or "recording_video"
        self._recorder = None
        self._border: BorderIndicator | None = None
        self._rec_region = None

    # ── Public API ────────────────────────────────────────────────────
    def start(self):
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self):
        self._running = False
        self._events.put("__shutdown__")
        if self._worker is not None:
            self._worker.join(timeout=5.0)

    def post_video_toggle(self):
        self.log.info("KEY          | video hotkey pressed")
        self._events.put("video")

    def post_screenshot(self):
        self.log.info("KEY          | screenshot hotkey pressed (plain)")
        self._events.put("shot_plain")

    def post_screenshot_edit(self):
        # The editor flow uses this same method for the *second* press too
        # (to commit). The annotator picks it up from the shared queue.
        self.log.info("KEY          | screenshot-edit hotkey pressed")
        self._events.put("shot_edit")

    # ── Worker loop ───────────────────────────────────────────────────
    def _run(self):
        self._root = tk.Tk()
        self._root.withdraw()
        try:
            while self._running:
                try:
                    ev = self._events.get(timeout=0.1)
                except queue.Empty:
                    self._pump()
                    continue
                if ev == "__shutdown__":
                    if self._state == "recording_video":
                        try:
                            self._finish_video()
                        except Exception:
                            self.log.exception(
                                "VIDEO_ERROR  | shutdown cleanup failed"
                            )
                    break
                try:
                    if ev == "video":
                        self._handle_video()
                    elif ev == "shot_plain":
                        self._handle_screenshot(edit=False)
                    elif ev == "shot_edit":
                        self._handle_screenshot(edit=True)
                except Exception:
                    tag = "VIDEO_ERROR  " if ev == "video" else "SHOT_ERROR   "
                    self.log.exception("%s| handler for ev=%s failed", tag, ev)
                    if ev == "video":
                        self._state = "idle"
                self._pump()
        except Exception:
            # Worker loop itself died — without this the thread would
            # just disappear and the screen hotkeys would silently stop
            # working with no clue in the log.
            self.log.exception("SCREEN_FATAL | worker loop crashed")
            raise
        finally:
            if self._border is not None:
                try:
                    self._border.hide()
                except Exception:
                    pass
                self._border = None
            try:
                if self._root is not None:
                    self._root.destroy()
            except Exception:
                pass
            self._root = None

    def _pump(self):
        """Process any pending Tk events (WM_PAINT, etc.) without blocking."""
        if self._root is None:
            return
        try:
            self._root.update()
        except Exception:
            pass

    def _notify(
        self,
        title: str,
        message: str,
        ok: bool,
        subject_label: str | None = None,
        subject: str | None = None,
        detail: str | None = None,
    ) -> None:
        if self.notifier is None:
            return
        self.notifier.show_event(
            title=title,
            message=message,
            ok=ok,
            subject_label=subject_label,
            subject=subject,
            detail=detail,
        )

    # ── Video ─────────────────────────────────────────────────────────
    def _handle_video(self):
        if self._state == "idle":
            region = select_region(self._root)
            if region is None:
                self.log.info("VIDEO_CANCEL | selection aborted")
                return
            self._rec_region = region
            self._recorder = VideoRecorder(region, fps=self.config.video_fps)
            self._recorder.start()
            self._border = BorderIndicator(self._root, region)
            self._border.show()
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
        if self._border is not None:
            self._border.hide()
            self._border = None
        frames, fps = self._recorder.stop()
        self._recorder = None
        self._state = "idle"
        if not frames:
            self.log.warning("VIDEO_EMPTY  | no frames captured")
            self._notify(
                title="Recording Failed",
                message="No frames were captured for this recording.",
                ok=False,
                subject_label="Action",
                subject="screen-record",
            )
            self._rec_region = None
            return
        duration = len(frames) / fps
        out_dir = self.output_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        export_error = None
        try:
            export_all(frames, fps, out_dir)
        except Exception as e:
            export_error = str(e)
            self.log.error("VIDEO_ERROR  | export failed: %s", e)
        clip_error = None
        clip_note = ""
        try:
            pyperclip.copy(str(out_dir))
        except Exception as e:
            clip_error = str(e)
            clip_note = f" (clipboard failed: {e})"
        self.log.info(
            "VIDEO_END    | frames=%d duration=%.2fs region=%dx%d path=%s%s",
            len(frames), duration,
            self._rec_region[2], self._rec_region[3], out_dir, clip_note,
        )
        if export_error is not None:
            self._notify(
                title="Recording Failed",
                message=f"Failed to export recording: {export_error}",
                ok=False,
                subject_label="Folder",
                subject=str(out_dir),
                detail=(
                    "Folder path copied to clipboard."
                    if clip_error is None
                    else f"Clipboard copy also failed: {clip_error}"
                ),
            )
        elif clip_error is not None:
            self._notify(
                title="Recording Saved, Clipboard Failed",
                message=f"GIF, MP4 and frame grid saved, but folder path was not copied: {clip_error}",
                ok=False,
                subject_label="Folder",
                subject=str(out_dir),
            )
        else:
            self._notify(
                title="Recording Saved",
                message="GIF, MP4 and frame grid saved. Folder path copied to clipboard.",
                ok=True,
                subject_label="Folder",
                subject=str(out_dir),
            )
        self._rec_region = None

    # ── Screenshot ────────────────────────────────────────────────────
    def _handle_screenshot(self, edit: bool):
        """Plain (edit=False) or annotated (edit=True) screenshot flow.

        Both start with the same frozen-snapshot region selection.
        Plain saves the cropped PNG immediately; edit hands the crop
        to the annotator, which commits on a second "shot_edit" event
        (the user tapping the edit hotkey again).
        """
        if self._state != "idle":
            self.log.info("SHOT_IGNORED | video recording in progress")
            self._notify(
                title="Screenshot Failed",
                message="Stop the active video recording before taking a screenshot.",
                ok=False,
                subject_label="Action",
                subject="screenshot",
            )
            return
        mode = "edit" if edit else "plain"
        self.log.info(
            "SHOT_START   | mode=%s, capturing frozen snapshot for region select",
            mode,
        )
        try:
            frozen, mon = capture_primary_screen()
        except Exception as e:
            self.log.error("SHOT_ERROR   | capture failed: %s", e)
            self._notify(
                title="Screenshot Failed",
                message=f"Could not capture the screen: {e}",
                ok=False,
                subject_label="Action",
                subject=mode,
            )
            return
        region = select_region(self._root, frozen_image=frozen, monitor=mon)
        if region is None:
            self.log.info("SHOT_CANCEL  | selection aborted (mode=%s)", mode)
            return
        x, y, w, h = region
        lx = max(0, x - mon["left"])
        ly = max(0, y - mon["top"])
        rx = min(frozen.shape[1], lx + w)
        ry = min(frozen.shape[0], ly + h)
        cropped = frozen[ly:ry, lx:rx]
        if cropped.size == 0:
            self.log.error("SHOT_ERROR   | empty region after crop")
            self._notify(
                title="Screenshot Failed",
                message="The selected region was empty after cropping.",
                ok=False,
                subject_label="Action",
                subject=mode,
            )
            return

        if edit:
            self._state = "annotating_shot"
            self.log.info(
                "SHOT_ANNOTATE| region=%dx%d, awaiting strokes (F18 save, Esc cancel)",
                region[2], region[3],
            )
            annotation_result = annotate_image(
                self._root, cropped, self._events,
                commit_event="shot_edit",
            )
            self._state = "idle"
            # Drop any hotkey events that piled up while the annotator
            # was blocking the worker — otherwise a stray F17/F20 tap
            # during annotation would fire the moment the window closes.
            self._drain_events()
            if annotation_result is None:
                self.log.info("SHOT_CANCEL  | annotation aborted")
                return
            save_array = annotation_result["image"]
            annotation_metadata = annotation_result["metadata"]
            out_prefix = "screenshot_edited_"
        else:
            save_array = cropped
            annotation_metadata = None
            out_prefix = "screenshot_"

        out_dir = self.output_dir / (out_prefix + datetime.now().strftime("%Y%m%d_%H%M%S"))
        out_dir.mkdir(parents=True, exist_ok=True)
        png_path = out_dir / "screenshot.png"
        metadata_path = out_dir / "annotations.json"
        try:
            Image.fromarray(save_array).save(png_path)
            if annotation_metadata is not None:
                with metadata_path.open("w", encoding="utf-8") as f:
                    json.dump(annotation_metadata, f, ensure_ascii=False, indent=2)
            sounds.shutter()
        except Exception as e:
            self.log.error("SHOT_ERROR   | save failed: %s", e)
            self._notify(
                title="Screenshot Failed",
                message=f"Could not save the screenshot: {e}",
                ok=False,
                subject_label="File",
                subject=str(png_path),
            )
            return
        clip_note = ""
        clip_error = None
        clipboard_path = out_dir if edit else png_path
        try:
            pyperclip.copy(str(clipboard_path))
        except Exception as e:
            clip_error = str(e)
            clip_note = f" (clipboard failed: {e})"
        self.log.info(
            "SHOT_SAVED   | mode=%s region=%dx%d file=%s clipboard=%s%s",
            mode, region[2], region[3], png_path, clipboard_path, clip_note,
        )
        shot_name = "Annotated Screenshot" if edit else "Screenshot"
        subject_label = "Folder" if edit else "File"
        subject_path = out_dir if edit else png_path
        if clip_error is not None:
            self._notify(
                title=f"{shot_name} Saved, Clipboard Failed",
                message=f"Image saved, but clipboard copy failed: {clip_error}",
                ok=False,
                subject_label=subject_label,
                subject=str(subject_path),
            )
        else:
            detail = (
                "Image and annotations JSON saved. Folder path copied to clipboard."
                if edit
                else "Image saved. File path copied to clipboard."
            )
            self._notify(
                title=f"{shot_name} Saved",
                message=detail,
                ok=True,
                subject_label=subject_label,
                subject=str(subject_path),
            )

    def _drain_events(self):
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break
