from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationPalette:
    border: str
    accent: str
    title_fg: str
    text_fg: str = "#111827"
    muted_fg: str = "#4b5563"


STATUS_PALETTES: dict[str, NotificationPalette] = {
    "done": NotificationPalette("#16a34a", "#dcfce7", "#15803d"),
    "failed": NotificationPalette("#dc2626", "#fee2e2", "#991b1b"),
}


class CommandNotifier:
    def __init__(
        self,
        default_popup_duration_ms: int = 4200,
    ) -> None:
        self._events: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = False
        self._root: tk.Tk | None = None
        self._popup_window: tk.Toplevel | None = None
        self._popup_close_after_id = None
        self._default_popup_duration_ms = max(1000, int(default_popup_duration_ms))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._events.put(("__shutdown__",))
        if self._worker is not None:
            self._worker.join(timeout=5.0)

    def set_default_popup_duration(self, ttl_ms: int) -> None:
        self._default_popup_duration_ms = max(1000, int(ttl_ms))

    def show_command_result(
        self,
        spoken_text: str,
        command_id: str | None,
        message: str,
        ok: bool,
    ) -> None:
        title = "Command Completed" if ok else "Command Failed"
        detail = f"Heard: {spoken_text}" if spoken_text else None
        self.show_event(
            title=title,
            message=message,
            ok=ok,
            subject_label="Command",
            subject=command_id or "unrecognized-command",
            detail=detail,
        )

    def show_event(
        self,
        title: str,
        message: str,
        ok: bool,
        subject_label: str | None = None,
        subject: str | None = None,
        detail: str | None = None,
        ttl_ms: int | None = None,
    ) -> None:
        self._events.put(
            ("show", title, message, ok, subject_label, subject, detail, ttl_ms)
        )

    def _run(self) -> None:
        self._root = tk.Tk()
        self._root.withdraw()
        try:
            while self._running:
                try:
                    event = self._events.get(timeout=0.1)
                except queue.Empty:
                    self._pump()
                    continue

                if event[0] == "__shutdown__":
                    break

                if event[0] == "show":
                    _, title, message, ok, subject_label, subject, detail, ttl_ms = event
                    palette = STATUS_PALETTES["done"] if ok else STATUS_PALETTES["failed"]
                    self._show_popup_window(
                        title=title,
                        message=message,
                        palette=palette,
                        subject_label=subject_label,
                        subject=subject,
                        detail=detail,
                        ttl_ms=ttl_ms,
                    )

                self._pump()
        finally:
            self._destroy_popup_window()
            if self._root is not None:
                try:
                    self._root.destroy()
                except Exception:
                    pass
            self._root = None

    def _pump(self) -> None:
        if self._root is None:
            return
        try:
            self._root.update()
        except Exception:
            pass

    def _show_popup_window(
        self,
        title: str,
        message: str,
        palette: NotificationPalette,
        subject_label: str | None = None,
        subject: str | None = None,
        detail: str | None = None,
        ttl_ms: int | None = None,
    ) -> None:
        if self._root is None:
            return

        self._destroy_popup_window()

        window = tk.Toplevel(self._root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=palette.border)

        outer = tk.Frame(window, bg=palette.border, padx=3, pady=3)
        outer.pack(fill="both", expand=True)

        body = tk.Frame(outer, bg=palette.accent)
        body.pack(fill="both", expand=True)

        for widget in (window, outer, body):
            widget.bind("<Button-1>", lambda _event: self._destroy_popup_window())

        tk.Label(
            body,
            text=title,
            font=("Segoe UI", 12, "bold"),
            fg=palette.title_fg,
            bg=palette.accent,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 4))

        if subject:
            subject_text = (
                f"{subject_label}: {subject}" if subject_label else subject
            )
            tk.Label(
                body,
                text=subject_text,
                font=("Consolas", 11, "bold"),
                fg=palette.text_fg,
                bg=palette.accent,
                anchor="w",
                justify="left",
                wraplength=560,
            ).pack(fill="x", padx=14)

        if detail:
            tk.Label(
                body,
                text=detail,
                font=("Segoe UI", 10),
                fg=palette.muted_fg,
                bg=palette.accent,
                anchor="w",
                justify="left",
                wraplength=560,
            ).pack(fill="x", padx=14, pady=(6, 0))

        tk.Label(
            body,
            text=message,
            font=("Segoe UI", 10),
            fg=palette.text_fg,
            bg=palette.accent,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(fill="x", padx=14, pady=(8, 14))

        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        x = max(16, screen_w - width - 28)
        y = max(16, screen_h - height - 56)
        window.geometry(f"+{x}+{y}")
        window.deiconify()

        self._popup_window = window
        ttl = self._default_popup_duration_ms if ttl_ms is None else max(1000, int(ttl_ms))
        self._popup_close_after_id = window.after(ttl, self._destroy_popup_window)

    def _destroy_popup_window(self) -> None:
        if self._popup_window is None:
            return
        try:
            if self._popup_close_after_id is not None:
                self._popup_window.after_cancel(self._popup_close_after_id)
        except Exception:
            pass
        self._popup_close_after_id = None
        try:
            self._popup_window.destroy()
        except Exception:
            pass
        self._popup_window = None
