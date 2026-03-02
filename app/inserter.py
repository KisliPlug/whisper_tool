"""Text inserter — pastes text into the active application.

Uses clipboard + keybd_event(Ctrl+V) to paste into whichever window
currently has keyboard focus.  No SetForegroundWindow, no COM, no
subprocesses — just the simplest possible Win32 keystroke injection.
"""

import ctypes
import time
from queue import Queue, Empty

import pyperclip

user32 = ctypes.windll.user32

VK_SHIFT = 0x10
VK_INSERT = 0x2D
KEYEVENTF_KEYUP = 0x0002


def _send_paste() -> None:
    """Shift+Insert via keybd_event — sends to the current foreground window.

    Shift+Insert is more universal than Ctrl+V: works in GUI apps
    (VS Code, Notepad) AND terminal TUI apps (OpenCode, Claude CLI)
    where Ctrl+V is often intercepted by the app itself.
    """
    user32.keybd_event(VK_SHIFT, 0, 0, 0)               # Shift down
    user32.keybd_event(VK_INSERT, 0, 0, 0)              # Insert down
    user32.keybd_event(VK_INSERT, 0, KEYEVENTF_KEYUP, 0) # Insert up
    user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)  # Shift up


class TextInserter:
    """Inserts text into the target window via clipboard + Ctrl+V.

    Background threads enqueue text via paste().
    The main thread calls poll_and_paste() on each tick to dequeue,
    copy text to clipboard, and send Ctrl+V via keybd_event.
    In real usage the target app stays in focus the entire time
    (user presses hotkey → speaks → releases), so keybd_event
    goes to the right window without any SetForegroundWindow tricks.
    """

    def __init__(self, mode: str = "clipboard", char_delay: float = 0.005):
        self.mode = mode
        self.char_delay = char_delay
        self._queue: Queue[str] = Queue()

    def capture_target_window(self) -> None:
        """No-op — keybd_event targets the current foreground window."""
        pass

    def paste(self, text: str) -> bool:
        """Enqueue text for pasting (called from any thread)."""
        if not text:
            return False
        self._queue.put(text)
        return True

    def copy_to_clipboard(self, text: str) -> None:
        """Just copy text to clipboard without pasting (for final full text)."""
        if text:
            pyperclip.copy(text)

    def poll_and_paste(self) -> None:
        """Paste ONE pending item.  Called from main thread every ~10ms."""
        try:
            text = self._queue.get_nowait()
        except Empty:
            return

        try:
            pyperclip.copy(text)
            time.sleep(0.02)
            _send_paste()
            time.sleep(0.05)
        except Exception as e:
            print(f"Paste error: {e}")

    def shutdown(self) -> None:
        """No-op — no subprocess to terminate."""
        pass
