# Whisper Screen Tools

System-wide voice input, voice commands, focus protection, screen recording, screenshots, and an Electron image annotator for Windows.

The app is built around global hotkeys: hold a key to dictate text, tap a key to capture the screen, or open the screenshot editor and save annotated PNG + JSON metadata for later AI review.

## Features

- Push-to-talk voice typing with faster-whisper.
- Voice command hotkey for spoken commands such as `task kill <app>`.
- Focus-lock hotkey to reduce foreground focus stealing by apps such as Revit.
- Plain screenshot capture with frozen-screen region selection.
- Screenshot image editor with notes, drawing tools, shapes, text boxes, colors, grid snap, zoom, pan, selection, copy/cut/paste, undo/redo, and radial tool wheel.
- Annotated screenshot export as `screenshot.png` plus `annotations.json`.
- Video region recording with `recording.gif`, `recording.mp4`, and a labeled `frames.png` grid.
- Clipboard integration: paths are copied after successful voice/screen operations.
- Activity logging in the screen output directory.

## Requirements

| Requirement | Details |
|---|---|
| OS | Windows 10/11 |
| Python | 3.10+ |
| Node.js | Needed for the Electron annotator |
| Microphone | Any Windows input device |
| GPU | Optional NVIDIA CUDA for faster transcription |

## Installation

```powershell
git clone https://github.com/KisliPlug/whisper_tool.git
cd whisper_tool

python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

npm install
npm run build:annotator
```

For NVIDIA GPU acceleration, install CUDA runtime wheels as needed:

```powershell
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

The first Whisper model load downloads the selected model automatically.

## Run

```powershell
python main.py
```

With a custom config:

```powershell
python main.py --config my_config.yaml
```

## Default Hotkeys

| Action | Default |
|---|---|
| Voice typing | Hold `F21`, release to transcribe and paste |
| Voice command | Hold `F19`, release to execute command |
| Focus lock | Tap `Ctrl+F17` |
| Screen recording | Tap `F20` to select/start, tap `F20` again to stop |
| Plain screenshot | Tap `F17`, select region |
| Annotated screenshot | Tap `F18`, select region, edit, tap `F18` again to commit |
| Exit app | `Ctrl+C` in the terminal |

Hotkeys are configured in `config.yaml`.

## Voice Input

Hold the voice hotkey, speak, then release. The recognized text is copied to the clipboard and pasted into the previously focused window.

Important config keys:

```yaml
hotkey: "f21"
language: "auto"
allowed_languages: ["ru", "en"]
model_size: "medium"
device: "auto"
compute_type: "float16"
insert_mode: "clipboard"
```

Supported model sizes include `tiny`, `base`, `small`, `medium`, and `large-v3`.

## Voice Commands

Hold the command hotkey, speak a command, and release. Current command support is intentionally narrow:

```text
task kill <app>
```

Examples:

- `task kill revit`
- `task kill chrome`
- Russian app-name aliases are handled in `app/commands.py`.

Important config keys:

```yaml
command_hotkey: "f19"
command_language: "en"
```

## Focus Lock

Focus lock uses a Windows foreground-lock timeout change to reduce unwanted focus stealing. This is useful when background apps interrupt text insertion or capture workflows.

```yaml
focus_lock_hotkey: "ctrl+f17"
focus_lock_auto_enable: true
```

## Screen Recording

Tap the video hotkey, select a region, and recording starts. Tap the same hotkey again to stop.

Each recording creates a timestamped folder containing:

- `recording.gif`
- `recording.mp4`
- `frames.png`

The output folder path is copied to the clipboard.

Relevant config:

```yaml
screen:
  enabled: true
  video_hotkey: "f20"
  video_fps: 15
  output_dir: "~/Documents/records"
```

## Screenshots

Plain screenshot:

1. Tap `screenshot_hotkey`.
2. Select a region on a frozen snapshot.
3. The app saves `screenshot.png`.
4. The PNG file path is copied to the clipboard.

Annotated screenshot:

1. Tap `screenshot_edit_hotkey`.
2. Select a region on a frozen snapshot.
3. The Electron annotator opens above other windows.
4. Draw notes, shapes, arrows, text, and markups.
5. Tap `screenshot_edit_hotkey` again or click Save.
6. The app saves `screenshot.png` and `annotations.json`.
7. The output folder path is copied to the clipboard.

Use `Save to Clipboard` instead of `Save` when you want the edited image itself on the clipboard. The image and metadata are still saved to disk, but Python will not overwrite the clipboard with the output folder path.

Relevant config:

```yaml
screen:
  screenshot_hotkey: "f17"
  screenshot_edit_hotkey: "f18"
