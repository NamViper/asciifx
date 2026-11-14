"""Frame sources: procedural, image, video, text-frame files.

Image and video support need numpy and Pillow (video also ffmpeg), so those
names, and the text-frame player, are imported on first access.
``import asciifx.sources`` stays stdlib-only.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from .base import Source
from .procedural import Bounce

if TYPE_CHECKING:
    from .image import ImageSource, image_to_grid
    from .textframes import TextFramesSource, ansi_to_grid
    from .video import FFmpegNotFoundError, VideoSource, find_ffmpeg

_LAZY = {
    "ImageSource": ".image",
    "image_to_grid": ".image",
    "TextFramesSource": ".textframes",
    "ansi_to_grid": ".textframes",
    "FFmpegNotFoundError": ".video",
    "VideoSource": ".video",
    "find_ffmpeg": ".video",
}

__all__ = [
    "Bounce", "FFmpegNotFoundError", "ImageSource", "Source", "TextFramesSource", "VideoSource",
    "ansi_to_grid", "find_ffmpeg", "image_to_grid",
]  # fmt: skip


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
