"""Configuration loader for Whisper Voice Input + Screen Capture."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


VOICE_DEFAULTS = {
    "hotkey": "f21",
    "language": "auto",
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

SCREEN_DEFAULTS = {
    "enabled": True,
    "video_hotkey": "f20",
    "screenshot_hotkey": "f17",
    "video_fps": 15,
    "output_dir": "~/Documents/records",
}


@dataclass
class ScreenConfig:
    enabled: bool = SCREEN_DEFAULTS["enabled"]
    video_hotkey: str = SCREEN_DEFAULTS["video_hotkey"]
    screenshot_hotkey: str = SCREEN_DEFAULTS["screenshot_hotkey"]
    video_fps: int = SCREEN_DEFAULTS["video_fps"]
    output_dir: str = SCREEN_DEFAULTS["output_dir"]


@dataclass
class Config:
    hotkey: str = VOICE_DEFAULTS["hotkey"]
    language: str = VOICE_DEFAULTS["language"]
    model_size: str = VOICE_DEFAULTS["model_size"]
    device: str = VOICE_DEFAULTS["device"]
    compute_type: str = VOICE_DEFAULTS["compute_type"]
    beam_size: int = VOICE_DEFAULTS["beam_size"]
    sample_rate: int = VOICE_DEFAULTS["sample_rate"]
    min_duration: float = VOICE_DEFAULTS["min_duration"]
    sound_feedback: bool = VOICE_DEFAULTS["sound_feedback"]
    insert_mode: str = VOICE_DEFAULTS["insert_mode"]
    stream_interval: float = VOICE_DEFAULTS["stream_interval"]
    screen: ScreenConfig = field(default_factory=ScreenConfig)

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        path = Path(path)
        data = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        screen_raw = data.pop("screen", None) or {}
        voice_merged = {**VOICE_DEFAULTS, **data}
        screen_merged = {**SCREEN_DEFAULTS, **screen_raw}

        voice_fields = {
            k: v for k, v in voice_merged.items()
            if k in cls.__dataclass_fields__ and k != "screen"
        }
        screen_fields = {
            k: v for k, v in screen_merged.items()
            if k in ScreenConfig.__dataclass_fields__
        }
        return cls(**voice_fields, screen=ScreenConfig(**screen_fields))

    @property
    def effective_device(self) -> str:
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
        if self.effective_device == "cpu" and self.compute_type == "float16":
            return "int8"
        return self.compute_type
