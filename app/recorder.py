"""Audio recorder using sounddevice — captures microphone input."""

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

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._chunks: deque[np.ndarray] = deque()
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._lock = threading.Lock()

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status
    ) -> None:
        """Called by sounddevice for each audio chunk."""
        if status:
            # Overflow or other issue — log but don't crash
            pass
        if self._recording:
            self._chunks.append(indata.copy())

    def start(self) -> None:
        """Start recording from the default microphone."""
        with self._lock:
            if self._recording:
                return
            self._chunks.clear()
            self._recording = True
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=self._audio_callback,
                blocksize=1024,
            )
            self._stream.start()

    def stop(self) -> np.ndarray | None:
        """Stop recording and return the captured audio as a float32 numpy array.

        Returns None if no audio was captured.
        """
        with self._lock:
            self._recording = False
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None

            if not self._chunks:
                return None

            audio = np.concatenate(list(self._chunks), axis=0).flatten()
            self._chunks.clear()
            return audio

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
