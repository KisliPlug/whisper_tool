"""Global hotkey manager using GetAsyncKeyState polling.

Supports multiple keys simultaneously and modifier combinations via
`+`-separated specs (e.g. `"ctrl+f17"`, `"ctrl+shift+f17"`). Each
binding is either:
  * "hold" — push-to-talk; fires on_press on key-down edge, on_release on up
  * "toggle" — tap-to-toggle; fires on_tap on every key-down edge

Edge detection is per *main key only*. When the main key's down-edge
fires, the binding whose tracked-modifier set (ctrl / alt / shift / win)
exactly matches the currently held modifiers is armed. Releasing or
changing modifiers mid-hold does NOT re-fire a different binding, and
it does NOT cancel an already-armed hold — `on_release` still runs when
the main key goes up. That prevents accidental double-fires when e.g.
the user lets go of Ctrl before F17.

Polls all bindings every 10 ms on a single thread. Callbacks run on
fresh daemon threads, each wrapped in `_safe_call` so an exception in
user code logs with full traceback instead of killing the daemon
silently.
"""

import ctypes
import logging
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

user32 = ctypes.windll.user32


# Main-key VKs the user can bind. Modifiers live in MOD_MAP so we can
# reject them as main keys (e.g. "ctrl" alone is not a valid binding).
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

# Canonical VK for the four tracked modifier families. Each maps to the
# "either-side" VK (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN). For Win we
# also poll RWIN (0x5C) but normalize matches to 0x5B so users don't
# have to pick a side in config.
MOD_MAP = {
    "ctrl": 0x11, "control": 0x11,
    "alt":  0x12, "menu":    0x12,
    "shift": 0x10,
    "win":  0x5B, "super":   0x5B, "meta": 0x5B,
}

_TRACKED_MODIFIER_VKS = (0x11, 0x12, 0x10, 0x5B, 0x5C)


def _current_modifier_set() -> frozenset[int]:
    """Snapshot which tracked modifier families are currently held."""
    held: set[int] = set()
    if user32.GetAsyncKeyState(0x11) & 0x8000:
        held.add(0x11)
    if user32.GetAsyncKeyState(0x12) & 0x8000:
        held.add(0x12)
    if user32.GetAsyncKeyState(0x10) & 0x8000:
        held.add(0x10)
    if (user32.GetAsyncKeyState(0x5B) & 0x8000) or (
        user32.GetAsyncKeyState(0x5C) & 0x8000
    ):
        held.add(0x5B)
    return frozenset(held)


