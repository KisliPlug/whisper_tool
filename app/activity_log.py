"""Structured activity logger — shared by voice and screen events.

Writes to <output_dir>/activity.log so you can grep at end of day:
    VIDEO_SAVED, SHOT_SAVED, VOICE_OK, VOICE_EMPTY, VOICE_ERROR, ...

File-only (no stdout): whisper's main.py already handles console UX
with colorama; duplicating would produce mixed formats.
"""

import logging
from pathlib import Path


_cached: dict[str, logging.Logger] = {}


def get_logger(log_dir: Path) -> logging.Logger:
    key = str(log_dir.resolve())
    if key in _cached:
        return _cached[key]

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "activity.log"

    logger = logging.getLogger(f"activity.{key}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _cached[key] = logger
    return logger
