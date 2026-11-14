"""Transitions: Morph (one grid turns into another) and Reverse (an effect run backwards).

Unlike the effects in ``EFFECTS``, these are not the identity once they end:
a finished Morph shows its target and a finished Reverse shows the wrapped
effect's start state (e.g. nothing). They are still pure: ``apply`` never
mutates its input and depends only on (params, grid, t).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

from ..core.grid import Cell, Color, Grid
from .base import Effect, cell_hashes, hash32, remix, time_bucket
from .decode import BRAILLE_CHARSET, DECODE_HIGHLIGHT, column_extent

MORPH_CHARSET = BRAILLE_CHARSET[::4] + "ABCDEFHKMNRSTXZ0123456789#$%&*+=?@<>/\\|[]{}~^"
"""Braille patterns mixed with ASCII junk: reads as noise between braille, letter
and digit art alike."""

_SWITCH_SALT = 0x3017
_GLYPH_SALT = 0x3018
_FRAME_SALT = 0x3019  # differs from _GLYPH_SALT so per-cell and per-frame hashes never cancel


@lru_cache(maxsize=16)
def _window_starts(
    seed: int, width: int, height: int, sweep: float, scramble: float, x0: int, x1: int
) -> tuple[float, ...]:
    """Per cell: where its scramble window opens, as a fraction of the duration.

    The window is ``[lo, lo + scramble]`` with ``lo = (1 - scramble) * order``,
    where ``order`` in [0, 1] mixes a left-to-right sweep over columns x0..x1
    with per-cell noise. So every window lies inside [0, 1].
    """
    span = x1 - x0
    col = [sweep * min(1.0, max(0.0, (x - x0) / span)) if span > 0 else 0.0 for x in range(width)]
    rnd = cell_hashes(seed, _SWITCH_SALT, width, height)
    keep = 1.0 - scramble
    scale = keep * (1.0 - sweep) / 4294967296.0
    col = [keep * c for c in col]
    return tuple(col[i % width] + rnd[i] * scale for i in range(width * height))


def _union_extent(a: tuple[int, int] | None, b: tuple[int, int] | None) -> tuple[int, int] | None:
    if a is None or b is None:
        return a or b
    return min(a[0], b[0]), max(a[1], b[1])


@dataclass(frozen=True)
class Morph:
    """Turn the grid into ``target`` (same size) cell by cell.

    Every cell gets a switch time in [start, start + duration]: per-cell noise,
    biased by ``sweep`` (0 = random order, 1 = a pure left-to-right sweep over
    the columns holding content in either grid). Around its switch time, for
    ``scramble`` x duration seconds, a cell shows a random glyph from
    ``charset``, re-picked ``rate`` times a second, drawn in ``highlight``
    (None: the target cell's fg, or the source cell's if the target cell is
    blank). Before that window the cell shows the source, after it the target.
    Cells blank in both grids stay blank.

    ``t <= start`` gives the grid, ``t >= end`` gives ``target`` (end wins when
    duration is 0). ``target`` is copied on construction.
    """

    target: Grid = field(hash=False)
    start: float = 0.0
    duration: float = 1.5
    sweep: float = 0.4
    rate: float = 20.0
    scramble: float = 0.35
    charset: str = MORPH_CHARSET
    highlight: Color | None = DECODE_HIGHLIGHT
    seed: int = 0
    _target_extent: tuple[int, int] | None = field(
        init=False, repr=False, compare=False, hash=False, default=None
    )

    def __post_init__(self) -> None:
        if not self.charset:
            raise ValueError("charset must not be empty")
        object.__setattr__(self, "target", self.target.copy())
        object.__setattr__(self, "sweep", min(1.0, max(0.0, self.sweep)))
        object.__setattr__(self, "scramble", min(1.0, max(0.0, self.scramble)))
        object.__setattr__(self, "_target_extent", column_extent(self.target))

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def window_starts(self, grid: Grid) -> tuple[float, ...] | None:
        """Per cell, when its scramble window opens as a fraction of ``duration``
        (it closes ``scramble`` later). None if both grids are entirely blank."""
        extent = _union_extent(column_extent(grid), self._target_extent)
        if extent is None:
            return None
        return _window_starts(
            self.seed, grid.width, grid.height, self.sweep, self.scramble, *extent
        )

    def apply(self, grid: Grid, t: float) -> Grid:
        tgt = self.target
        w, h = grid.width, grid.height
        if (w, h) != (tgt.width, tgt.height):
            raise ValueError(f"Morph target is {tgt.width}x{tgt.height}, grid is {w}x{h}")
        if t >= self.end:
            return tgt.copy()
        if t <= self.start:
            return grid.copy()
        starts = self.window_starts(grid)
        if starts is None:
            return grid.copy()

        p = (t - self.start) / self.duration
        p_done = p - self.scramble  # windows opening at or before this have closed
        hl = self.highlight
        charset, nc = self.charset, len(self.charset)
        rnd = cell_hashes(self.seed, _GLYPH_SALT, w, h)
        b = hash32(self.seed, _FRAME_SALT, time_bucket(t, self.rate))
        src, dst = grid.cells, tgt.cells
        out = list(src)
        for i, lo in enumerate(starts):
            if lo > p:
                continue  # still the source
            d = dst[i]
            if lo <= p_done:
                out[i] = d
                continue
            d_blank = d.ch == " " and d.bg is None
            s = src[i]
            if d_blank and s.ch == " " and s.bg is None:
                continue  # blank in both: stays blank
            ref = s if d_blank else d
            out[i] = Cell(charset[remix(rnd[i], b) % nc], ref.fg if hl is None else hl, ref.bg)
        return Grid(w, h, out)


@dataclass(frozen=True)
class Reverse:
    """Run ``effect``'s timeline backwards inside its own [start, end] window.

    ``apply(grid, t) = effect.apply(grid, start + end - t)`` for ``t < end``,
    with ``start = effect.start`` (0 if it has none) and ``end = effect.end_for(grid)``.
    So before the window it shows the effect's end state (the plain grid, as
    effects are the identity once done). From ``end`` on it shows the effect
    as it is just before ``start`` (e.g. nothing, for an appearing Decode or a
    reveal Matrix). ``Reverse(Decode(appear=0.6))`` is a dissolve: locked
    glyphs scramble, then vanish. (With ``appear=1`` they vanish without scrambling.)
    """

    effect: Effect

    @property
    def start(self) -> float:
        return float(getattr(self.effect, "start", 0.0))

    @property
    def end(self) -> float:
        return self.effect.end

    def end_for(self, grid: Grid) -> float:
        return self.effect.end_for(grid)

    def apply(self, grid: Grid, t: float) -> Grid:
        start, end = self.start, self.end_for(grid)
        u = start + end - t if t < end else math.nextafter(start, -math.inf)
        out = self.effect.apply(grid, u)
        return grid.copy() if out is grid else out
