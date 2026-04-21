"""Two-point area selection overlay + primary-monitor snapshot helper.

Tk usage is parent-driven: the caller passes in a long-lived Tk root
and the overlay is created as a Toplevel on it, then dismissed via
wait_window(). This keeps every Tk operation on a single thread —
running multiple Tk() instances across threads in one process trips
"Tcl_AsyncDelete: async handler deleted by the wrong thread".

Two flavours of overlay:
  * live (for video): translucent dim over the live desktop.
  * frozen (for screenshot): a pre-captured snapshot of the primary
    monitor, so transient UI (dropdowns, tooltips, menus) stays
    visible in the picker even though opening the overlay stole focus.
"""

import tkinter as tk

import numpy as np
import mss
from PIL import Image, ImageTk


def capture_primary_screen():
    """Snapshot the primary monitor. Returns (rgb_ndarray, monitor_dict)."""
    with mss.mss() as sct:
        mon = sct.monitors[1]  # [0] is virtual desktop; [1] is primary
        raw = sct.grab(mon)
    rgb = np.array(raw)[:, :, :3][:, :, ::-1]
    return rgb, dict(mon)


def select_region(root, frozen_image=None, monitor=None):
    """Show a Toplevel overlay for two-point selection on the given root.

    Returns (x, y, w, h) in absolute screen pixels, or None if cancelled.
    """
    win = tk.Toplevel(root)
    if monitor is not None:
        win.geometry(
            f"{monitor['width']}x{monitor['height']}"
            f"+{monitor['left']}+{monitor['top']}"
        )
    win.attributes("-fullscreen", True)
    win.attributes("-topmost", True)
    win.config(cursor="crosshair")

    if frozen_image is not None:
        win.attributes("-alpha", 1.0)
        canvas = tk.Canvas(win, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        bg_pil = Image.fromarray(frozen_image)
        bg_photo = ImageTk.PhotoImage(bg_pil, master=win)
        canvas._bg_photo_ref = bg_photo  # prevent GC
        canvas.create_image(0, 0, anchor="nw", image=bg_photo)
        h_img, w_img = frozen_image.shape[:2]
        canvas.create_rectangle(
            0, 0, w_img, h_img,
            fill="black", stipple="gray25", outline="",
        )
        info_text = "Frozen screen — click two corner points (Esc to cancel)"
    else:
        win.attributes("-alpha", 0.55)
        canvas = tk.Canvas(win, bg="gray15", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        info_text = "Click two corner points (Esc to cancel)"

    info = canvas.create_text(
        20, 20, anchor="nw",
        text=info_text,
        fill="white", font=("Segoe UI", 14, "bold"),
    )

    state = {
        "screen_points": [], "canvas_points": [],
        "rect_halo": None, "rect": None, "ok": False,
    }

    def on_click(e):
        state["screen_points"].append((e.x_root, e.y_root))
        state["canvas_points"].append((e.x, e.y))
        if len(state["canvas_points"]) == 1:
            canvas.delete(info)
            state["rect_halo"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline="white", width=9
            )
            state["rect"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline="#ff0033", width=4
            )
        elif len(state["canvas_points"]) == 2:
            state["ok"] = True
            win.after(10, win.destroy)

    def on_motion(e):
        if len(state["canvas_points"]) == 1 and state["rect"] is not None:
            x0, y0 = state["canvas_points"][0]
            canvas.coords(state["rect_halo"], x0, y0, e.x, e.y)
            canvas.coords(state["rect"], x0, y0, e.x, e.y)

    def on_escape(_):
        state["ok"] = False
        win.destroy()

    win.bind("<Button-1>", on_click)
    win.bind("<Motion>", on_motion)
    win.bind("<Escape>", on_escape)
    win.grab_set()
    win.focus_force()
    root.wait_window(win)

    # Drop the PhotoImage reference explicitly — otherwise it may live
    # on via canvas._bg_photo_ref attached to a dead widget and get GC'd
    # later on a different thread.
    try:
        canvas._bg_photo_ref = None
    except Exception:
        pass

    if not state["ok"] or len(state["screen_points"]) != 2:
        return None
    (x1, y1), (x2, y2) = state["screen_points"]
    x, y = min(x1, x2), min(y1, y2)
    w, h = abs(x2 - x1), abs(y2 - y1)
    if w < 5 or h < 5:
        return None
    return (x, y, w, h)
