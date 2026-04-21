"""Export captured frames to GIF, MP4, and a labeled PNG frame grid.

The frame grid is the artifact optimized for AI vision models — animated
GIFs aren't recognized as video by most models (Claude API, GPT-4V, etc.
see only the first frame). A grid of labeled key frames is universally
readable and often more informative than the original animation.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import imageio.v2 as imageio


GRID_COLS = 4
GRID_ROWS = 3
MAX_GRID_WIDTH = 2400


def export_gif(frames, fps, path: Path):
    duration_ms = max(20, int(round(1000 / fps)))
    pil_frames = [Image.fromarray(f) for f in frames]
    pil_frames[0].save(
        path,
        save_all=True,
        append_images=pil_frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
        disposal=2,
    )


def export_mp4(frames, fps, path: Path):
    h, w = frames[0].shape[:2]
    h2, w2 = h - (h % 2), w - (w % 2)
    if (h2, w2) != (h, w):
        frames = [f[:h2, :w2] for f in frames]
    with imageio.get_writer(
        str(path),
        fps=fps,
        codec="libx264",
        quality=7,
        macro_block_size=1,
        ffmpeg_log_level="error",
    ) as writer:
        for f in frames:
            writer.append_data(f)


def _load_font(size):
    for candidate in (
        "arial.ttf",
        "Arial.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def export_frame_grid(frames, fps, path: Path, cols=GRID_COLS, rows=GRID_ROWS):
    n = min(cols * rows, len(frames))
    if n == 0:
        return
    indices = np.linspace(0, len(frames) - 1, n).astype(int)
    sel = [frames[i] for i in indices]
    h, w = sel[0].shape[:2]

    scale = min(1.0, MAX_GRID_WIDTH / (w * cols))
    if scale < 1.0:
        new_w, new_h = int(w * scale), int(h * scale)
        sel = [
            np.array(Image.fromarray(f).resize((new_w, new_h), Image.LANCZOS))
            for f in sel
        ]
        w, h = new_w, new_h

    grid = np.zeros((h * rows, w * cols, 3), dtype=np.uint8)
    for i, f in enumerate(sel):
        r, c = i // cols, i % cols
        grid[r * h:(r + 1) * h, c * w:(c + 1) * w] = f

    img = Image.fromarray(grid)
    draw = ImageDraw.Draw(img)
    font = _load_font(max(14, int(h * 0.05)))
    for i in range(n):
        r, c = i // cols, i % cols
        t = indices[i] / fps
        label = f"{i + 1}/{n}  t={t:.2f}s"
        x_pos, y_pos = c * w + 6, r * h + 4
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    draw.text((x_pos + dx, y_pos + dy), label, fill="black", font=font)
        draw.text((x_pos, y_pos), label, fill="white", font=font)
    img.save(path)


def export_all(frames, fps, out_dir: Path):
    """Write recording.gif, recording.mp4, and frames.png into out_dir.

    Each export is wrapped so a single-format failure doesn't lose the others.
    """
    for name, fn in (
        ("recording.gif", lambda p: export_gif(frames, fps, p)),
        ("recording.mp4", lambda p: export_mp4(frames, fps, p)),
        ("frames.png",    lambda p: export_frame_grid(frames, fps, p)),
    ):
        try:
            fn(out_dir / name)
        except Exception as e:
            print(f"  [{name}] export failed: {e}")
