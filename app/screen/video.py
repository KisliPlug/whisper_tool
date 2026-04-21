"""Live screen capture recorder and the red border indicator shown
around the active recording region.
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
    """Four thin red borderless Tk windows hugging the outside of the
    recording region, signalling an active capture.

    The strips sit just OUTSIDE the region bbox, so mss.grab() never
    reads them into the recording.
    """

    THICKNESS = 4
    COLOR = "#ff2020"

    def __init__(self, region):
        self.region = region
        self._stop = threading.Event()
        self._thread = None
        self._ready = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=1.0)

    def _run(self):
        try:
            x, y, w, h = self.region
            t = self.THICKNESS
            root = tk.Tk()
            root.withdraw()
            rects = [
                (x - t, y - t, w + 2 * t, t),
                (x - t, y + h,     w + 2 * t, t),
                (x - t, y,         t,         h),
                (x + w, y,         t,         h),
            ]
            wins = []
            for wx, wy, ww, wh in rects:
                win = tk.Toplevel(root)
                win.overrideredirect(True)
                win.attributes("-topmost", True)
                win.geometry(f"{max(1, ww)}x{max(1, wh)}+{wx}+{wy}")
                win.configure(bg=self.COLOR)
                wins.append(win)
            self._ready.set()

            def tick():
                if self._stop.is_set():
                    for w in wins:
                        try:
                            w.destroy()
                        except Exception:
                            pass
                    root.quit()
                else:
                    root.after(80, tick)

            root.after(80, tick)
            root.mainloop()
            try:
                root.destroy()
            except Exception:
                pass
        except Exception as e:
            print(f"[border] unavailable: {e}")
            self._ready.set()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