def _resolve_hotkey(spec: str) -> tuple[int, frozenset[int]]:
    """Parse a hotkey spec like `"ctrl+f17"` into (main_vk, mod_vks).

    - Last `+`-separated token is the main key and must be in VK_MAP.
    - All preceding tokens must be in MOD_MAP.
    - `frozenset` so bindings are directly comparable as dict keys.
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"empty hotkey spec: {spec!r}")
    main_part = parts[-1]
    mod_parts = parts[:-1]
    if main_part not in VK_MAP:
        raise ValueError(
            f"Unknown main key {main_part!r} in {spec!r}. "
            f"Supported: {', '.join(sorted(VK_MAP.keys()))}"
        )
    mod_vks: set[int] = set()
    for m in mod_parts:
        if m not in MOD_MAP:
            raise ValueError(
                f"Unknown modifier {m!r} in {spec!r}. "
                f"Supported modifiers: {', '.join(sorted(set(MOD_MAP.keys())))}"
            )
        mod_vks.add(MOD_MAP[m])
    return VK_MAP[main_part], frozenset(mod_vks)


@dataclass
class _Binding:
    vk: int
    mod_vks: frozenset[int]
    name: str
    mode: str  # "hold" or "toggle"
    on_press: Optional[Callable[[], None]] = None
    on_release: Optional[Callable[[], None]] = None
    on_tap: Optional[Callable[[], None]] = None


class HotkeyManager:
    def __init__(self, log: Optional[logging.Logger] = None):
        self._bindings: list[_Binding] = []
        self._tick: Optional[Callable[[], None]] = None
        self._running = True
        self._log = log

    def _safe_call(self, fn: Callable[[], None], hotkey: str, event: str) -> None:
        """Run a hotkey callback, logging any exception with full trace.

        Without this, an error inside a callback would kill the daemon
        thread silently and the app would appear to "stop responding"
        to that hotkey with no clue in the log.
        """
        try:
            fn()
        except Exception:
            tb = traceback.format_exc()
            if self._log is not None:
                try:
                    self._log.error(
                        "HOTKEY_EXC   | hotkey=%s event=%s\n%s",
                        hotkey, event, tb.rstrip(),
                    )
                except Exception:
                    pass
            sys.stderr.write(
                f"\n[HOTKEY_EXC {hotkey} {event}]\n{tb}\n"
            )

    def add_hold(self, hotkey: str,
                 on_press: Callable[[], None],
                 on_release: Callable[[], None]) -> None:
        vk, mod_vks = _resolve_hotkey(hotkey)
        self._reject_duplicate(vk, mod_vks, hotkey)
        self._bindings.append(_Binding(
            vk=vk, mod_vks=mod_vks, name=hotkey, mode="hold",
            on_press=on_press, on_release=on_release,
        ))

    def add_toggle(self, hotkey: str, on_tap: Callable[[], None]) -> None:
        vk, mod_vks = _resolve_hotkey(hotkey)
        self._reject_duplicate(vk, mod_vks, hotkey)
        self._bindings.append(_Binding(
            vk=vk, mod_vks=mod_vks, name=hotkey, mode="toggle", on_tap=on_tap,
        ))

    def set_tick(self, fn: Callable[[], None]) -> None:
        """Called once per poll (~10 ms). Use for cooperative work
        (e.g. clipboard-paste pumping in TextInserter)."""
        self._tick = fn

    def _reject_duplicate(self, vk: int, mod_vks: frozenset[int], name: str) -> None:
        for b in self._bindings:
            if b.vk == vk and b.mod_vks == mod_vks:
                raise ValueError(
                    f"Hotkey conflict: {name!r} is already bound to "
                    f"{b.name!r} (mode={b.mode})"
                )

    def start(self) -> None:
        pass  # polling starts in wait()

    def stop(self) -> None:
        self._running = False

    def wait(self) -> None:
        # Edge detection runs on the *main key only*. At each main-key
        # down edge we pick the binding whose mod_vks matches the
        # currently held modifier set — that binding is "armed" until
        # the main key comes up, even if the user releases modifiers
        # mid-hold.
        main_held: dict[int, bool] = {}
        armed: dict[int, _Binding] = {}
        for b in self._bindings:
            main_held.setdefault(b.vk, False)

        try:
            while self._running:
                try:
                    mods_now = _current_modifier_set()
                    for vk, was_held in list(main_held.items()):
                        is_pressed = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                        if is_pressed and not was_held:
                            main_held[vk] = True
                            match = self._match_binding(vk, mods_now)
                            if match is not None:
                                armed[vk] = match
                                if match.mode == "hold" and match.on_press:
                                    threading.Thread(
                                        target=self._safe_call,
                                        args=(match.on_press, match.name, "press"),
                                        daemon=True,
                                    ).start()
                                elif match.mode == "toggle" and match.on_tap:
                                    threading.Thread(
                                        target=self._safe_call,
                                        args=(match.on_tap, match.name, "tap"),
                                        daemon=True,
                                    ).start()
                        elif not is_pressed and was_held:
                            main_held[vk] = False
                            match = armed.pop(vk, None)
                            if (match is not None
                                    and match.mode == "hold"
                                    and match.on_release):
                                threading.Thread(
                                    target=self._safe_call,
                                    args=(match.on_release, match.name, "release"),
                                    daemon=True,
                                ).start()

                    if self._tick:
                        try:
                            self._tick()
                        except Exception:
                            if self._log is not None:
                                try:
                                    self._log.exception(
                                        "TICK_EXC     | tick callback raised"
                                    )
                                except Exception:
                                    pass

                    time.sleep(0.01)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    # Don't let one poll iteration crash kill the whole
                    # hotkey thread — log with traceback and keep going.
                    if self._log is not None:
                        try:
                            self._log.exception(
                                "POLL_EXC     | hotkey poll iteration raised, continuing"
                            )
                        except Exception:
                            pass
                    else:
                        sys.stderr.write(
                            f"\n[POLL_EXC]\n{traceback.format_exc()}\n"
                        )
                    time.sleep(0.1)
        except KeyboardInterrupt:
            raise

    def _match_binding(
        self, vk: int, mods_now: frozenset[int],
    ) -> Optional[_Binding]:
        """Pick the binding whose (vk, mod_vks) matches the press event.

        Exact modifier-set equality: `f17` binding only fires when no
        tracked mods are held, `ctrl+f17` only when exactly ctrl is
        held (no shift/alt/win). Prefers longer mod sets if multiple
        match (shouldn't happen with exact equality, but defensive).
        """
        best: Optional[_Binding] = None
        for b in self._bindings:
            if b.vk == vk and b.mod_vks == mods_now:
                if best is None or len(b.mod_vks) > len(best.mod_vks):
                    best = b
        return best
