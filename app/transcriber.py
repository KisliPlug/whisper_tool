"""Whisper transcriber using faster-whisper (CTranslate2 backend)."""

import sys
import numpy as np
from faster_whisper import WhisperModel


class Transcriber:
    """Loads a Whisper model and transcribes audio buffers.

    The model is loaded once on init (can take 10-30s on first run due to download).
    Subsequent transcriptions are fast, especially on GPU.
    """

    def __init__(
        self,
        model_size: str = "medium",
        device: str = "cuda",
        compute_type: str = "float16",
        beam_size: int = 5,
        language: str = "ru",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.language = language
        self.model: WhisperModel | None = None

    def load_model(self, on_progress=None) -> None:
        """Load the Whisper model. Call this once at startup.

        Args:
            on_progress: Optional callback(status_message: str) for UI updates.
        """
        if on_progress:
            on_progress(
                f"Loading model '{self.model_size}' on {self.device} "
                f"({self.compute_type})..."
            )

        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )

        if on_progress:
            on_progress("Model loaded successfully.")

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 numpy audio array to text.

        Args:
            audio: 1D float32 numpy array, 16kHz mono.

        Returns:
            Transcribed text string (may be empty if nothing recognized).
        """
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        segments, info = self.model.transcribe(
            audio,
            language=self.language if self.language != "auto" else None,
            beam_size=self.beam_size,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
            condition_on_previous_text=False,  # Prevents repetition loops
            no_speech_threshold=0.6,
            repetition_penalty=1.2,
        )

        # Collect all segment texts
        parts = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                parts.append(text)

        return " ".join(parts)
