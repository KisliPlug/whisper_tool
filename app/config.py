"""Configuration loader for Whisper Voice Input."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


DEFAULTS = {
    "hotkey": "scroll lock",
    "language": "ru",
    "model_size": "medium",
    "device": "auto",
    "compute_type": "float16",
    "beam_size": 5,
    "sample_rate": 16000,
    "min_duration": 0.5,
    "sound_feedback": True,
    "insert_mode": "clipboard",
    "stream_interval": 1.0,
}


@dataclass
class Config:
    hotkey: str = DEFAULTS["hotkey"]
    language: str = DEFAULTS["language"]
    model_size: str = DEFAULTS["model_size"]
    device: str = DEFAULTS["device"]
    compute_type: str = DEFAULTS["compute_type"]
    beam_size: int = DEFAULTS["beam_size"]
    sample_rate: int = DEFAULTS["sample_rate"]
    min_duration: float = DEFAULTS["min_duration"]
    sound_feedback: bool = DEFAULTS["sound_feedback"]
    insert_mode: str = DEFAULTS["insert_mode"]
    stream_interval: float = DEFAULTS["stream_interval"]

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        """Load config from YAML file, falling back to defaults for missing keys."""
        path = Path(path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        merged = {**DEFAULTS, **data}
        return cls(**{k: v for k, v in merged.items() if k in cls.__dataclass_fields__})

    @property
    def effective_device(self) -> str:
        """Resolve 'auto' device to actual device."""
        if self.device != "auto":
            return self.device
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda"
        except Exception:
            pass
        return "cpu"

    @property
    def effective_compute_type(self) -> str:
        """Pick compute type based on device if set to default."""
        if self.effective_device == "cpu" and self.compute_type == "float16":
            return "int8"
        return self.compute_type
