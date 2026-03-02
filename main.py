"""Whisper Voice Input — system-wide push-to-talk voice typing.

Hold the configured hotkey, speak in Russian, release — text is pasted
into the active window (terminal, editor, browser, etc.).

Usage:
    python main.py              # Use config.yaml
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

from colorama import Fore, Style, init as colorama_init

from app.config import Config
from app.recorder import AudioRecorder
from app.transcriber import Transcriber
from app.inserter import TextInserter
from app.hotkey import HotkeyManager


# ── UI helpers ──────────────────────────────────────────────────────────────

def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def print_status(msg: str, color: str = Fore.WHITE) -> None:
    print(f"  {color}[{timestamp()}]{Style.RESET_ALL} {msg}")


def print_banner(config: Config, device: str) -> None:
    gpu_icon = "✓ CUDA" if device == "cuda" else "CPU"
    print()
    print(f"  {Fore.CYAN}{'═' * 50}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}  Whisper Voice Input{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")
    print(f"    Model    : {Fore.YELLOW}{config.model_size}{Style.RESET_ALL}")
    print(f"    Language : {Fore.YELLOW}{config.language}{Style.RESET_ALL}")
    print(f"    Device   : {Fore.YELLOW}{gpu_icon}{Style.RESET_ALL}")
    print(f"    Hotkey   : {Fore.GREEN}{config.hotkey.upper()}{Style.RESET_ALL} (hold to record)")
    print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")
    print(f"    Hold [{Fore.GREEN}{config.hotkey.upper()}{Style.RESET_ALL}] and speak, release to transcribe.")
    print(f"    Press {Fore.RED}Ctrl+C{Style.RESET_ALL} to exit.")
    print(f"  {Fore.CYAN}{'═' * 50}{Style.RESET_ALL}")
    print()


# ── Beep feedback ───────────────────────────────────────────────────────────

def beep_start() -> None:
    """Short high beep — recording started."""
    try:
        winsound.Beep(800, 150)
    except Exception:
        pass


def beep_stop() -> None:
    """Short low beep — recording stopped."""
    try:
        winsound.Beep(400, 150)
    except Exception:
        pass


def beep_error() -> None:
    """Two short beeps — error."""
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
        self.recorder = AudioRecorder(sample_rate=config.sample_rate)
        self.transcriber = Transcriber(
            model_size=config.model_size,
            device=self.device,
            compute_type=config.effective_compute_type,
            beam_size=config.beam_size,
            language=config.language,
        )
        self.inserter = TextInserter(mode=config.insert_mode)
        self.hotkey_mgr = HotkeyManager(
            hotkey=config.hotkey,
            on_press=self._on_key_press,
            on_release=self._on_key_release,
            on_tick=self.inserter.poll_and_paste,
        )
        self._processing = False
        self._recording = False
        self._lock = threading.Lock()

    def run(self) -> None:
        """Start the application."""
        colorama_init()

        print_status(
            f"Loading Whisper model '{self.config.model_size}'... (first run downloads ~1-3 GB)",
            Fore.YELLOW,
        )
        self.transcriber.load_model(on_progress=lambda msg: print_status(msg, Fore.YELLOW))
        print_status("Model ready!", Fore.GREEN)

        print_banner(self.config, self.device)

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
            print_status("Bye!", Fore.CYAN)

    def _on_key_press(self) -> None:
        """Called when push-to-talk key is pressed."""
        with self._lock:
            if self._processing or self._recording:
                return
            self._recording = True

        self.inserter.capture_target_window()
        self.recorder.start()

        if self.config.sound_feedback:
            threading.Thread(target=beep_start, daemon=True).start()

        print_status(f"{Fore.RED}● Recording...{Style.RESET_ALL}")

    def _on_key_release(self) -> None:
        """Called when push-to-talk key is released."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        if self.config.sound_feedback:
            threading.Thread(target=beep_stop, daemon=True).start()

        # Final transcription in background
        threading.Thread(target=self._finalize, daemon=True).start()

    def _finalize(self) -> None:
        """Stop recording, transcribe full audio, paste result."""
        with self._lock:
            self._processing = True

        try:
            audio = self.recorder.stop()

            if audio is None:
                print_status("No audio captured.", Fore.YELLOW)
                return

            duration = len(audio) / self.config.sample_rate
            if duration < self.config.min_duration:
                print_status(
                    f"Too short ({duration:.1f}s), skipped.", Fore.YELLOW
                )
                return

            print_status(
                f"{Fore.YELLOW}◆ Transcribing ({duration:.1f}s)...{Style.RESET_ALL}"
            )
            start = time.perf_counter()
            full_text = self.transcriber.transcribe(audio)
            elapsed = time.perf_counter() - start

            if full_text:
                self.inserter.paste(full_text)
                self.inserter.copy_to_clipboard(full_text)
                print_status(
                    f'{Fore.GREEN}✓{Style.RESET_ALL} "{Fore.WHITE}{full_text}{Style.RESET_ALL}" '
                    f'({elapsed:.1f}s) — pasted & copied to clipboard',
                )
            else:
                print_status("Nothing recognized.", Fore.YELLOW)

        except Exception as e:
            print_status(f"Transcription error: {e}", Fore.RED)
            if self.config.sound_feedback:
                beep_error()

        finally:
            with self._lock:
                self._processing = False

# ── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Whisper Voice Input")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    args = parser.parse_args()

    config = Config.load(args.config)
    app = App(config)
    app.run()


if __name__ == "__main__":
    main()
