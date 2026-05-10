"""Screenshot annotation overlay.

Pipeline: F18 pressed -> frozen snapshot + region selection -> cropped
image handed to `annotate_image`, which opens a Toplevel canvas where
the user can add annotations. A second F18 press (posted to the shared
event queue as `commit_event`) commits the annotated image; Esc cancels.
Non-commit events that arrive during annotation are silently dropped.

All widgets live on the caller-supplied Tk root so Tk stays
single-threaded. See controller.py for the worker/root ownership model.
"""

import copy
import queue as _queue
import tkinter as tk

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk


COLORS = {
    "red": "#ff2a2a",
    "orange": "#f97316",
    "yellow": "#facc15",
    "green": "#22c55e",
    "cyan": "#06b6d4",
    "blue": "#1e90ff",
    "purple": "#a855f7",
    "white": "#ffffff",
    "black": "#111111",
}
TOOLS = ("select", "pen", "square", "circle", "text")
DEFAULT_COLOR = "red"
DEFAULT_TOOL = "select"
DEFAULT_WIDTH = 4
DEFAULT_FONT_SIZE = 20
MIN_WIDTH = 1
MAX_WIDTH = 40
MIN_FONT_SIZE = 10
MAX_FONT_SIZE = 96


def annotate_image(root, image_np, event_queue, commit_event="shot_edit"):
    """Open an annotation overlay on the given RGB ndarray.

    Returns {"image": np.ndarray, "metadata": dict} on save, or None on
    cancel. Save triggers on a `commit_event` value popped off
    `event_queue`, i.e. the user pressing the edit-screenshot hotkey a
    second time. Non-save events that arrive during annotation are
    dropped.
    """
    h, w = image_np.shape[:2]
    pil_img = Image.fromarray(image_np)

    # Fit the display inside the screen while keeping the saved image
    # at original resolution. Annotation coordinates are stored in
    # displayed-image pixels and scaled back up when rendered.
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    max_display_w = max(200, screen_w - 80)
    max_display_h = max(200, screen_h - 180)
    scale = min(1.0, max_display_w / w, max_display_h / h)
    display_w = max(1, int(w * scale))
    display_h = max(1, int(h * scale))

    if scale < 1.0:
        display_pil = pil_img.resize((display_w, display_h), Image.LANCZOS)
    else:
        display_pil = pil_img

    win = tk.Toplevel(root)
    win.title("Annotate screenshot")
    win.attributes("-topmost", True)
    win.configure(bg="#0a0a0a")

    photo = ImageTk.PhotoImage(display_pil, master=win)

    toolbar = tk.Frame(win, bg="#1a1a1a")
    toolbar.pack(fill="x")

    note_row = tk.Frame(toolbar, bg="#1a1a1a")
    note_row.pack(fill="x", padx=8, pady=(7, 3))

    tool_row = tk.Frame(toolbar, bg="#1a1a1a")
    tool_row.pack(fill="x", padx=8, pady=(7, 3))

    color_row = tk.Frame(toolbar, bg="#1a1a1a")
    color_row.pack(fill="x", padx=8, pady=(0, 7))

    status = tk.Label(
        toolbar,
        bg="#1a1a1a",
        fg="#e5e5e5",
        font=("Segoe UI", 9),
        anchor="w",
        padx=8,
        pady=4,
    )
    status.pack(fill="x")

    canvas = tk.Canvas(
        win,
        width=display_w,
        height=display_h,
        highlightthickness=0,
        bg="black",
        cursor="pencil",
    )
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas._photo_ref = photo

    state = {"result": None, "done": False}
    annotations: list[dict] = []
    notes = [{"id": 1, "text": ""}]
    current = {"annotation": None, "preview_id": None, "start": None}
    selected = {"annotation": None, "outline_id": None, "handle_ids": []}
    resize = {"annotation": None, "handle": None, "start_bbox": None}
    undo_stack: list[dict] = []
    redo_stack: list[dict] = []
    color = {"hex": COLORS[DEFAULT_COLOR], "name": DEFAULT_COLOR}
    stroke_width = {"v": DEFAULT_WIDTH}
    font_size = {"v": DEFAULT_FONT_SIZE}
    tool = {"name": DEFAULT_TOOL}
    active_note_index = {"v": 0}
    tool_buttons: dict[str, tk.Button] = {}
    color_buttons: dict[str, tk.Button] = {}
    note_label = tk.Label(
        note_row,
        bg="#1a1a1a",
        fg="#f8fafc",
        font=("Segoe UI", 9, "bold"),
        width=9,
        anchor="w",
    )
    note_label.pack(side="left", padx=(0, 6))
    note_preview = tk.Label(
        note_row,
        bg="#1a1a1a",
        fg="#d4d4d8",
        font=("Segoe UI", 9),
        anchor="w",
    )
    note_preview.pack(side="left", fill="x", expand=True, padx=(8, 0))

    def clamp_canvas_point(x, y):
        return (
            max(0, min(display_w, int(x))),
            max(0, min(display_h, int(y))),
        )

    def rect_bbox(x0, y0, x1, y1):
        x1, y1 = clamp_canvas_point(x1, y1)
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def normalize_bbox(bbox):
        x1, y1, x2, y2 = bbox
        x1, y1 = clamp_canvas_point(x1, y1)
        x2, y2 = clamp_canvas_point(x2, y2)
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))

    def active_note():
        return notes[active_note_index["v"]]

    def note_title(note):
        return f"Note {note['id']}"

    def update_note_widgets():
        note = active_note()
        note_label.config(text=note_title(note))
        preview = note["text"].replace("\n", " ").strip()
        if len(preview) > 140:
            preview = preview[:137] + "..."
        note_preview.config(text=preview or "No note text")

    def update_toolbar():
        for name, button in tool_buttons.items():
            button.config(relief="sunken" if name == tool["name"] else "raised")
        for name, button in color_buttons.items():
            selected = name == color["name"]
            button.config(
                relief="sunken" if selected else "raised",
                highlightthickness=2 if selected else 0,
                highlightbackground="#f8fafc" if selected else "#1a1a1a",
            )
        cursor = "arrow" if tool["name"] == "select" else "xterm" if tool["name"] == "text" else "pencil"
        canvas.config(cursor=cursor)
        update_note_widgets()
        status.config(
            text=(
                f"{note_title(active_note())}   Tool: {tool['name']}   Color: {color['name']}   "
                f"Width: {stroke_width['v']}   Text: {font_size['v']}   "
                "Ctrl+Enter finish text   Del delete   Ctrl+Z undo   Ctrl+Y redo   F18 save"
            ),
            fg=color["hex"],
        )

    def annotation_snapshot(annotation):
        return copy.deepcopy({
            k: v for k, v in annotation.items()
            if k != "canvas_ids"
        })

    def make_snapshot():
        return {
            "notes": copy.deepcopy(notes),
            "active_note_index": active_note_index["v"],
            "annotations": [
                annotation_snapshot(annotation)
                for annotation in annotations
            ],
        }

    def text_bbox(annotation):
        if "bbox" in annotation:
            return tuple(annotation["bbox"])
        x, y = annotation["xy"]
        bbox = annotation_bbox(annotation)
        if bbox is not None:
            return bbox
        return (x, y, min(display_w, x + 260), min(display_h, y + 120))

    def draw_annotation(annotation):
        if annotation["type"] == "pen":
            ids = []
            points = annotation["points"]
            for first, second in zip(points, points[1:]):
                ids.append(canvas.create_line(
                    first[0],
                    first[1],
                    second[0],
                    second[1],
                    fill=annotation["color"],
                    width=annotation["width"],
                    capstyle="round",
                    smooth=True,
                ))
            return ids
        if annotation["type"] == "square":
            return [canvas.create_rectangle(
                *annotation["bbox"],
                outline=annotation["color"],
                width=annotation["width"],
            )]
        if annotation["type"] == "circle":
            return [canvas.create_oval(
                *annotation["bbox"],
                outline=annotation["color"],
                width=annotation["width"],
            )]
        if annotation["type"] == "text":
            bbox = text_bbox(annotation)
            x, y = bbox[0], bbox[1]
            annotation["bbox"] = bbox
            annotation["xy"] = (x, y)
            return [canvas.create_text(
                x,
                y,
                anchor="nw",
                text=annotation["text"],
                fill=annotation["color"],
                font=("Segoe UI", annotation["font_size"]),
                width=max(20, bbox[2] - bbox[0]),
            )]
        return []

    def push_history():
        undo_stack.append(make_snapshot())
        if len(undo_stack) > 100:
            undo_stack.pop(0)
        redo_stack.clear()

    def restore_snapshot(snapshot):
        clear_selection()
        for annotation in annotations:
            for cid in annotation.get("canvas_ids", []):
                canvas.delete(cid)
        notes[:] = copy.deepcopy(snapshot["notes"])
        if not notes:
            notes.append({"id": 1, "text": ""})
        active_note_index["v"] = max(
            0,
            min(len(notes) - 1, int(snapshot.get("active_note_index", 0))),
        )
        annotations.clear()
        for raw in snapshot["annotations"]:
            annotation = copy.deepcopy(raw)
            annotation["canvas_ids"] = draw_annotation(annotation)
            annotations.append(annotation)
        refresh_note_visibility()
        update_toolbar()

    def undo_action():
        close_inline_text_editor(commit=True)
        if not undo_stack:
            return
        redo_stack.append(make_snapshot())
        restore_snapshot(undo_stack.pop())

    def redo_action():
        close_inline_text_editor(commit=True)
        if not redo_stack:
            return
        undo_stack.append(make_snapshot())
        restore_snapshot(redo_stack.pop())

    def set_tool(name):
        if name not in TOOLS:
            return
        tool["name"] = name
        update_toolbar()

    def set_color(name):
        if name not in COLORS:
            return
        color["hex"] = COLORS[name]
        color["name"] = name
        if selected["annotation"] is not None:
            push_history()
            selected["annotation"]["color"] = color["hex"]
            selected["annotation"]["color_name"] = color["name"]
            apply_annotation_style(selected["annotation"])
            update_selection_outline()
        update_toolbar()

    def inc_width(delta):
        stroke_width["v"] = max(MIN_WIDTH, min(MAX_WIDTH, stroke_width["v"] + delta))
        annotation = selected["annotation"]
        if annotation is not None and annotation["type"] in ("pen", "square", "circle"):
            push_history()
            annotation["width"] = max(MIN_WIDTH, min(MAX_WIDTH, annotation["width"] + delta))
            apply_annotation_style(annotation)
            update_selection_outline()
        update_toolbar()

    def inc_font(delta):
        font_size["v"] = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, font_size["v"] + delta))
        annotation = selected["annotation"]
        if annotation is not None and annotation["type"] == "text":
            push_history()
            annotation["font_size"] = max(
                MIN_FONT_SIZE,
                min(MAX_FONT_SIZE, annotation["font_size"] + delta),
            )
            apply_annotation_style(annotation)
            update_selection_outline()
        update_toolbar()

    def delete_annotation(annotation):
        if selected["annotation"] is annotation:
            clear_selection()
        for cid in annotation.get("canvas_ids", []):
            canvas.delete(cid)

    def undo():
        close_inline_text_editor(commit=True)
        undo_action()

    def clear_all():
        close_inline_text_editor(commit=True)
        note_id = active_note()["id"]
        if not any(annotation["note_id"] == note_id for annotation in annotations):
            return
        push_history()
        kept = []
        for annotation in annotations:
            if annotation["note_id"] != note_id:
                kept.append(annotation)
                continue
            delete_annotation(annotation)
        annotations[:] = kept
        clear_selection()
        refresh_note_visibility()

    def clear_selection():
        if selected["outline_id"] is not None:
            canvas.delete(selected["outline_id"])
        for handle_id in selected["handle_ids"]:
            canvas.delete(handle_id)
        selected["annotation"] = None
        selected["outline_id"] = None
        selected["handle_ids"] = []
        resize["annotation"] = None
        resize["handle"] = None
        resize["start_bbox"] = None

    def is_active_note_annotation(annotation):
        return annotation["note_id"] == active_note()["id"]

    def refresh_note_visibility():
        active_id = active_note()["id"]
        for annotation in annotations:
            state_value = "normal" if annotation["note_id"] == active_id else "hidden"
            for cid in annotation.get("canvas_ids", []):
                canvas.itemconfigure(cid, state=state_value)
        if selected["outline_id"] is not None:
            canvas.itemconfigure(selected["outline_id"], state="normal")
        for handle_id in selected["handle_ids"]:
            canvas.itemconfigure(handle_id, state="normal")

    def annotation_bbox(annotation):
        if annotation["type"] == "text" and "bbox" in annotation:
            return tuple(annotation["bbox"])
        boxes = []
        for cid in annotation.get("canvas_ids", []):
            bbox = canvas.bbox(cid)
            if bbox:
                boxes.append(bbox)
        if not boxes:
            return None
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )

    def update_selection_outline():
        annotation = selected["annotation"]
        if selected["outline_id"] is not None:
            canvas.delete(selected["outline_id"])
            selected["outline_id"] = None
        for handle_id in selected["handle_ids"]:
            canvas.delete(handle_id)
        selected["handle_ids"] = []
        if annotation is None or not is_active_note_annotation(annotation):
            selected["annotation"] = None
            return
        bbox = annotation_bbox(annotation)
        if bbox is None:
            return
        pad = 4
        selected["outline_id"] = canvas.create_rectangle(
            bbox[0] - pad,
            bbox[1] - pad,
            bbox[2] + pad,
            bbox[3] + pad,
            outline="#f8fafc",
            dash=(4, 3),
            width=1,
        )
        canvas.tag_raise(selected["outline_id"])
        if annotation["type"] in ("square", "circle", "text"):
            handle_size = 8
            handles = (
                ("nw", bbox[0], bbox[1]),
                ("ne", bbox[2], bbox[1]),
                ("sw", bbox[0], bbox[3]),
                ("se", bbox[2], bbox[3]),
            )
            for name, hx, hy in handles:
                handle_id = canvas.create_rectangle(
                    hx - handle_size / 2,
                    hy - handle_size / 2,
                    hx + handle_size / 2,
                    hy + handle_size / 2,
                    fill="#f8fafc",
                    outline="#111827",
                    width=1,
                    tags=(f"resize:{name}",),
                )
                selected["handle_ids"].append(handle_id)
                canvas.tag_raise(handle_id)

    def select_annotation(annotation):
        if annotation is None or not is_active_note_annotation(annotation):
            clear_selection()
            return
        selected["annotation"] = annotation
        if annotation.get("color_name") in COLORS:
            color["name"] = annotation["color_name"]
            color["hex"] = annotation["color"]
        if annotation["type"] in ("pen", "square", "circle"):
            stroke_width["v"] = annotation["width"]
        elif annotation["type"] == "text":
            font_size["v"] = annotation["font_size"]
        update_selection_outline()
        update_toolbar()

    def find_annotation_at(x, y):
        hits = canvas.find_overlapping(x - 3, y - 3, x + 3, y + 3)
        if not hits:
            return None
        hit_ids = set(hits)
        for annotation in reversed(annotations):
            if not is_active_note_annotation(annotation):
                continue
            if any(cid in hit_ids for cid in annotation.get("canvas_ids", [])):
                return annotation
        return None

    def hit_resize_handle(x, y):
        annotation = selected["annotation"]
        if annotation is None or annotation["type"] not in ("square", "circle", "text"):
            return None
        bbox = annotation_bbox(annotation)
        if bbox is None:
            return None
        handles = {
            "nw": (bbox[0], bbox[1]),
            "ne": (bbox[2], bbox[1]),
            "sw": (bbox[0], bbox[3]),
            "se": (bbox[2], bbox[3]),
        }
        radius = 8
        for name, (hx, hy) in handles.items():
            if abs(x - hx) <= radius and abs(y - hy) <= radius:
                return name
        return None

    def bbox_from_handle(start_bbox, handle, x, y):
        x1, y1, x2, y2 = start_bbox
        x, y = clamp_canvas_point(x, y)
        if "n" in handle:
            y1 = y
        if "s" in handle:
            y2 = y
        if "w" in handle:
            x1 = x
        if "e" in handle:
            x2 = x
        bbox = normalize_bbox((x1, y1, x2, y2))
        if bbox[2] - bbox[0] < 20 or bbox[3] - bbox[1] < 20:
            return start_bbox
        return bbox

    def apply_annotation_geometry(annotation):
        if annotation["type"] in ("square", "circle"):
            for cid in annotation["canvas_ids"]:
                canvas.coords(cid, *annotation["bbox"])
        elif annotation["type"] == "text":
            bbox = text_bbox(annotation)
            annotation["bbox"] = bbox
            annotation["xy"] = (bbox[0], bbox[1])
            for cid in annotation["canvas_ids"]:
                canvas.coords(cid, bbox[0], bbox[1])
                canvas.itemconfigure(cid, width=max(20, bbox[2] - bbox[0]))

    def apply_annotation_style(annotation):
        if annotation["type"] == "pen":
            for cid in annotation["canvas_ids"]:
                canvas.itemconfigure(
                    cid,
                    fill=annotation["color"],
                    width=annotation["width"],
                )
        elif annotation["type"] in ("square", "circle"):
            for cid in annotation["canvas_ids"]:
                canvas.itemconfigure(
                    cid,
                    outline=annotation["color"],
                    width=annotation["width"],
                )
        elif annotation["type"] == "text":
            bbox = text_bbox(annotation)
            for cid in annotation["canvas_ids"]:
                canvas.itemconfigure(
                    cid,
                    fill=annotation["color"],
                    text=annotation["text"],
                    font=("Segoe UI", annotation["font_size"]),
                    width=max(20, bbox[2] - bbox[0]),
                )

    def delete_selected():
        annotation = selected["annotation"]
        if annotation is None:
            return
        push_history()
        delete_annotation(annotation)
        try:
            annotations.remove(annotation)
        except ValueError:
            pass

    def edit_selected():
        annotation = selected["annotation"]
        if annotation is None:
            return
        if annotation["type"] == "text":
            begin_inline_text_editor(text_bbox(annotation), annotation=annotation)
            return
        value = ask_number(
            title="Edit Width",
            label="Line width",
            initial=annotation.get("width", stroke_width["v"]),
            minimum=MIN_WIDTH,
            maximum=MAX_WIDTH,
        )
        if value is None:
            return
        push_history()
        annotation["width"] = value
        stroke_width["v"] = value
        apply_annotation_style(annotation)
        update_selection_outline()
        update_toolbar()

    def new_note():
        close_inline_text_editor(commit=True)
        push_history()
        clear_selection()
        next_id = max(note["id"] for note in notes) + 1
        notes.append({"id": next_id, "text": ""})
        active_note_index["v"] = len(notes) - 1
        refresh_note_visibility()
        update_toolbar()

    def prev_note():
        if len(notes) < 2:
            return
        close_inline_text_editor(commit=True)
        clear_selection()
        active_note_index["v"] = (active_note_index["v"] - 1) % len(notes)
        refresh_note_visibility()
        update_toolbar()

    def next_note():
        if len(notes) < 2:
            return
        close_inline_text_editor(commit=True)
        clear_selection()
        active_note_index["v"] = (active_note_index["v"] + 1) % len(notes)
        refresh_note_visibility()
        update_toolbar()

    def edit_note():
        close_inline_text_editor(commit=True)
        note = active_note()
        text = ask_multiline_text(title=note_title(note), initial=note["text"])
        if text is not None:
            push_history()
            note["text"] = text
            update_toolbar()

    def add_tool_button(name, label):
        button = tk.Button(
            tool_row,
            text=label,
            command=lambda n=name: set_tool(n),
            bg="#262626",
            fg="#f5f5f5",
            activebackground="#3f3f46",
            activeforeground="#ffffff",
            bd=1,
            padx=8,
            pady=3,
            font=("Segoe UI", 9),
        )
        button.pack(side="left", padx=(0, 5))
        tool_buttons[name] = button

    def add_color_button(name, value):
        button = tk.Button(
            color_row,
            text="",
            command=lambda n=name: set_color(n),
            bg=value,
            activebackground=value,
            width=3,
            height=1,
            bd=2,
        )
        button.pack(side="left", padx=(0, 5))
        color_buttons[name] = button

    def add_note_button(label, command):
        button = tk.Button(
            note_row,
            text=label,
            command=command,
            bg="#262626",
            fg="#f5f5f5",
            activebackground="#3f3f46",
            activeforeground="#ffffff",
            bd=1,
            padx=8,
            pady=3,
            font=("Segoe UI", 9),
        )
        button.pack(side="left", padx=(0, 5))

    add_note_button("<", prev_note)
    add_note_button(">", next_note)
    add_note_button("New Note", new_note)
    add_note_button("Edit Note", edit_note)

    add_tool_button("select", "Select")
    add_tool_button("pen", "Pen")
    add_tool_button("square", "Rect")
    add_tool_button("circle", "Circle")
    add_tool_button("text", "Text")
    tk.Button(
        tool_row,
        text="-",
        command=lambda: inc_width(-1),
        bg="#262626",
        fg="#f5f5f5",
        activebackground="#3f3f46",
        activeforeground="#ffffff",
        bd=1,
        width=3,
        font=("Segoe UI", 9),
    ).pack(side="left", padx=(8, 3))
    tk.Button(
        tool_row,
        text="+",
        command=lambda: inc_width(1),
        bg="#262626",
        fg="#f5f5f5",
        activebackground="#3f3f46",
        activeforeground="#ffffff",
        bd=1,
        width=3,
        font=("Segoe UI", 9),
    ).pack(side="left", padx=(0, 8))
    tk.Button(
        tool_row,
        text="Undo",
        command=undo,
        bg="#262626",
        fg="#f5f5f5",
        activebackground="#3f3f46",
        activeforeground="#ffffff",
        bd=1,
        padx=8,
        pady=3,
        font=("Segoe UI", 9),
    ).pack(side="left", padx=(0, 5))
    tk.Button(
        tool_row,
        text="Redo",
        command=redo_action,
        bg="#262626",
        fg="#f5f5f5",
        activebackground="#3f3f46",
        activeforeground="#ffffff",
        bd=1,
        padx=8,
        pady=3,
        font=("Segoe UI", 9),
    ).pack(side="left", padx=(0, 5))
    tk.Button(
        tool_row,
        text="Edit Selected",
        command=lambda: edit_selected(),
        bg="#262626",
        fg="#f5f5f5",
        activebackground="#3f3f46",
        activeforeground="#ffffff",
        bd=1,
        padx=8,
        pady=3,
        font=("Segoe UI", 9),
    ).pack(side="left", padx=(0, 5))
    tk.Button(
        tool_row,
        text="Clear Note",
        command=clear_all,
        bg="#262626",
        fg="#f5f5f5",
        activebackground="#3f3f46",
        activeforeground="#ffffff",
        bd=1,
        padx=8,
        pady=3,
        font=("Segoe UI", 9),
    ).pack(side="left")

    for color_name, color_value in COLORS.items():
        add_color_button(color_name, color_value)

    def ask_multiline_text(title="Text", initial=""):
        dialog = tk.Toplevel(win)
        dialog.title(title)
        dialog.transient(win)
        dialog.attributes("-topmost", True)
        dialog.configure(bg="#1a1a1a")
        dialog.resizable(True, True)

        result = {"value": None}
        editor = tk.Text(
            dialog,
            width=42,
            height=7,
            wrap="word",
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="#f8fafc",
            relief="flat",
            padx=8,
            pady=8,
            font=("Segoe UI", 10),
        )
        editor.pack(fill="both", expand=True, padx=10, pady=(10, 8))
        if initial:
            editor.insert("1.0", initial)

        actions = tk.Frame(dialog, bg="#1a1a1a")
        actions.pack(fill="x", padx=10, pady=(0, 10))

        def accept(_event=None):
            value = editor.get("1.0", "end-1c").rstrip("\n")
            result["value"] = value
            dialog.destroy()

        def reject(_event=None):
            dialog.destroy()

        tk.Button(
            actions,
            text="Add",
            command=accept,
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            bd=0,
            padx=12,
            pady=5,
            font=("Segoe UI", 9),
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            actions,
            text="Cancel",
            command=reject,
            bg="#3f3f46",
            fg="#ffffff",
            activebackground="#52525b",
            activeforeground="#ffffff",
            bd=0,
            padx=12,
            pady=5,
            font=("Segoe UI", 9),
        ).pack(side="right")

        dialog.bind("<Control-Return>", accept)
        dialog.bind("<Escape>", reject)
        dialog.protocol("WM_DELETE_WINDOW", reject)
        editor.focus_set()
        dialog.grab_set()
        win.wait_window(dialog)
        try:
            win.grab_set()
        except Exception:
            pass
        return result["value"]

    def ask_number(title, label, initial, minimum, maximum):
        dialog = tk.Toplevel(win)
        dialog.title(title)
        dialog.transient(win)
        dialog.attributes("-topmost", True)
        dialog.configure(bg="#1a1a1a")
        dialog.resizable(False, False)

        result = {"value": None}
        tk.Label(
            dialog,
            text=f"{label} ({minimum}-{maximum})",
            bg="#1a1a1a",
            fg="#f8fafc",
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(10, 4))

        value_var = tk.StringVar(value=str(initial))
        entry = tk.Entry(
            dialog,
            textvariable=value_var,
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 10),
        )
        entry.pack(fill="x", padx=10, pady=(0, 10))

        actions = tk.Frame(dialog, bg="#1a1a1a")
        actions.pack(fill="x", padx=10, pady=(0, 10))

        def accept(_event=None):
            try:
                value = int(value_var.get())
            except ValueError:
                return
            result["value"] = max(minimum, min(maximum, value))
            dialog.destroy()

        def reject(_event=None):
            dialog.destroy()

        tk.Button(
            actions,
            text="Apply",
            command=accept,
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            bd=0,
            padx=12,
            pady=5,
            font=("Segoe UI", 9),
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            actions,
            text="Cancel",
            command=reject,
            bg="#3f3f46",
            fg="#ffffff",
            activebackground="#52525b",
            activeforeground="#ffffff",
            bd=0,
            padx=12,
            pady=5,
            font=("Segoe UI", 9),
        ).pack(side="right")

        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", reject)
        dialog.protocol("WM_DELETE_WINDOW", reject)
        entry.focus_set()
        entry.select_range(0, "end")
        dialog.grab_set()
        win.wait_window(dialog)
        try:
            win.grab_set()
        except Exception:
            pass
        return result["value"]

    inline_editor = {
        "text_id": None,
        "cursor_id": None,
        "annotation": None,
        "bbox": None,
        "text": "",
        "font_size": DEFAULT_FONT_SIZE,
        "color": COLORS[DEFAULT_COLOR],
        "color_name": DEFAULT_COLOR,
    }

    def inline_editor_active():
        return inline_editor["text_id"] is not None

    def update_inline_text_cursor():
        if not inline_editor_active():
            return
        text_id = inline_editor["text_id"]
        bbox = inline_editor["bbox"]
        text_bounds = canvas.bbox(text_id)
        if text_bounds is None:
            x = bbox[0]
            y1 = bbox[1]
            y2 = bbox[1] + inline_editor["font_size"]
        else:
            x = min(bbox[2], text_bounds[2] + 2)
            y1 = max(bbox[1], text_bounds[1])
            y2 = max(y1 + inline_editor["font_size"], text_bounds[3])
        if inline_editor["cursor_id"] is None:
            inline_editor["cursor_id"] = canvas.create_line(
                x,
                y1,
                x,
                y2,
                fill=inline_editor["color"],
                width=1,
            )
        else:
            canvas.coords(inline_editor["cursor_id"], x, y1, x, y2)
            canvas.itemconfigure(inline_editor["cursor_id"], fill=inline_editor["color"])
        canvas.tag_raise(inline_editor["cursor_id"])

    def redraw_inline_text():
        if not inline_editor_active():
            return
        bbox = inline_editor["bbox"]
        canvas.itemconfigure(
            inline_editor["text_id"],
            text=inline_editor["text"],
            fill=inline_editor["color"],
            font=("Segoe UI", inline_editor["font_size"]),
            width=max(20, bbox[2] - bbox[0]),
        )
        canvas.coords(inline_editor["text_id"], bbox[0], bbox[1])
        update_inline_text_cursor()

    def close_inline_text_editor(commit):
        if not inline_editor_active():
            return True
        annotation = inline_editor["annotation"]
        bbox = inline_editor["bbox"]
        text = inline_editor["text"]
        text_id = inline_editor["text_id"]
        cursor_id = inline_editor["cursor_id"]
        font_size_value = inline_editor["font_size"]
        color_value = inline_editor["color"]
        color_name_value = inline_editor["color_name"]
        inline_editor["text_id"] = None
        inline_editor["cursor_id"] = None
        inline_editor["annotation"] = None
        inline_editor["bbox"] = None
        inline_editor["text"] = ""
        if text_id is not None:
            canvas.delete(text_id)
        if cursor_id is not None:
            canvas.delete(cursor_id)
        if not commit:
            return False
        if annotation is None:
            if not text.strip():
                return False
            create_text_annotation(
                bbox,
                text,
                font_size_value=font_size_value,
                color_value=color_value,
                color_name_value=color_name_value,
            )
            return True
        if text == annotation["text"] and tuple(bbox) == tuple(text_bbox(annotation)):
            return True
        push_history()
        annotation["text"] = text
        annotation["bbox"] = bbox
        annotation["xy"] = (bbox[0], bbox[1])
        annotation["font_size"] = font_size_value
        annotation["color"] = color_value
        annotation["color_name"] = color_name_value
        apply_annotation_geometry(annotation)
        apply_annotation_style(annotation)
        select_annotation(annotation)
        return True

    def begin_inline_text_editor(bbox, annotation=None):
        close_inline_text_editor(commit=True)
        clear_selection()
        bbox = normalize_bbox(bbox)
        inline_text = annotation["text"] if annotation is not None else ""
        inline_color = annotation["color"] if annotation is not None else color["hex"]
        inline_color_name = (
            annotation.get("color_name", color["name"])
            if annotation is not None else color["name"]
        )
        inline_font_size = (
            annotation["font_size"] if annotation is not None else font_size["v"]
        )
        text_id = canvas.create_text(
            bbox[0],
            bbox[1],
            anchor="nw",
            text=inline_text,
            fill=inline_color,
            font=("Segoe UI", inline_font_size),
            width=max(20, bbox[2] - bbox[0]),
        )
        inline_editor["text_id"] = text_id
        inline_editor["cursor_id"] = None
        inline_editor["annotation"] = annotation
        inline_editor["bbox"] = bbox
        inline_editor["text"] = inline_text
        inline_editor["font_size"] = inline_font_size
        inline_editor["color"] = inline_color
        inline_editor["color_name"] = inline_color_name
        update_inline_text_cursor()
        canvas.focus_set()

    def on_inline_keypress(e):
        if not inline_editor_active():
            return None
        ctrl_down = bool(e.state & 0x4)
        if e.keysym == "Escape":
            close_inline_text_editor(commit=False)
            return "break"
        if e.keysym == "Return":
            if ctrl_down:
                close_inline_text_editor(commit=True)
            else:
                inline_editor["text"] += "\n"
                redraw_inline_text()
            return "break"
        if e.keysym == "BackSpace":
            inline_editor["text"] = inline_editor["text"][:-1]
            redraw_inline_text()
            return "break"
        if e.keysym == "Delete":
            return "break"
        if e.char and not ctrl_down:
            inline_editor["text"] += e.char
            redraw_inline_text()
            return "break"
        return "break"

    def create_text_annotation(
        bbox,
        text,
        font_size_value=None,
        color_value=None,
        color_name_value=None,
    ):
        push_history()
        bbox = normalize_bbox(bbox)
        annotation = {
            "type": "text",
            "note_id": active_note()["id"],
            "color": color_value if color_value is not None else color["hex"],
            "color_name": color_name_value if color_name_value is not None else color["name"],
            "font_size": font_size_value if font_size_value is not None else font_size["v"],
            "xy": (bbox[0], bbox[1]),
            "bbox": bbox,
            "text": text,
            "canvas_ids": [],
        }
        annotation["canvas_ids"] = draw_annotation(annotation)
        annotations.append(annotation)
        select_annotation(annotation)

    def on_down(e):
        x, y = clamp_canvas_point(e.x, e.y)
        close_inline_text_editor(commit=True)
        if tool["name"] == "select":
            handle = hit_resize_handle(x, y)
            if handle is not None:
                annotation = selected["annotation"]
                push_history()
                resize["annotation"] = annotation
                resize["handle"] = handle
                resize["start_bbox"] = annotation["bbox"]
                canvas.config(cursor="crosshair")
                return
            select_annotation(find_annotation_at(x, y))
            return
        if tool["name"] == "text":
            current["start"] = (x, y)
            clear_selection()
            current["preview_id"] = canvas.create_rectangle(
                x,
                y,
                x,
                y,
                outline=color["hex"],
                dash=(4, 3),
                width=1,
            )
            return
        current["start"] = (x, y)
        clear_selection()
        if tool["name"] == "pen":
            push_history()
            annotation = {
                "type": "pen",
                "note_id": active_note()["id"],
                "color": color["hex"],
                "color_name": color["name"],
                "width": stroke_width["v"],
                "points": [(x, y)],
                "canvas_ids": [],
            }
            current["annotation"] = annotation
            annotations.append(annotation)
            return
        bbox = (x, y, x, y)
        if tool["name"] == "square":
            preview_id = canvas.create_rectangle(
                *bbox,
                outline=color["hex"],
                width=stroke_width["v"],
            )
        else:
            preview_id = canvas.create_oval(
                *bbox,
                outline=color["hex"],
                width=stroke_width["v"],
            )
        current["preview_id"] = preview_id

    def on_motion(e):
        x, y = clamp_canvas_point(e.x, e.y)
        if tool["name"] == "select":
            if resize["annotation"] is not None:
                annotation = resize["annotation"]
                annotation["bbox"] = bbox_from_handle(
                    resize["start_bbox"],
                    resize["handle"],
                    x,
                    y,
                )
                apply_annotation_geometry(annotation)
                update_selection_outline()
                canvas.config(cursor="crosshair")
                return
            canvas.config(
                cursor="crosshair" if hit_resize_handle(x, y) else "arrow"
            )
            return
        if tool["name"] == "pen":
            annotation = current["annotation"]
            if annotation is None:
                return
            last = annotation["points"][-1]
            line_id = canvas.create_line(
                last[0],
                last[1],
                x,
                y,
                fill=annotation["color"],
                width=annotation["width"],
                capstyle="round",
                smooth=True,
            )
            annotation["points"].append((x, y))
            annotation["canvas_ids"].append(line_id)
            return
        if tool["name"] not in ("square", "circle", "text"):
            return
        if current["preview_id"] is None or current["start"] is None:
            return
        x0, y0 = current["start"]
        canvas.coords(current["preview_id"], *rect_bbox(x0, y0, x, y))

    def on_up(e):
        if tool["name"] == "select":
            resize["annotation"] = None
            resize["handle"] = None
            resize["start_bbox"] = None
            x, y = clamp_canvas_point(e.x, e.y)
            canvas.config(
                cursor="crosshair" if hit_resize_handle(x, y) else "arrow"
            )
            return
        if tool["name"] == "pen":
            current["annotation"] = None
            return
        if tool["name"] not in ("square", "circle", "text"):
            return
        if current["preview_id"] is None or current["start"] is None:
            return
        x, y = clamp_canvas_point(e.x, e.y)
        x0, y0 = current["start"]
        bbox = rect_bbox(x0, y0, x, y)
        if tool["name"] == "text" and (
            abs(bbox[2] - bbox[0]) < 20 or abs(bbox[3] - bbox[1]) < 20
        ):
            bbox = (
                x0,
                y0,
                min(display_w, x0 + 280),
                min(display_h, y0 + 120),
            )
        if tool["name"] == "text":
            canvas.delete(current["preview_id"])
            begin_inline_text_editor(bbox)
            current["preview_id"] = None
            current["start"] = None
            return
        if abs(bbox[2] - bbox[0]) < 3 or abs(bbox[3] - bbox[1]) < 3:
            canvas.delete(current["preview_id"])
        else:
            push_history()
            annotation = {
                "type": tool["name"],
                "note_id": active_note()["id"],
                "color": color["hex"],
                "color_name": color["name"],
                "width": stroke_width["v"],
                "bbox": bbox,
                "canvas_ids": [current["preview_id"]],
            }
            annotations.append(annotation)
            select_annotation(annotation)
        current["preview_id"] = None
        current["start"] = None

    def on_double_click(e):
        x, y = clamp_canvas_point(e.x, e.y)
        annotation = find_annotation_at(x, y)
        if annotation is None:
            return
        select_annotation(annotation)
        edit_selected()

    def load_font(size):
        for font_name in ("segoeui.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(font_name, size)
            except OSError:
                continue
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def font_text_width(font, text):
        try:
            return font.getlength(text)
        except Exception:
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0]

    def wrap_text_to_width(text, font, max_width):
        lines = []
        for paragraph in text.split("\n"):
            if not paragraph:
                lines.append("")
                continue
            current_line = ""
            for word in paragraph.split(" "):
                candidate = word if not current_line else f"{current_line} {word}"
                if font_text_width(font, candidate) <= max_width:
                    current_line = candidate
                    continue
                if current_line:
                    lines.append(current_line)
                if font_text_width(font, word) <= max_width:
                    current_line = word
                    continue
                chunk = ""
                for ch in word:
                    candidate = chunk + ch
                    if chunk and font_text_width(font, candidate) > max_width:
                        lines.append(chunk)
                        chunk = ch
                    else:
                        chunk = candidate
                current_line = chunk
            if current_line:
                lines.append(current_line)
        return "\n".join(lines)

    def scaled_point(point, inv):
        x, y = point
        return [int(round(x * inv)), int(round(y * inv))]

    def scaled_bbox(bbox, inv):
        return [int(round(v * inv)) for v in bbox]

    def points_bbox(points):
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return [min(xs), min(ys), max(xs), max(ys)]

    def build_metadata(inv):
        items_by_note = {note["id"]: [] for note in notes}
        for index, annotation in enumerate(annotations, start=1):
            note_id = annotation["note_id"]
            base = {
                "id": index,
                "type": annotation["type"],
                "color": annotation["color"],
                "color_name": annotation.get("color_name"),
            }
            if annotation["type"] == "pen":
                points = [
                    scaled_point(point, inv)
                    for point in annotation["points"]
                ]
                if len(points) < 2:
                    continue
                base.update({
                    "type": "freehand",
                    "points": points,
                    "width": max(1, int(round(annotation["width"] * inv))),
                    "bbox": points_bbox(points),
                })
            elif annotation["type"] == "square":
                base.update({
                    "type": "rectangle",
                    "bbox": scaled_bbox(annotation["bbox"], inv),
                    "width": max(1, int(round(annotation["width"] * inv))),
                })
            elif annotation["type"] == "circle":
                base.update({
                    "type": "ellipse",
                    "bbox": scaled_bbox(annotation["bbox"], inv),
                    "width": max(1, int(round(annotation["width"] * inv))),
                })
            elif annotation["type"] == "text":
                bbox = text_bbox(annotation)
                base.update({
                    "xy": scaled_point((bbox[0], bbox[1]), inv),
                    "bbox": scaled_bbox(bbox, inv),
                    "text": annotation["text"],
                    "font_size": max(1, int(round(annotation["font_size"] * inv))),
                })
            items_by_note.setdefault(note_id, []).append(base)

        metadata_notes = []
        for note in notes:
            items = items_by_note.get(note["id"], [])
            if not note["text"].strip() and not items:
                continue
            metadata_notes.append({
                "id": note["id"],
                "text": note["text"],
                "items": items,
            })
        return {
            "version": 1,
            "image": "screenshot.png",
            "size": {"width": w, "height": h},
            "coordinate_space": "screenshot_pixels",
            "notes": metadata_notes,
        }

    def save_and_close():
        if state["done"]:
            return
        close_inline_text_editor(commit=True)
        annotated = pil_img.copy()
        draw = ImageDraw.Draw(annotated)
        inv = 1.0 / scale
        for annotation in annotations:
            width = max(1, int(round(annotation.get("width", 1) * inv)))
            if annotation["type"] == "pen":
                if len(annotation["points"]) < 2:
                    continue
                orig_points = [
                    (int(round(x * inv)), int(round(y * inv)))
                    for x, y in annotation["points"]
                ]
                draw.line(
                    orig_points,
                    fill=annotation["color"],
                    width=width,
                    joint="curve",
                )
            elif annotation["type"] in ("square", "circle"):
                bbox = tuple(
                    int(round(v * inv))
                    for v in annotation["bbox"]
                )
                if annotation["type"] == "square":
                    draw.rectangle(bbox, outline=annotation["color"], width=width)
                else:
                    draw.ellipse(bbox, outline=annotation["color"], width=width)
            elif annotation["type"] == "text":
                bbox = tuple(
                    int(round(v * inv))
                    for v in text_bbox(annotation)
                )
                font = load_font(max(1, int(round(annotation["font_size"] * inv))))
                wrapped_text = wrap_text_to_width(
                    annotation["text"],
                    font,
                    max(20, bbox[2] - bbox[0]),
                )
                draw.multiline_text(
                    (bbox[0], bbox[1]),
                    wrapped_text,
                    fill=annotation["color"],
                    font=font,
                    spacing=max(2, int(round(4 * inv))),
                )
        state["result"] = {
            "image": np.array(annotated),
            "metadata": build_metadata(inv),
        }
        state["done"] = True
        try:
            canvas._photo_ref = None
        except Exception:
            pass
        win.destroy()

    def cancel():
        if state["done"]:
            return
        close_inline_text_editor(commit=False)
        state["result"] = None
        state["done"] = True
        try:
            canvas._photo_ref = None
        except Exception:
            pass
        win.destroy()

    def check_queue():
        if state["done"]:
            return
        try:
            ev = event_queue.get_nowait()
        except _queue.Empty:
            win.after(100, check_queue)
            return
        if ev == commit_event:
            save_and_close()
            return
        # Drop any other event during annotation: F17/F20 etc. should not
        # interrupt the annotation flow.
        win.after(100, check_queue)

    canvas.bind("<Button-1>", on_down)
    canvas.bind("<Double-Button-1>", on_double_click)
    canvas.bind("<B1-Motion>", on_motion)
    canvas.bind("<ButtonRelease-1>", on_up)
    canvas.bind("<KeyPress>", on_inline_keypress)

    for key, name in (
        ("r", "red"),
        ("R", "red"),
        ("b", "blue"),
        ("B", "blue"),
        ("g", "green"),
        ("G", "green"),
        ("o", "orange"),
        ("O", "orange"),
        ("y", "yellow"),
        ("Y", "yellow"),
        ("w", "white"),
        ("W", "white"),
        ("k", "black"),
        ("K", "black"),
    ):
        win.bind(f"<KeyPress-{key}>", lambda _e, n=name: set_color(n))

    for key, name in (
        ("v", "select"),
        ("V", "select"),
        ("p", "pen"),
        ("P", "pen"),
        ("s", "square"),
        ("S", "square"),
        ("d", "square"),
        ("D", "square"),
        ("t", "text"),
        ("T", "text"),
    ):
        win.bind(f"<KeyPress-{key}>", lambda _e, n=name: set_tool(n))

    win.bind("<KeyPress-c>", lambda _e: clear_all())
    win.bind("<KeyPress-C>", lambda _e: clear_all())
    win.bind("<KeyPress-x>", lambda _e: clear_all())
    win.bind("<KeyPress-X>", lambda _e: clear_all())
    win.bind("<KeyPress-z>", lambda _e: undo())
    win.bind("<KeyPress-Z>", lambda _e: undo())
    win.bind("<Control-z>", lambda _e: undo())
    win.bind("<Control-Z>", lambda _e: undo())
    win.bind("<Control-y>", lambda _e: redo_action())
    win.bind("<Control-Y>", lambda _e: redo_action())
    win.bind("<Delete>", lambda _e: delete_selected())
    win.bind("<BackSpace>", lambda _e: delete_selected())
    win.bind("<Return>", lambda _e: edit_selected())
    win.bind("<plus>", lambda _e: inc_width(1))
    win.bind("<equal>", lambda _e: inc_width(1))
    win.bind("<minus>", lambda _e: inc_width(-1))
    win.bind("<Control-plus>", lambda _e: inc_font(1))
    win.bind("<Control-equal>", lambda _e: inc_font(1))
    win.bind("<Control-minus>", lambda _e: inc_font(-1))
    win.bind("<Escape>", lambda _e: cancel())
    win.protocol("WM_DELETE_WINDOW", cancel)

    update_toolbar()
    win.focus_force()
    win.grab_set()

    win.after(100, check_queue)
    root.wait_window(win)

    return state["result"]
