"""Electron + React screenshot annotator bridge.

The Python screen pipeline still owns hotkeys, capture, and final save
folders. This bridge hands the cropped screenshot to the Electron UI and
waits for `output.png` plus `annotations.json`.
"""

from __future__ import annotations

import json
import queue as _queue
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .annotator import annotate_image as tk_annotate_image


REPO_ROOT = Path(__file__).resolve().parents[2]
ELECTRON_MAIN = REPO_ROOT / "electron" / "annotator" / "main.js"
ELECTRON_DIST = REPO_ROOT / "dist" / "index.html"


def _electron_executable() -> Path | None:
    bin_name = "electron.cmd" if sys.platform == "win32" else "electron"
    local = REPO_ROOT / "node_modules" / ".bin" / bin_name
    if local.exists():
        return local
    found = shutil.which("electron")
    return Path(found) if found else None


def _electron_available() -> bool:
    return (
        ELECTRON_MAIN.exists()
        and ELECTRON_DIST.exists()
        and _electron_executable() is not None
    )


def annotate_image(root, image_np, event_queue, commit_event="shot_edit"):
    """Run the Electron annotator, falling back to the Tk annotator."""
    if not _electron_available():
        return tk_annotate_image(root, image_np, event_queue, commit_event)

    electron = _electron_executable()
    h, w = image_np.shape[:2]

    with tempfile.TemporaryDirectory(prefix="whisper_annotator_") as tmp:
        session_dir = Path(tmp)
        input_path = session_dir / "input.png"
        request_path = session_dir / "request.json"
        result_path = session_dir / "result.json"
        output_path = session_dir / "output.png"
        metadata_path = session_dir / "annotations.json"

        Image.fromarray(image_np).save(input_path)
        request_path.write_text(
            json.dumps(
                {
                    "imagePath": str(input_path),
                    "size": {"width": w, "height": h},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        proc = subprocess.Popen(
            [str(electron), str(ELECTRON_MAIN), str(session_dir)],
            cwd=str(REPO_ROOT),
        )

        try:
            while proc.poll() is None:
                try:
                    ev = event_queue.get_nowait()
                except _queue.Empty:
                    ev = None
                if ev == commit_event:
                    (session_dir / "commit").write_text("1", encoding="utf-8")
                elif ev is not None:
                    # Drop non-commit hotkeys while the annotator owns focus.
                    pass
                try:
                    root.update()
                except Exception:
                    pass
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                proc.terminate()

        if not result_path.exists():
            return tk_annotate_image(root, image_np, event_queue, commit_event)

        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not result.get("ok"):
            return None
        if not output_path.exists() or not metadata_path.exists():
            return tk_annotate_image(root, image_np, event_queue, commit_event)

        annotated = np.array(Image.open(output_path).convert("RGB"))
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return {
            "image": annotated,
            "metadata": metadata,
            "clipboard_image": bool(result.get("clipboardImage")),
        }
