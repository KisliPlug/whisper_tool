"""Audio recorder using sounddevice — captures microphone input."""

import logging
import threading
from collections import deque

import numpy as np
import sounddevice as sd


class AudioRecorder:
    """Records audio from the default microphone.

    Usage:
        recorder = AudioRecorder(sample_rate=16000)
        recorder.start()
        # ... user speaks ...
        audio = recorder.stop()  # Returns numpy array (float32, mono)
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        device: str | int | None = None,
        keepalive_ms: int = 0,
        log: logging.Logger | None = None,
    ):
        self.sample_rate = sample_rate
        self.device = device
        self.keepalive_ms = max(0, int(keepalive_ms))
        self._log = log
        self._chunks: deque[np.ndarray] = deque()
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._lock = threading.Lock()
        self._close_timer: threading.Timer | None = None

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status
    ) -> None:
        """Called by sounddevice for each audio chunk."""
        if status:
            if self._log is not None:
                try:
                    self._log.warning("AUDIO_STATUS | %s", status)
                except Exception:
                    pass
        if self._recording:
            self._chunks.append(indata.copy())

    def _cancel_close_timer_locked(self) -> None:
        if self._close_timer is None:
            return
        self._close_timer.cancel()
        self._close_timer = None

    def _close_stream_locked(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
        finally:
            try:
                self._stream.close()
            finally:
                self._stream = None

    def _schedule_close_locked(self) -> None:
        self._cancel_close_timer_locked()
        if self.keepalive_ms <= 0:
            self._close_stream_locked()
            return
        self._close_timer = threading.Timer(
            self.keepalive_ms / 1000.0,
            self._close_if_idle,
        )
        self._close_timer.daemon = True
        self._close_timer.start()

    def _close_if_idle(self) -> None:
        with self._lock:
            self._close_timer = None
            if self._recording:
                return
            self._close_stream_locked()

    def _ensure_stream_locked(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=1024,
        )
        self._stream.start()

    def start(self) -> None:
        """Start recording from the configured microphone."""
        with self._lock:
            if self._recording:
                return
            self._cancel_close_timer_locked()
            self._ensure_stream_locked()
            self._chunks.clear()
            self._recording = True

    def warmup(self) -> None:
        """Open the microphone once so Bluetooth headsets wake up before first use."""
        with self._lock:
            self._cancel_close_timer_locked()
            self._ensure_stream_locked()
            if not self._recording:
                self._schedule_close_locked()

    def stop(self) -> np.ndarray | None:
        """Stop recording and return the captured audio as a float32 numpy array.

        Returns None if no audio was captured.
        """
        with self._lock:
            self._recording = False
            if self._stream is not None:
                self._schedule_close_locked()

            if not self._chunks:
                return None

            audio = np.concatenate(list(self._chunks), axis=0).flatten()
            self._chunks.clear()
            return audio

    def close(self) -> None:
        with self._lock:
            self._recording = False
            self._chunks.clear()
            self._cancel_close_timer_locked()
            self._close_stream_locked()

    def describe_input(self) -> str:
        try:
            device_ref = self.device
            if device_ref is None:
                default_input, _ = sd.default.device
                device_ref = default_input
            info = sd.query_devices(device_ref, "input")
            return (
                f"{info['name']} | in={info['max_input_channels']} "
                f"default_sr={int(info['default_samplerate'])}"
            )
        except Exception:
            if self.device is None:
                return "<default input>"
            return str(self.device)

    @property
    def is_recording(self) -> bool:
        return self._recording

    def duration(self) -> float:
        """Approximate duration of currently captured audio in seconds."""
        total_samples = sum(chunk.shape[0] for chunk in self._chunks)
        return total_samples / self.sample_rate

    def get_snapshot(self) -> np.ndarray | None:
        """Get a copy of all recorded audio so far WITHOUT stopping.

        Returns None if no audio captured yet.
        """
        with self._lock:
            if not self._chunks:
                return None
            return np.concatenate(list(self._chunks), axis=0).flatten()
