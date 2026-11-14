"""Color mode detection and truecolor -> xterm-256 fallback."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache


class ColorMode(StrEnum):
    TRUECOLOR = "truecolor"
    ANSI256 = "256"


def detect_color_mode(env: Mapping[str, str] = os.environ) -> ColorMode:
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return ColorMode.TRUECOLOR
    return ColorMode.ANSI256


_CUBE = (0, 95, 135, 175, 215, 255)


def _cube_index(v: int) -> int:
    # Nearest of the 6 xterm cube levels (midpoints: 48, 115, 155, 195, 235).
    return 0 if v < 48 else 1 if v < 115 else (v - 35) // 40


@lru_cache(maxsize=4096)
def rgb_to_256(r: int, g: int, b: int) -> int:
    """Nearest xterm-256 palette index (6x6x6 cube or 24-step gray ramp)."""
    ri, gi, bi = _cube_index(r), _cube_index(g), _cube_index(b)
    cr, cg, cb = _CUBE[ri], _CUBE[gi], _CUBE[bi]
    gray_i = min(23, max(0, round(((r + g + b) / 3 - 8) / 10)))
    gv = 8 + 10 * gray_i
    cube_d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
    gray_d = (r - gv) ** 2 + (g - gv) ** 2 + (b - gv) ** 2
    if gray_d < cube_d:
        return 232 + gray_i
    return 16 + 36 * ri + 6 * gi + bi
