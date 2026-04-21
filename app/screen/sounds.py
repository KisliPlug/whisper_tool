"""Short audio feedback for screen capture events.

winsound.Beep on Windows; terminal bell fallback elsewhere.
The combined app is Windows-only (GetAsyncKeyState), so the fallback
is effectively a courtesy.
"""

import sys


def _beep(pairs):
    if sys.platform == "win32":
        try:
            import winsound
            for freq, ms in pairs:
                winsound.Beep(freq, ms)
        except Exception:
            pass
    else:
        print("\a", end="", flush=True)


def video_start():
    _beep([(660, 80), (988, 100)])


def video_stop():
    _beep([(988, 70), (660, 110)])


def shutter():
    _beep([(1500, 40)])
