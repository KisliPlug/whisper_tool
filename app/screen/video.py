"""Live screen capture recorder and the red border indicator shown
around the active recording region.

The border is implemented as four Toplevel windows on a caller-supplied
Tk root, NOT a separate Tk instance — keeping all Tk operations on one
thread to avoid "Tcl_AsyncDelete: async handler deleted by the wrong
thread" panics.
"""

import threading
import time
import tkinter as tk

import numpy as np
import mss


class VideoRecorder:
    """Grabs the specified region at a fixed FPS into an in-memory list
    of RGB numpy frames. Stop returns (frames, fps).
    """

    def __init__(self, region, fps=15):
        self.region = region
        self.fps = fps
        self.frames = []
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        x, y, w, h = self.region
        bbox = {"left": x, "top": y, "width": w, "height": h}
        interval = 1.0 / self.fps
        with mss.mss() as sct:
            next_t = time.perf_counter()
            while not self._stop.is_set():
                raw = sct.grab(bbox)
                img = np.array(raw)[:, :, :3][:, :, ::-1]  # BGRA -> RGB
                self.frames.append(np.ascontiguousarray(img))
                next_t += interval
                sleep = next_t - time.perf_counter()
                if sleep > 0:
                    time.sleep(sleep)
                else:
                    next_t = time.perf_counter()

    def stop(self):
        self._stop.set()
        self._thread.join()
        return self.frames, self.fps


class BorderIndicator:
    """Four thin red borderless Toplevel windows hugging the outside of
    the recording region, signalling an active capture.

    The strips sit just OUTSIDE the region bbox, so mss.grab() never
    reads them into the recording. All four Toplevels share the caller's
    Tk root — show()/hide() must be invoked on the root's thread.
    """

    THICKNESS = 4
    COLOR = "#ff2020"

    def __init__(self, root, region):
        self.root = root
        self.region = region
        self._wins = []

    def show(self):
        x, y, w, h = self.region
        t = self.THICKNESS
        rects = [
            (x - t, y - t, w + 2 * t, t),  # top
            (x - t, y + h,     w + 2 * t, t),  # bottom
            (x - t, y,         t,         h),  # left
            (x + w, y,         t,         h),  # right
        ]
        for wx, wy, ww, wh in rects:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.geometry(f"{max(1, ww)}x{max(1, wh)}+{wx}+{wy}")
            win.configure(bg=self.COLOR)
            self._wins.append(win)
        try:
            self.root.update()
        except Exception:
            pass

    def hide(self):
        for w in self._wins:
            try:
                w.destroy()
            except Exception:
                pass
        self._wins.clear()
        try:
            self.root.update()
        except Exception:
            pass
