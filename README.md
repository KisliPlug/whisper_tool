<p align="center">
  <h1 align="center">🎙️ Whisper Voice Input</h1>
  <p align="center">
    System-wide push-to-talk voice typing for Windows powered by <a href="https://github.com/SYSTRAN/faster-whisper">faster-whisper</a>
  </p>
  <p align="center">
    Hold a key, speak, release — text is pasted into any active window.
  </p>
</p>

---

## How It Works

1. **Hold** the configured hotkey
2. **Speak** into your microphone
3. **Release** the key — transcribed text is instantly pasted into whatever app has focus

Works everywhere: terminal, editor, browser, chat — any window that accepts text input.

## Features

- **Push-to-talk** — hold a key to record, release to transcribe and paste
- **System-wide** — works from any application, no window switching required
- **GPU accelerated** — CUDA support via CTranslate2 for fast transcription
- **Multi-language** — supports any language Whisper knows; auto-detect or force a specific one
- **Configurable** — model size, hotkey, language, audio settings — all in one YAML file
- **Sound feedback** — audio beeps on recording start/stop
- **Clipboard-safe** — pastes via `Shift+Insert`, works even in terminal apps (Claude CLI, vim, etc.)

## Requirements

| Requirement | Details |
|---|---|
| **OS** | Windows 10/11 |
| **Python** | 3.10+ |
| **Microphone** | Any default input device |
| **GPU** *(optional)* | NVIDIA GPU with CUDA support for faster transcription |

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/KisliPlug/whisper_tool.git
cd whisper_tool
```

### 2. Create a virtual environment

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install dependencies

**CPU only:**

```bash
pip install -r requirements.txt
```

**With NVIDIA GPU (recommended):**

```bash
pip install -r requirements.txt
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

> On first run, the Whisper model will be downloaded automatically (~1–3 GB depending on the model size).

### 4. Run

```bash
python main.py
```

Or with a custom config:

```bash
python main.py --config my_config.yaml
```

## Configuration

All settings live in **`config.yaml`**. Edit it to match your setup:

```yaml
# Push-to-talk key
hotkey: "f21"

# Recognition language ("auto", "ru", "en", etc.)
language: "auto"

# Whisper model size
model_size: "medium"

# Compute device ("auto", "cuda", "cpu")
device: "auto"

# Compute precision
compute_type: "float16"

# Beam search width (higher = better quality, slower)
beam_size: 5

# Audio sample rate in Hz
sample_rate: 16000

# Minimum recording duration in seconds (skip accidental taps)
min_duration: 0.5

# Sound feedback on start/stop
sound_feedback: true

# Text insertion method ("clipboard" or "typing")
insert_mode: "clipboard"
```

### Configuration Reference

| Parameter | Default | Description |
|---|---|---|
| `hotkey` | `"f21"` | Push-to-talk key (see [Supported Hotkeys](#supported-hotkeys)) |
| `language` | `"auto"` | Language code (`"auto"`, `"ru"`, `"en"`, etc.) |
| `model_size` | `"medium"` | Whisper model: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `device` | `"auto"` | `"auto"` / `"cuda"` / `"cpu"` |
| `compute_type` | `"float16"` | GPU: `float16`, `int8_float16`, `float32`; CPU: `int8`, `float32` |
| `beam_size` | `5` | Beam search width. Higher = more accurate, slower |
| `sample_rate` | `16000` | Audio sample rate (16 kHz is optimal for Whisper) |
| `min_duration` | `0.5` | Minimum recording length in seconds |
| `sound_feedback` | `true` | Beep on recording start/stop |
| `insert_mode` | `"clipboard"` | `"clipboard"` (Shift+Insert) or `"typing"` (character-by-character) |

### Model Size Guide

| Model | VRAM | Speed | Quality | Best For |
|---|---|---|---|---|
| `tiny` | ~1 GB | Fastest | Low | Quick tests |
| `base` | ~1 GB | Fast | Fair | Light usage |
| `small` | ~2 GB | Moderate | Good | Everyday use |
| `medium` | ~5 GB | Slower | Great | **Recommended for Russian** |
| `large-v3` | ~10 GB | Slowest | Best | Maximum accuracy |

## Supported Hotkeys

Any of the following key names can be used in the `hotkey` config field:

| Category | Keys |
|---|---|
| **Function keys** | `F1` – `F24` |
| **Toggle keys** | `Scroll Lock`, `Caps Lock`, `Num Lock`, `Pause`, `Insert` |
| **Modifier keys** | `Left Ctrl`, `Right Ctrl`, `Left Alt`, `Right Alt`, `Left Shift`, `Right Shift` |

> **Tip:** Keys like `F13`–`F24` are great choices — they don't conflict with anything. Many gaming mice and macro keyboards can be mapped to send these keycodes.

## Controls

| Action | Key |
|---|---|
| **Record** | Hold configured hotkey |
| **Transcribe & paste** | Release the hotkey |
| **Exit** | `Ctrl+C` in the terminal |

## Project Structure

```
whisper_tool/
├── main.py              # Entry point, CLI, app lifecycle
├── config.yaml          # User configuration
├── requirements.txt     # Python dependencies
└── app/
    ├── config.py        # YAML config loader with defaults
    ├── hotkey.py        # Global push-to-talk via Win32 polling
    ├── recorder.py      # Microphone capture (sounddevice)
    ├── transcriber.py   # Whisper model loading & inference
    └── inserter.py      # Text pasting via clipboard + Shift+Insert
```

## License

MIT
