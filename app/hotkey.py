"""Global hotkey manager using GetAsyncKeyState polling.

Works with any key from any window — no hooks, no message pump.
"""

import ctypes
import time
import threading
from typing import Callable

user32 = ctypes.windll.user32

# VK code mapping
VK_MAP = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "f13": 0x7C, "f14": 0x7D, "f15": 0x7E, "f16": 0x7F,
    "f17": 0x80, "f18": 0x81, "f19": 0x82, "f20": 0x83,
    "f21": 0x84, "f22": 0x85, "f23": 0x86, "f24": 0x87,
    "scroll lock": 0x91, "pause": 0x13, "insert": 0x2D,
    "caps lock": 0x14, "num lock": 0x90,
    "right ctrl": 0xA3, "left ctrl": 0xA2,
    "right alt": 0xA5, "left alt": 0xA4,
    "right shift": 0xA1, "left shift": 0xA0,
}


def _resolve_vk_code(hotkey: str) -> int:
    key = hotkey.strip().lower()
    if key in VK_MAP:
        return VK_MAP[key]
    raise ValueError(
        f"Unknown hotkey: {hotkey!r}. "
        f"Supported: {', '.join(sorted(VK_MAP.keys()))}"
    )


class HotkeyManager:
    """Global push-to-talk via GetAsyncKeyState polling.

    Polls key state every 10ms (~0% CPU). Works from ANY window,
    no hooks or message pump required.
    """

    def __init__(
        self,
        hotkey: str = "scroll lock",
        on_press: Callable[[], None] | None = None,
        on_release: Callable[[], None] | None = None,
        on_tick: Callable[[], None] | None = None,
        suppress: bool = True,
    ):
        self.vk_code = _resolve_vk_code(hotkey)
        self.on_press = on_press
        self.on_release = on_release
        self.on_tick = on_tick
        self.on_press = on_press
        self.on_release = on_release
        self._key_held = False
        self._running = True

    def start(self) -> None:
        """Nothing to install — polling starts in wait()."""
        pass

    def stop(self) -> None:
        self._running = False

    def wait(self) -> None:
        """Poll key state. Blocks until stop() or Ctrl+C."""
        try:
            while self._running:
                pressed = bool(user32.GetAsyncKeyState(self.vk_code) & 0x8000)

                if pressed and not self._key_held:
                    self._key_held = True
                    if self.on_press:
                        threading.Thread(
                            target=self.on_press, daemon=True
                        ).start()

                elif not pressed and self._key_held:
                    self._key_held = False
                    if self.on_release:
                        threading.Thread(
                            target=self.on_release, daemon=True
                        ).start()

                if self.on_tick:
                    self.on_tick()

                time.sleep(0.01)
        except KeyboardInterrupt:
            raise
