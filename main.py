"""Whisper Voice Input + Screen Capture — unified system-wide hotkeys.

Voice (push-to-talk):
    Hold   F21 (default) — speak — release: transcription pasted & copied.

Screen capture (tap-to-toggle):
    Tap    F20 — select region, tap F20 again — GIF + MP4 + frame grid saved.
    Tap    F17 — select region on a frozen snapshot (keeps dropdowns visible)
                  — PNG screenshot saved.

Every event is appended to <screen.output_dir>/activity.log so you can
review your day at a glance.

Usage:
    python main.py                         # use config.yaml
    python main.py --config my_config.yaml
"""

import os

# Add NVIDIA CUDA library paths (pip-installed) to DLL search path
try:
    import nvidia.cublas
    import nvidia.cudnn
    for pkg in (nvidia.cublas, nvidia.cudnn):
        dll_dir = os.path.join(pkg.__path__[0], "bin")
        if os.path.isdir(dll_dir):
            os.add_dll_directory(dll_dir)
            os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")
except ImportError:
    pass  # CUDA libs not installed, will use CPU


import argparse
import sys
import threading
import time
import winsound
from datetime import datetime
from pathlib import Path

# Per-monitor DPI awareness: Tk reports physical pixel coords that
# match mss.grab() — required for correct region selection under
# Windows display scaling.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from colorama import Fore, Style, init as colorama_init

from app.config import Config
from app.recorder import AudioRecorder
from app.transcriber import Transcriber
from app.inserter import TextInserter
from app.hotkey import HotkeyManager
from app.activity_log import get_logger
from app.screen import ScreenController


# ── UI helpers ──────────────────────────────────────────────────────────────

def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def print_status(msg: str, color: str = Fore.WHITE) -> None:
    print(f"  {color}[{timestamp()}]{Style.RESET_ALL} {msg}")


def print_banner(config: Config, device: str) -> None:
    gpu_icon = "✓ CUDA" if device == "cuda" else "CPU"
    print()
    print(f"  {Fore.CYAN}{'═' * 60}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}  Whisper Voice Input + Screen Capture{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 60}{Style.RESET_ALL}")
    print(f"    Model          : {Fore.YELLOW}{config.model_size}{Style.RESET_ALL}")
    print(f"    Language       : {Fore.YELLOW}{config.language}{Style.RESET_ALL}")
    print(f"    Device         : {Fore.YELLOW}{gpu_icon}{Style.RESET_ALL}")
    print(f"    Voice hotkey   : {Fore.GREEN}{config.hotkey.upper()}{Style.RESET_ALL} (hold to record)")
    if config.screen.enabled:
        print(f"    Video hotkey   : {Fore.GREEN}{config.screen.video_hotkey.upper()}{Style.RESET_ALL} (tap to toggle)")
        print(f"    Shot hotkey    : {Fore.GREEN}{config.screen.screenshot_hotkey.upper()}{Style.RESET_ALL} (tap)")
        print(f"    Output dir     : {Fore.YELLOW}{Path(config.screen.output_dir).expanduser()}{Style.RESET_ALL}")
    else:
        print(f"    Screen capture : {Fore.YELLOW}disabled{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 60}{Style.RESET_ALL}")
    print(f"    Press {Fore.RED}Ctrl+C{Style.RESET_ALL} to exit.")
    print(f"  {Fore.CYAN}{'═' * 60}{Style.RESET_ALL}")
    print()


# ── Voice beeps ─────────────────────────────────────────────────────────────

def voice_beep_start():
    try:
        winsound.Beep(800, 150)
    except Exception:
        pass


def voice_beep_stop():
    try:
        winsound.Beep(400, 150)
    except Exception:
        pass


def voice_beep_error():
    try:
        winsound.Beep(300, 100)
        winsound.Beep(300, 100)
    except Exception:
        pass


# ── Main application ────────────────────────────────────────────────────────

