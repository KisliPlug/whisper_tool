"""Prevent other apps from stealing the foreground window.

Works by cranking Windows' `SPI_SETFOREGROUNDLOCKTIMEOUT` to a huge
value. Once set, apps calling `SetForegroundWindow` get their call
downgraded to a taskbar flash unless the user has interacted with them
within the timeout — effectively killing Revit's "jump in front of
whatever you're doing" behaviour.

Not bulletproof: apps that call `AllowSetForegroundWindow` on
themselves or use the `keybd_event` hack can still steal focus. Works
for most real-world offenders (Revit, Teams popup, installers).

Requires no admin rights. The setting is process-wide-global (it mutates
the user's Windows profile via SPI_SETFOREGROUNDLOCKTIMEOUT) so we
always snapshot the previous value on lock and restore it on unlock.
`restore_if_locked()` is meant for the App shutdown path — if Whisper
crashes after locking, on next run it'll still be locked until you
tap the toggle again or run `restore_if_locked()` once.
"""

import ctypes
import threading
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)

# From winuser.h
SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
SPIF_SENDCHANGE = 0x0002

# A large-but-reasonable value. 0xFFFFFFFF would work too; 10 minutes
# is long enough that no unattended foreground steal can slip through
# during an active session.
_LOCK_TIMEOUT_MS = 600_000
# Windows default at install time. Stored for fallback if we never
# saw a "before" value.
_WINDOWS_DEFAULT_MS = 200_000

_SystemParametersInfoW = _user32.SystemParametersInfoW
_SystemParametersInfoW.argtypes = [
    wintypes.UINT,    # uiAction
    wintypes.UINT,    # uiParam
    wintypes.LPVOID,  # pvParam
    wintypes.UINT,    # fWinIni
]
_SystemParametersInfoW.restype = wintypes.BOOL


_lock = threading.Lock()
_locked = False
_prev_timeout_ms: int | None = None


def _get_timeout_ms() -> int:
    ctypes.set_last_error(0)
    out = wintypes.DWORD(0)
    ok = _SystemParametersInfoW(
        SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(out), 0,
    )
    if not ok:
        raise OSError(
            f"SystemParametersInfo(GET) failed, "
            f"LastError={ctypes.get_last_error()}"
        )
    return int(out.value)


def _set_timeout_ms(ms: int) -> None:
    # For SET, pvParam carries the value itself cast to LPVOID — not
    # a pointer. Passing the int directly through ctypes works because
    # the LPVOID arg accepts integer values as raw pointer-sized data.
    ctypes.set_last_error(0)
    ok = _SystemParametersInfoW(
        SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(ms), SPIF_SENDCHANGE,
    )
    if not ok:
        last_error = ctypes.get_last_error()
        hint = ""
        if last_error == 0:
            hint = (
                " Windows can reject this call when the process is not allowed "
                "to change the foreground window."
            )
        raise OSError(
            f"SystemParametersInfo(SET, {ms}) failed, "
            f"LastError={last_error}.{hint}"
        )


def is_locked() -> bool:
    with _lock:
        return _locked


def ensure_locked() -> tuple[int, int, bool]:
    """Enable protection if needed.

    Returns `(previous_ms, current_ms, adopted_existing)`.
    """
    global _locked, _prev_timeout_ms
    with _lock:
        if _locked:
            return (_prev_timeout_ms or -1, _LOCK_TIMEOUT_MS, False)
        current = _get_timeout_ms()
        if current >= _LOCK_TIMEOUT_MS:
            _prev_timeout_ms = _WINDOWS_DEFAULT_MS
            _locked = True
            return (_prev_timeout_ms, current, True)
        _prev_timeout_ms = current
        _set_timeout_ms(_LOCK_TIMEOUT_MS)
        _locked = True
        return (_prev_timeout_ms, _LOCK_TIMEOUT_MS, False)


def lock() -> tuple[int, int]:
    """Enable protection. Returns (previous_ms, new_ms)."""
    global _locked, _prev_timeout_ms
    with _lock:
        if _locked:
            # Idempotent — don't overwrite _prev_timeout_ms on double-lock
            return (_prev_timeout_ms or -1, _LOCK_TIMEOUT_MS)
        _prev_timeout_ms = _get_timeout_ms()
        _set_timeout_ms(_LOCK_TIMEOUT_MS)
        _locked = True
        return (_prev_timeout_ms, _LOCK_TIMEOUT_MS)


def unlock() -> tuple[int, int]:
    """Disable protection, restoring the previous timeout. Returns
    (before_unlock_ms, restored_ms)."""
    global _locked, _prev_timeout_ms
    with _lock:
        if not _locked:
            return (_get_timeout_ms(), _get_timeout_ms())
        target = _prev_timeout_ms if _prev_timeout_ms is not None else _WINDOWS_DEFAULT_MS
        before = _get_timeout_ms()
        _set_timeout_ms(target)
        _locked = False
        _prev_timeout_ms = None
        return (before, target)


def toggle() -> tuple[bool, int, int]:
    """Flip state. Returns (new_state_locked, before_ms, after_ms)."""
    if is_locked():
        before, after = unlock()
        return (False, before, after)
    before, after = lock()
    return (True, before, after)


def restore_if_locked() -> None:
    """Safe to call from shutdown path — no-op if not locked by us."""
    if is_locked():
        unlock()
