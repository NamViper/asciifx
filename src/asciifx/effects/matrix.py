"""Matrix: digital rain falling over the grid, optionally painting the art in."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import NamedTuple

from ..core.grid import BLANK, Cell, Color, Grid
from .base import cell_hashes, finite_t, gradient, hash32, noise, remix, time_bucket

# Single-cell-width glyphs only: half-width katakana (U+FF66..U+FF9D), digits,
# ASCII symbols. Full-width characters would take two cells and break the grid.
MATRIX_CHARSET = (
    "".join(chr(c) for c in range(0xFF66, 0xFF9E)) + "0123456789" + ":.=*+-<>|\"'_"
)
MATRIX_HEAD: Color = (215, 255, 215)
MATRIX_TAIL: tuple[Color, ...] = ((80, 255, 110), (0, 200, 60), (0, 130, 40), (0, 60, 20))

_SCHED_SALT = 0x3A7
_GLYPH_SALT = 0x3A8
_FRAME_SALT = 0x3A9  # must differ from _GLYPH_SALT: equal salts make remix() cancel to 0


class Column(NamedTuple):
    """Rain schedule for one column (times relative to the effect's start)."""

    speed: float  # rows per second
    tail: int  # trail length in rows, head included
    spawns: tuple[float, ...]  # when each drop's head sits just above row 0
    phase: float  # offsets this column's glyph re-pick times


@lru_cache(maxsize=16)
def schedule(
    seed: int,
    width: int,
    height: int,
    duration: float,
    speed: float,
    speed_var: float,
    spread: float,
    tail_len: int,
    drops: int,
) -> tuple[Column, ...]:
    """Drops per column, timed so every trail (head and tail) has left the
    bottom of the grid by ``duration``."""
    if duration <= 0:
        return tuple(Column(1.0, 1, (), 0.0) for _ in range(width))
    spread = min(0.95, max(0.0, spread))
    cols = []
    for x in range(width):
        u = [noise(seed, _SCHED_SALT, x, j) for j in range(4)]
        tail = max(1, round(tail_len * (0.75 + 0.5 * u[0])))
        first = u[1] * spread * duration
        travel_rows = height + tail
        v = speed * (1.0 + speed_var * (2.0 * u[2] - 1.0))
        v = max(v, travel_rows / (duration - first), 1e-6)  # fast enough to finish
        travel = travel_rows / v
        spawns = [first]
        for k in range(1, max(1, drops)):
            gap = (tail + 1 + noise(seed, _SCHED_SALT, x, 100 + k) * height) / v
            nxt = spawns[-1] + gap
            if nxt + travel > duration:
                break
            spawns.append(nxt)
        cols.append(Column(v, tail, tuple(spawns), u[3]))
    return tuple(cols)


@lru_cache(maxsize=16)
def _trail_colors(head: Color, tail: tuple[Color, ...], length: int) -> tuple[Color, ...]:
    return (head, *gradient(tail, length - 1))


@dataclass(frozen=True)
class Matrix:
    """Digital rain: drops with a bright ``head`` and a ``tail`` fading through
    the given colors (nearest the head first), falling at about ``speed`` rows/s.

    Every column gets a first drop within the first ``spread`` fraction of the
    duration, then up to ``drops - 1`` more if they fit. Speeds vary per column
    by ``speed_var`` and are raised where needed so every trail has left the
    bottom by ``start + duration``; from then on the output equals the input.
    Rain glyphs (from ``charset``) are re-picked ``rate`` times a second and
    cover whatever is underneath while passing.

    ``reveal=True``: the underlying grid is hidden until the column's first
    drop head has passed a row, so the rain paints the art in. Otherwise the
    rain falls over the visible grid.
    """

    start: float = 0.0
    duration: float = 1.5
    speed: float = 24.0
    speed_var: float = 0.3
    spread: float = 0.5
    tail_len: int = 8
    drops: int = 3
    rate: float = 12.0
    reveal: bool = True
    charset: str = MATRIX_CHARSET
    head: Color = MATRIX_HEAD
    tail: tuple[Color, ...] = MATRIX_TAIL
    seed: int = 0

    def __post_init__(self) -> None:
        if not self.charset:
            raise ValueError("charset must not be empty")
        if not self.tail:
            raise ValueError("tail needs at least one color")
        object.__setattr__(self, "tail", tuple(tuple(c) for c in self.tail))
        object.__setattr__(self, "head", tuple(self.head))

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def columns(self, width: int, height: int) -> tuple[Column, ...]:
        return schedule(
            self.seed, width, height, self.duration, self.speed, self.speed_var,
            self.spread, self.tail_len, self.drops,
        )

    def apply(self, grid: Grid, t: float) -> Grid:
        t = finite_t(t)
        w, h, cells = grid.width, grid.height, grid.cells
        if t >= self.end or not cells:
            return grid.copy()
        if t < self.start:
            return Grid(w, h) if self.reveal else grid.copy()

        elapsed = t - self.start
        out = [BLANK] * len(cells) if self.reveal else list(cells)
        rnd = cell_hashes(self.seed, _GLYPH_SALT, w, h)
        charset, nc = self.charset, len(self.charset)
        for x, col in enumerate(self.columns(w, h)):
            colors = _trail_colors(self.head, self.tail, col.tail)
            b = hash32(self.seed, _FRAME_SALT, x, time_bucket(t, self.rate, col.phase))
            for k, spawn in enumerate(col.spawns):
                head = math.floor((elapsed - spawn) * col.speed) - 1  # row -1 at spawn
                if head < 0:
                    break  # later drops have not entered either
                if k == 0 and self.reveal:
                    stop = x + min(head, h) * w  # rows above the head are revealed
                    out[x:stop:w] = cells[x:stop:w]
                for y in range(max(0, head - col.tail + 1), min(head, h - 1) + 1):
                    i = y * w + x
                    out[i] = Cell(charset[remix(rnd[i], b) % nc], colors[head - y], out[i].bg)
        return Grid(w, h, out)