class App:
    def __init__(self, config: Config):
        self.config = config
        self.device = config.effective_device

        # Shared file logger — voice + screen events in one place.
        self.log = get_logger(Path(config.screen.output_dir).expanduser())

        # Voice subsystem
        self.audio_recorder = AudioRecorder(sample_rate=config.sample_rate)
        self.transcriber = Transcriber(
            model_size=config.model_size,
            device=self.device,
            compute_type=config.effective_compute_type,
            beam_size=config.beam_size,
            language=config.language,
        )
        self.inserter = TextInserter(mode=config.insert_mode)

        # Screen subsystem (optional)
        self.screen: ScreenController | None = None
        if config.screen.enabled:
            self.screen = ScreenController(config.screen, self.log)

        # Unified hotkey manager
        self.hotkey_mgr = HotkeyManager()
        self.hotkey_mgr.add_hold(
            config.hotkey,
            on_press=self._on_voice_press,
            on_release=self._on_voice_release,
        )
        if self.screen is not None:
            self.hotkey_mgr.add_toggle(
                config.screen.video_hotkey, self.screen.post_video_toggle,
            )
            self.hotkey_mgr.add_toggle(
                config.screen.screenshot_hotkey, self.screen.post_screenshot,
            )
        self.hotkey_mgr.set_tick(self.inserter.poll_and_paste)

        self._processing = False
        self._recording = False
        self._lock = threading.Lock()

    def run(self) -> None:
        colorama_init()

        print_status(
            f"Loading Whisper model '{self.config.model_size}'... "
            "(first run downloads ~1-3 GB)",
            Fore.YELLOW,
        )
        self.transcriber.load_model(
            on_progress=lambda msg: print_status(msg, Fore.YELLOW)
        )
        print_status("Model ready!", Fore.GREEN)

        if self.screen is not None:
            self.screen.start()

        print_banner(self.config, self.device)
        self.log.info(
            "READY        | voice=%s video=%s shot=%s",
            self.config.hotkey,
            self.config.screen.video_hotkey if self.screen else "-",
            self.config.screen.screenshot_hotkey if self.screen else "-",
        )

        self.hotkey_mgr.start()
        print_status("Listening...", Fore.GREEN)

        try:
            self.hotkey_mgr.wait()
        except KeyboardInterrupt:
            pass
        finally:
            print()
            print_status("Shutting down...", Fore.YELLOW)
            self.hotkey_mgr.stop()
            self.inserter.shutdown()
            if self.screen is not None:
                self.screen.stop()
            self.log.info("EXIT         | shutdown")
            print_status("Bye!", Fore.CYAN)

    # ── Voice callbacks ───────────────────────────────────────────────
    def _on_voice_press(self) -> None:
        with self._lock:
            if self._processing or self._recording:
                return
            self._recording = True

        self.inserter.capture_target_window()
        self.audio_recorder.start()

        if self.config.sound_feedback:
            threading.Thread(target=voice_beep_start, daemon=True).start()

        print_status(f"{Fore.RED}● Recording...{Style.RESET_ALL}")

    def _on_voice_release(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        if self.config.sound_feedback:
            threading.Thread(target=voice_beep_stop, daemon=True).start()

        threading.Thread(target=self._finalize_voice, daemon=True).start()

    def _finalize_voice(self) -> None:
        with self._lock:
            self._processing = True
        try:
            audio = self.audio_recorder.stop()
            if audio is None:
                print_status("No audio captured.", Fore.YELLOW)
                self.log.info("VOICE_EMPTY  | no audio captured")
                return

            duration = len(audio) / self.config.sample_rate
            if duration < self.config.min_duration:
                print_status(f"Too short ({duration:.1f}s), skipped.", Fore.YELLOW)
                self.log.info("VOICE_SKIP   | duration=%.2fs (below min)", duration)
                return

            print_status(
                f"{Fore.YELLOW}◆ Transcribing ({duration:.1f}s)...{Style.RESET_ALL}"
            )
            start = time.perf_counter()
            text = self.transcriber.transcribe(audio)
            elapsed = time.perf_counter() - start

            if text:
                self.inserter.paste(text)
                self.inserter.copy_to_clipboard(text)
                print_status(
                    f'{Fore.GREEN}✓{Style.RESET_ALL} '
                    f'"{Fore.WHITE}{text}{Style.RESET_ALL}" '
                    f'({elapsed:.1f}s) — pasted & copied',
                )
                self.log.info(
                    "VOICE_OK     | duration=%.2fs elapsed=%.2fs text=%r",
                    duration, elapsed, text,
                )
            else:
                print_status("Nothing recognized.", Fore.YELLOW)
                self.log.info("VOICE_EMPTY  | nothing recognized")

        except Exception as e:
            print_status(f"Transcription error: {e}", Fore.RED)
            self.log.error("VOICE_ERROR  | %s", e)
            if self.config.sound_feedback:
                voice_beep_error()

        finally:
            with self._lock:
                self._processing = False


# ── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Whisper Voice Input + Screen Capture"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    args = parser.parse_args()

    config = Config.load(args.config)
    app = App(config)
    app.run()


if __name__ == "__main__":
    main()
