"""Whisper Voice Input + Screen Capture — unified system-wide hotkeys.

Voice (push-to-talk):
    Hold   F21 (default) — speak — release: transcription pasted & copied.
    Hold   F19 (default) — speak a command — release: command executed.
                            Transcribed with language forced to English
                            (see config `command_language`). Example:
                            "task kill revit" → kills all revit* processes.

Screen capture (tap-to-toggle):
    Tap    F20 — select region, tap F20 again — GIF + MP4 + frame grid saved.
    Tap    F17 — plain screenshot: select region on a frozen snapshot,
                  PNG saved + path copied to clipboard immediately.
    Tap    F18 — annotated screenshot: select region, then draw strokes
                  in the annotator window; tap F18 again to save & close.

OS actions (tap-to-toggle):
    Tap    Ctrl+F17 — focus-steal lock: Windows stops apps (e.g. Revit)
                       from yanking foreground away. Tap again to restore.

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
import platform
import sys
import threading
import time
import traceback
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
from app import commands
from app import focus_lock
from app.notifier import CommandNotifier


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
    if config.language == "auto" and config.allowed_languages:
        print(f"    Allowed langs  : {Fore.YELLOW}{', '.join(config.allowed_languages)}{Style.RESET_ALL}")
    print(f"    Device         : {Fore.YELLOW}{gpu_icon}{Style.RESET_ALL}")
    print(f"    Voice hotkey   : {Fore.GREEN}{config.hotkey.upper()}{Style.RESET_ALL} (hold to record)")
    print(f"    Command hotkey : {Fore.GREEN}{config.command_hotkey.upper()}{Style.RESET_ALL} (hold, speak a command, release)")
    print(f"    Focus-lock     : {Fore.GREEN}{config.focus_lock_hotkey.upper()}{Style.RESET_ALL} (tap to block focus-steal, tap again to release)")
    if config.focus_lock_auto_enable:
        print(f"    Focus-lock auto: {Fore.GREEN}enabled{Style.RESET_ALL} on startup")
    if config.screen.enabled:
        print(f"    Video hotkey   : {Fore.GREEN}{config.screen.video_hotkey.upper()}{Style.RESET_ALL} (tap to toggle)")
        print(f"    Shot hotkey    : {Fore.GREEN}{config.screen.screenshot_hotkey.upper()}{Style.RESET_ALL} (tap — plain)")
        print(f"    Shot+edit      : {Fore.GREEN}{config.screen.screenshot_edit_hotkey.upper()}{Style.RESET_ALL} (tap, draw, tap again)")
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


# ── Diagnostic hooks ────────────────────────────────────────────────────────

def install_exception_hooks(log) -> None:
    """Route every uncaught exception — main thread or daemon — to `log`.

    Without this, exceptions in background threads (audio finalize,
    hotkey callbacks, screen worker) die silently: the thread stops,
    the user sees nothing. `threading.excepthook` catches those. The
    `sys.excepthook` override logs anything that escapes the main loop
    before re-delegating to the original handler so the console still
    shows the traceback.
    """
    original_sys_hook = sys.excepthook

    def _sys_hook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            try:
                log.critical("UNCAUGHT     | main thread\n%s", tb.rstrip())
            except Exception:
                pass
        original_sys_hook(exc_type, exc_value, exc_tb)

    def _thread_hook(args):
        if issubclass(args.exc_type, (SystemExit, KeyboardInterrupt)):
            return
        tb = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback
        ))
        thread_name = args.thread.name if args.thread is not None else "?"
        try:
            log.critical(
                "UNCAUGHT     | thread=%s\n%s", thread_name, tb.rstrip(),
            )
        except Exception:
            pass
        sys.stderr.write(f"\n[UNCAUGHT thread={thread_name}]\n{tb}\n")

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


# ── Main application ────────────────────────────────────────────────────────

class App:
    def __init__(self, config: Config):
        self.config = config
        self.device = config.effective_device

        # Shared file logger — voice + screen events in one place.
        self.log = get_logger(Path(config.screen.output_dir).expanduser())

        # Voice subsystem
        self.audio_recorder = AudioRecorder(
            sample_rate=config.sample_rate,
            device=config.input_device,
            keepalive_ms=config.input_keepalive_ms,
            log=self.log,
        )
        self.transcriber = Transcriber(
            model_size=config.model_size,
            device=self.device,
            compute_type=config.effective_compute_type,
            beam_size=config.beam_size,
            language=config.language,
            allowed_languages=config.allowed_languages,
        )
        self.inserter = TextInserter(mode=config.insert_mode)
        self.notifier = CommandNotifier()

        # Screen subsystem (optional)
        self.screen: ScreenController | None = None
        if config.screen.enabled:
            self.screen = ScreenController(
                config.screen,
                self.log,
                notifier=self.notifier,
            )

        # Unified hotkey manager
        self.hotkey_mgr = HotkeyManager(log=self.log)
        self.hotkey_mgr.add_hold(
            config.hotkey,
            on_press=self._on_voice_press,
            on_release=self._on_voice_release,
        )
        self.hotkey_mgr.add_hold(
            config.command_hotkey,
            on_press=self._on_command_press,
            on_release=self._on_command_release,
        )
        self.hotkey_mgr.add_toggle(
            config.focus_lock_hotkey, self._on_focus_lock_toggle,
        )
        if self.screen is not None:
            self.hotkey_mgr.add_toggle(
                config.screen.video_hotkey, self.screen.post_video_toggle,
            )
            self.hotkey_mgr.add_toggle(
                config.screen.screenshot_hotkey, self.screen.post_screenshot,
            )
            self.hotkey_mgr.add_toggle(
                config.screen.screenshot_edit_hotkey,
                self.screen.post_screenshot_edit,
            )
        self.hotkey_mgr.set_tick(self.inserter.poll_and_paste)

        self._processing = False
        self._recording = False
        # "voice" | "command" | None — distinguishes which release handler
        # should fire when the shared audio_recorder stops.
        self._recording_mode: str | None = None
        self._lock = threading.Lock()

    def _shorten(self, text: str, limit: int = 220) -> str:
        text = " ".join(text.split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _notify(
        self,
        title: str,
        message: str,
        ok: bool,
        subject_label: str | None = None,
        subject: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.notifier.show_event(
            title=title,
            message=message,
            ok=ok,
            subject_label=subject_label,
            subject=subject,
            detail=detail,
        )

    def _auto_enable_focus_lock(self) -> None:
        if not self.config.focus_lock_auto_enable:
            return
        try:
            before, after, adopted = focus_lock.ensure_locked()
        except Exception:
            self.log.exception("FOCUS_ERROR  | auto-enable failed")
            print_status(
                "Focus-lock auto-enable failed (see activity.log)",
                Fore.RED,
            )
            self._notify(
                title="Focus Lock Failed",
                message="Could not auto-enable focus lock. See activity.log for details.",
                ok=False,
                subject_label="Feature",
                subject="focus-lock",
            )
            return

        if adopted:
            self.log.info(
                "FOCUS_LOCK   | AUTO | adopted existing timeout=%dms",
                after,
            )
            print_status("Focus lock already active.", Fore.GREEN)
            message = (
                "Protection was already active from a previous session. "
                "This run adopted it and will restore it cleanly on exit."
            )
        else:
            self.log.info(
                "FOCUS_LOCK   | AUTO | prev_timeout=%dms new_timeout=%dms",
                before, after,
            )
            print_status("Focus lock auto-enabled.", Fore.GREEN)
            message = (
                f"Foreground lock timeout changed from {before}ms to {after}ms."
            )

        self._notify(
            title="Focus Lock Enabled",
            message=message,
            ok=True,
            subject_label="Feature",
            subject="focus-lock",
            detail="Auto-enabled on startup.",
        )

    def _warmup_microphone(self, timeout_s: float = 5.0) -> None:
        """Warm up the input stream without letting PortAudio block startup."""
        result: dict[str, object] = {"done": False, "error": None}

        def worker() -> None:
            try:
                self.audio_recorder.warmup()
                result["done"] = True
            except Exception as e:
                result["error"] = e
                result["done"] = True

        thread = threading.Thread(
            target=worker,
            name="audio-warmup",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=timeout_s)

        if thread.is_alive():
            self.log.warning(
                "AUDIO_WARN   | warmup timed out after %.1fs; continuing startup",
                timeout_s,
            )
            print_status(
                f"Microphone warmup timed out after {timeout_s:.1f}s; continuing.",
                Fore.YELLOW,
            )
            self._notify(
                title="Microphone Warmup Timed Out",
                message="Startup continued, but the selected microphone may be slow or unavailable.",
                ok=False,
                subject_label="Input",
                subject=str(self.config.input_device or "<default input>"),
            )
            # The old recorder may still be stuck inside PortAudio while
            # holding its lock. Replace it so hotkey recording has a fresh
            # recorder instance instead of blocking on that lock.
            self.audio_recorder = AudioRecorder(
                sample_rate=self.config.sample_rate,
                device=self.config.input_device,
                keepalive_ms=self.config.input_keepalive_ms,
                log=self.log,
            )
            return

        if result["error"] is not None:
            raise result["error"]

        input_desc = self.audio_recorder.describe_input()
        self.log.info("AUDIO_INPUT  | %s", input_desc)
        print_status(f"Microphone ready: {input_desc}", Fore.CYAN)

    def run(self) -> None:
        colorama_init()

        self.log.info(
            "BOOT         | python=%s platform=%s device=%s model=%s argv=%s",
            platform.python_version(),
            platform.platform(),
            self.device, self.config.model_size, sys.argv,
        )

        exit_reason = "clean"
        try:
            self._auto_enable_focus_lock()
            print_status(
                f"Loading Whisper model '{self.config.model_size}'... "
                "(first run downloads ~1-3 GB)",
                Fore.YELLOW,
            )
            self.transcriber.load_model(
                on_progress=lambda msg: print_status(msg, Fore.YELLOW)
            )
            print_status("Model ready!", Fore.GREEN)
            try:
                self._warmup_microphone()
            except Exception as e:
                self.log.exception("AUDIO_ERROR  | warmup failed: %s", e)
                print_status(f"Microphone warmup failed: {e}", Fore.RED)

            if self.screen is not None:
                self.screen.start()
            self.notifier.start()

            print_banner(self.config, self.device)
            self.log.info(
                "READY        | voice=%s cmd=%s focus=%s video=%s shot=%s shot_edit=%s",
                self.config.hotkey,
                self.config.command_hotkey,
                self.config.focus_lock_hotkey,
                self.config.screen.video_hotkey if self.screen else "-",
                self.config.screen.screenshot_hotkey if self.screen else "-",
                self.config.screen.screenshot_edit_hotkey if self.screen else "-",
            )

            self.hotkey_mgr.start()
            print_status("Listening...", Fore.GREEN)

            try:
                self.hotkey_mgr.wait()
            except KeyboardInterrupt:
                exit_reason = "keyboard_interrupt"
            except Exception as e:
                exit_reason = f"exception: {type(e).__name__}: {e}"
                self.log.exception(
                    "FATAL        | hotkey manager wait() crashed"
                )
                # Re-raise after logging so the global excepthook also
                # captures it and the console shows the trace.
                raise
        except Exception as e:
            if exit_reason == "clean":
                exit_reason = f"exception: {type(e).__name__}: {e}"
                self.log.exception("FATAL        | run() crashed before hotkey loop")
            raise
        finally:
            print()
            print_status("Shutting down...", Fore.YELLOW)
            try:
                self.hotkey_mgr.stop()
            except Exception:
                self.log.exception("EXIT_ERROR   | hotkey_mgr.stop failed")
            try:
                self.inserter.shutdown()
            except Exception:
                self.log.exception("EXIT_ERROR   | inserter.shutdown failed")
            try:
                self.audio_recorder.close()
            except Exception:
                self.log.exception("EXIT_ERROR   | audio_recorder.close failed")
            if self.screen is not None:
                try:
                    self.screen.stop()
                except Exception:
                    self.log.exception("EXIT_ERROR   | screen.stop failed")
            try:
                self.notifier.stop()
            except Exception:
                self.log.exception("EXIT_ERROR   | notifier.stop failed")
            # If user left focus-lock ON, restore Windows default so we
            # don't leave the system in a weird foreground-policy state.
            try:
                if focus_lock.is_locked():
                    focus_lock.restore_if_locked()
                    self.log.info(
                        "FOCUS_LOCK   | auto-restored on exit"
                    )
            except Exception:
                self.log.exception("EXIT_ERROR   | focus_lock.restore_if_locked failed")
            self.log.info("EXIT         | reason=%s", exit_reason)
            print_status(f"Bye! ({exit_reason})", Fore.CYAN)

    # ── Voice callbacks ───────────────────────────────────────────────
    def _on_voice_press(self) -> None:
        self.log.info("KEY          | voice hotkey pressed")
        with self._lock:
            if self._processing or self._recording:
                return
            self._recording = True
            self._recording_mode = "voice"

        self.inserter.capture_target_window()
        self.audio_recorder.start()

        if self.config.sound_feedback:
            threading.Thread(target=voice_beep_start, daemon=True).start()

        print_status(f"{Fore.RED}● Recording...{Style.RESET_ALL}")

    def _on_voice_release(self) -> None:
        self.log.info("KEY          | voice hotkey released")
        with self._lock:
            if not self._recording or self._recording_mode != "voice":
                return
            self._recording = False
            self._recording_mode = None

        if self.config.sound_feedback:
            threading.Thread(target=voice_beep_stop, daemon=True).start()

        threading.Thread(target=self._finalize_voice, daemon=True).start()

    # ── Focus-lock callback ───────────────────────────────────────────
    def _on_focus_lock_toggle(self) -> None:
        self.log.info("KEY          | focus-lock hotkey tapped")
        try:
            new_state, before, after = focus_lock.toggle()
        except Exception:
            self.log.exception("FOCUS_ERROR  | toggle failed")
            print_status("Focus-lock toggle failed (see activity.log)", Fore.RED)
            self._notify(
                title="Focus Lock Failed",
                message="Could not toggle focus lock. See activity.log for details.",
                ok=False,
                subject_label="Feature",
                subject="focus-lock",
            )
            return
        if new_state:
            self.log.info(
                "FOCUS_LOCK   | ON  | prev_timeout=%dms new_timeout=%dms",
                before, after,
            )
            print_status(
                f"{Fore.GREEN}🔒 Focus lock ON{Style.RESET_ALL} "
                f"(was {before}ms → now {after}ms)",
            )
            self._notify(
                title="Focus Lock Enabled",
                message=f"Foreground lock timeout changed from {before}ms to {after}ms.",
                ok=True,
                subject_label="Feature",
                subject="focus-lock",
            )
        else:
            self.log.info(
                "FOCUS_LOCK   | OFF | prev_timeout=%dms restored=%dms",
                before, after,
            )
            print_status(
                f"{Fore.YELLOW}🔓 Focus lock OFF{Style.RESET_ALL} "
                f"(restored {after}ms)",
            )

    # ── Command callbacks ─────────────────────────────────────────────
            self._notify(
                title="Focus Lock Disabled",
                message=f"Foreground lock timeout restored from {before}ms to {after}ms.",
                ok=True,
                subject_label="Feature",
                subject="focus-lock",
            )

    def _on_command_press(self) -> None:
        self.log.info("KEY          | command hotkey pressed")
        with self._lock:
            if self._processing or self._recording:
                return
            self._recording = True
            self._recording_mode = "command"

        self.audio_recorder.start()

        if self.config.sound_feedback:
            threading.Thread(target=voice_beep_start, daemon=True).start()

        print_status(f"{Fore.MAGENTA}● Recording command...{Style.RESET_ALL}")

    def _on_command_release(self) -> None:
        self.log.info("KEY          | command hotkey released")
        with self._lock:
            if not self._recording or self._recording_mode != "command":
                return
            self._recording = False
            self._recording_mode = None

        if self.config.sound_feedback:
            threading.Thread(target=voice_beep_stop, daemon=True).start()

        threading.Thread(target=self._finalize_command, daemon=True).start()

    def _finalize_command(self) -> None:
        with self._lock:
            self._processing = True
        try:
            audio = self.audio_recorder.stop()
            if audio is None:
                print_status("No audio captured.", Fore.YELLOW)
                self.log.info("CMD_EMPTY    | no audio captured")
                self._notify(
                    title="Command Failed",
                    message="No audio was captured for the command hotkey.",
                    ok=False,
                    subject_label="Command",
                    subject="voice-command",
                )
                return

            duration = len(audio) / self.config.sample_rate
            if duration < self.config.min_duration:
                print_status(f"Too short ({duration:.1f}s), skipped.", Fore.YELLOW)
                self.log.info("CMD_SKIP     | duration=%.2fs (below min)", duration)
                self._notify(
                    title="Command Failed",
                    message=f"Command recording was too short ({duration:.1f}s).",
                    ok=False,
                    subject_label="Command",
                    subject="voice-command",
                )
                return

            print_status(
                f"{Fore.YELLOW}◆ Transcribing command ({duration:.1f}s)...{Style.RESET_ALL}"
            )
            start = time.perf_counter()
            text = self.transcriber.transcribe(
                audio, language=self.config.command_language,
            )
            elapsed = time.perf_counter() - start

            if not text:
                print_status("Nothing recognized.", Fore.YELLOW)
                self.log.info("CMD_EMPTY    | nothing recognized")
                self._notify(
                    title="Command Failed",
                    message="Speech was captured, but no command was recognized.",
                    ok=False,
                    subject_label="Command",
                    subject="voice-command",
                )
                return

            self.log.info(
                "CMD_TEXT     | elapsed=%.2fs lang=%s text=%r",
                elapsed, self.config.command_language, text,
            )
            print_status(
                f'{Fore.CYAN}Heard:{Style.RESET_ALL} '
                f'"{Fore.WHITE}{text}{Style.RESET_ALL}"'
            )

            result = commands.dispatch(text)
            if result["matched"]:
                self.log.info(
                    "CMD_EXEC     | action=%s | %s",
                    result["action"], result["message"],
                )
                if result.get("ok", False):
                    print_status(
                        f"{Fore.GREEN}✓ {result['message']}{Style.RESET_ALL}"
                    )
                else:
                    print_status(
                        f"{Fore.YELLOW}? {result['message']}{Style.RESET_ALL}"
                    )
            else:
                self.log.info("CMD_UNKNOWN  | %s", result["message"])
                print_status(
                    f"{Fore.YELLOW}? {result['message']}{Style.RESET_ALL}"
                )
            self.notifier.show_command_result(
                spoken_text=text,
                command_id=result.get("command_id"),
                message=result["message"],
                ok=result.get("ok", False),
            )

        except Exception as e:
            print_status(f"Command error: {e}", Fore.RED)
            self.log.exception("CMD_ERROR    | finalize crashed: %s", e)
            if self.config.sound_feedback:
                voice_beep_error()
            self.notifier.show_command_result(
                spoken_text=text if "text" in locals() and text else "<none>",
                command_id=None,
                message=f"Command error: {e}",
                ok=False,
            )

        finally:
            with self._lock:
                self._processing = False

    def _finalize_voice(self) -> None:
        with self._lock:
            self._processing = True
        try:
            audio = self.audio_recorder.stop()
            if audio is None:
                print_status("No audio captured.", Fore.YELLOW)
                self.log.info("VOICE_EMPTY  | no audio captured")
                self._notify(
                    title="Voice Input Failed",
                    message="No audio was captured for voice input.",
                    ok=False,
                    subject_label="Action",
                    subject="voice-input",
                )
                return

            duration = len(audio) / self.config.sample_rate
            if duration < self.config.min_duration:
                print_status(f"Too short ({duration:.1f}s), skipped.", Fore.YELLOW)
                self.log.info("VOICE_SKIP   | duration=%.2fs (below min)", duration)
                self._notify(
                    title="Voice Input Failed",
                    message=f"Voice recording was too short ({duration:.1f}s).",
                    ok=False,
                    subject_label="Action",
                    subject="voice-input",
                )
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
                self._notify(
                    title="Voice Input Ready",
                    message="Text sent to the active window and copied to clipboard.",
                    ok=True,
                    subject_label="Action",
                    subject="paste + clipboard",
                    detail=f"Heard: {self._shorten(text)}",
                )
            else:
                print_status("Nothing recognized.", Fore.YELLOW)
                self.log.info("VOICE_EMPTY  | nothing recognized")
                self._notify(
                    title="Voice Input Failed",
                    message="Speech was captured, but nothing was recognized.",
                    ok=False,
                    subject_label="Action",
                    subject="voice-input",
                )

        except Exception as e:
            print_status(f"Transcription error: {e}", Fore.RED)
            self.log.exception("VOICE_ERROR  | finalize crashed: %s", e)
            if self.config.sound_feedback:
                voice_beep_error()
            self._notify(
                title="Voice Input Failed",
                message=f"Voice processing failed: {e}",
                ok=False,
                subject_label="Action",
                subject="voice-input",
            )

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
    install_exception_hooks(app.log)
    try:
        app.run()
    except KeyboardInterrupt:
        # Already logged inside App.run()'s finally.
        pass
    except Exception:
        # Already logged with trace; sys.excepthook will re-emit to stderr
        # so the user sees why the process exited.
        raise


if __name__ == "__main__":
    main()