```

## Image Annotator

The editor is an Electron + React app launched by the Python screen controller.

Main capabilities:

- Multiple notes per image.
- Note names and note text.
- Select, pen, line, arrow, rectangle, ellipse, and text tools.
- Radial tool wheel instead of a permanent tool sidebar.
- Color picker, stroke size, text size, grid size, and snap settings.
- Move, resize, delete, copy, cut, paste, duplicate, and multi-select.
- Undo/redo.
- Zoom, fit, 100%, and middle-mouse pan.
- Inline multi-line text boxes.
- Freehand pen ignores grid snap for intermediate points so strokes stay smooth.

Annotator controls:

| Action | Control |
|---|---|
| Open radial wheel | Right mouse button over the image, or top tool pill |
| Pan view | Hold middle mouse button |
| Zoom | `Ctrl+wheel`, `Ctrl++`, `Ctrl+-`, `Ctrl+0` |
| Undo/redo | `Ctrl+Z`, `Ctrl+Y` |
| Copy/cut/paste objects | `Ctrl+C`, `Ctrl+X`, `Ctrl+V` when not editing text |
| Delete selection | `Delete` or `Backspace` when not editing text |
| Save annotated screenshot | Tap `F18` again or click Save |
| Save edited image to clipboard | Click `Save to Clipboard` |

The Electron window title is `Whisper Screenshot Annotator`. On this machine, GlazeWM is configured to ignore that title so the editor floats instead of being tiled.

Tool shortcuts use physical key codes as a fallback, so they still work when the keyboard layout is not English.

## Annotation Metadata

Annotated screenshot folders contain:

```text
screenshot.png
annotations.json
```

`annotations.json` uses original screenshot pixels:

```json
{
  "version": 1,
  "image": "screenshot.png",
  "size": { "width": 1280, "height": 720 },
  "coordinate_space": "screenshot_pixels",
  "notes": [
    {
      "id": 1,
      "name": "Note 1",
      "text": "User note text",
      "items": [
        {
          "id": 1,
          "type": "rectangle",
          "bbox": [420, 180, 610, 240],
          "color": "#ff2a2a",
          "color_name": "red",
          "width": 4
        }
      ]
    }
  ]
}
```

Supported item types:

- `rectangle`
- `ellipse`
- `line`
- `arrow`
- `freehand`
- `text`

## Configuration Reference

Top-level keys:

| Key | Description |
|---|---|
| `hotkey` | Voice typing hold hotkey |
| `command_hotkey` | Voice command hold hotkey |
| `command_language` | Language forced for command recognition |
| `focus_lock_hotkey` | Focus lock toggle hotkey |
| `focus_lock_auto_enable` | Enable focus lock on startup |
| `language` | Voice recognition language, or `auto` |
| `allowed_languages` | Allow-list for auto language detection |
| `input_device` | Optional microphone index or name |
| `input_keepalive_ms` | Keep mic stream warm after recording |
| `model_size` | Whisper model size |
| `device` | `auto`, `cuda`, or `cpu` |
| `compute_type` | faster-whisper compute precision |
| `beam_size` | Decode beam size |
| `sample_rate` | Audio sample rate |
| `min_duration` | Ignore shorter recordings |
| `stream_interval` | Streaming transcription interval |
| `sound_feedback` | Beeps for record start/stop |
| `insert_mode` | `clipboard` or `typing` |

Screen keys:

| Key | Description |
|---|---|
| `screen.enabled` | Enable screen tools |
| `screen.video_hotkey` | Toggle region recording |
| `screen.screenshot_hotkey` | Plain screenshot |
| `screen.screenshot_edit_hotkey` | Screenshot editor |
| `screen.video_fps` | Recording frame rate |
| `screen.output_dir` | Capture output root |

## Development

Build the annotator:

```powershell
npm run build:annotator
```

Run basic Python compile validation:

```powershell
python -m compileall -q app main.py
```

Use Vite during annotator UI work:

```powershell
npm run annotator:dev
```

Set `ANNOTATOR_DEV_URL` before launching the Python flow if you want Electron to load the dev server instead of `dist/index.html`.

## Project Structure

```text
whisper_tool/
  main.py                         App entry point and hotkey wiring
  config.yaml                     User configuration
  requirements.txt                Python dependencies
  package.json                    Electron/Vite/React dependencies
  app/
    commands.py                   Voice command parser/executor
    config.py                     Config loader
    focus_lock.py                 Windows focus-lock helper
    hotkey.py                     Global hotkey polling
    notifier.py                   Desktop/user notifications
    recorder.py                   Microphone capture
    transcriber.py                faster-whisper wrapper
    screen/
      controller.py               Screen capture state machine
      electron_annotator.py       Python/Electron bridge
      annotator.py                Tk fallback annotator
      exporter.py                 GIF/MP4/frame-grid exporter
      selector.py                 Region picker
      video.py                    Region video recorder
  electron/
    annotator/
      main.js                     Electron main process
      preload.cjs                 IPC bridge
      src/App.jsx                 React annotator
      src/styles.css              Annotator styling
```

## Troubleshooting

- If the annotator opens in a GlazeWM tile, reload GlazeWM config with `Alt+Shift+R` or:

```powershell
glazewm.exe command wm-reload-config
```

- If the annotator does not open, run `npm install` and `npm run build:annotator`.
- If a screenshot image path contains spaces or non-ASCII characters, the Electron bridge should use a proper `file://` URL. This is handled in `electron/annotator/main.js`.
- If video export fails, check that `imageio-ffmpeg` is installed from `requirements.txt`.
- If the microphone hangs at startup, check `input_device` and Windows microphone permissions.

## License

MIT
