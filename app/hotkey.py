"""Global hotkey manager using GetAsyncKeyState polling.

Supports multiple keys simultaneously. Each binding is either:
  * "hold" — push-to-talk; fires on_press on key-down edge, on_release on up
  * "toggle" — tap-to-toggle; fires on_tap on every key-down edge

Polls all bindings every 10 ms on a single thread. Callbacks are run
on fresh daemon threads so a long-running handler (e.g. waiting on a
selection overlay) never stalls key detection.
"""

import ctypes
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

user32 = ctypes.windll.user32


VK_MAP = {
    "f1":  0x70, "f2":  0x71, "f3":  0x72, "f4":  0x73,
    "f5":  0x74, "f6":  0x75, "f7":  0x76, "f8":  0x77,
    "f9":  0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "f13": 0x7C, "f14": 0x7D, "f15": 0x7E, "f16": 0x7F,
    "f17": 0x80, "f18": 0x81, "f19": 0x82, "f20": 0x83,
    "f21": 0x84, "f22": 0x85, "f23": 0x86, "f24": 0x87,
    "scroll lock": 0x91, "pause": 0x13, "insert": 0x2D,
    "caps lock":   0x14, "num lock": 0x90,
    "right ctrl":  0xA3, "left ctrl":  0xA2,
    "right alt":   0xA5, "left alt":   0xA4,
    "right shift": 0xA1, "left shift": 0xA0,
}


def _resolve_vk(hotkey: str) -> int:
    key = hotkey.strip().lower()
    if key in VK_MAP:
        return VK_MAP[key]
    raise ValueError(
        f"Unknown hotkey: {hotkey!r}. "
        f"Supported: {', '.join(sorted(VK_MAP.keys()))}"
    )


@dataclass
class _Binding:
    vk: int
    name: str
    mode: str  # "hold" or "toggle"
    on_press: Optional[Callable[[], None]] = None
    on_release: Optional[Callable[[], None]] = None
    on_tap: Optional[Callable[[], None]] = None
    held: bool = False


class HotkeyManager:
    def __init__(self):
        self._bindings: list[_Binding] = []
        self._tick: Optional[Callable[[], None]] = None
        self._running = True

    def add_hold(self, hotkey: str,
                 on_press: Callable[[], None],
                 on_release: Callable[[], None]) -> None:
        vk = _resolve_vk(hotkey)
        self._reject_duplicate(vk, hotkey)
        self._bindings.append(_Binding(
            vk=vk, name=hotkey, mode="hold",
            on_press=on_press, on_release=on_release,
        ))

    def add_toggle(self, hotkey: str, on_tap: Callable[[], None]) -> None:
        vk = _resolve_vk(hotkey)
        self._reject_duplicate(vk, hotkey)
        self._bindings.append(_Binding(
            vk=vk, name=hotkey, mode="toggle", on_tap=on_tap,
        ))

    def set_tick(self, fn: Callable[[], None]) -> None:
        """Called once per poll (~10 ms). Use for cooperative work
        (e.g. clipboard-paste pumping in TextInserter)."""
        self._tick = fn

    def _reject_duplicate(self, vk: int, name: str) -> None:
        for b in self._bindings:
            if b.vk == vk:
                raise ValueError(
                    f"Hotkey conflict: {name!r} is already bound to "
                    f"{b.name!r} (mode={b.mode})"
                )

    def start(self) -> None:
        pass  # polling starts in wait()

    def stop(self) -> None:
        self._running = False

    def wait(self) -> None:
        try:
            while self._running:
                for b in self._bindings:
                    pressed = bool(user32.GetAsyncKeyState(b.vk) & 0x8000)
                    if pressed and not b.held:
                        b.held = True
                        if b.mode == "hold" and b.on_press:
                            threading.Thread(target=b.on_press, daemon=True).start()
                        elif b.mode == "toggle" and b.on_tap:
                            threading.Thread(target=b.on_tap, daemon=True).start()
                    elif not pressed and b.held:
                        b.held = False
                        if b.mode == "hold" and b.on_release:
                            threading.Thread(target=b.on_release, daemon=True).start()

                if self._tick:
                    self._tick()

                time.sleep(0.01)
        except KeyboardInterrupt:
            raise
