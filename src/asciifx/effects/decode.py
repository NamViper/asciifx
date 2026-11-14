"""Decode: art resolves out of scrambled glyphs, cell by cell."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ..core.grid import BLANK, Cell, Color, Grid
from .base import cell_hashes, finite_t, hash32, remix, time_bucket

DECODE_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#$%&*+=?@<>/\\|[]{}"
BRAILLE_CHARSET = "".join(chr(c) for c in range(0x2801, 0x2900))
"""Every non-empty braille pattern: scrambles braille art without losing its texture."""
DECODE_HIGHLIGHT: Color = (200, 255, 210)

_LOCK_SALT = 0xDEC0
_GLYPH_SALT = 0xDEC1


def column_extent(grid: Grid) -> tuple[int, int] | None:
    """(leftmost, rightmost) column holding a non-blank cell, or None if all blank."""
    w, cells = grid.width, grid.cells
    lo, hi = w, -1
    for y in range(grid.height):
        base = y * w
        for x in range(lo):
            c = cells[base + x]
            if c.ch != " " or c.bg is not None:
                lo = x
                break
        for x in range(w - 1, hi, -1):
            c = cells[base + x]
            if c.ch != " " or c.bg is not None:
                hi = x
                break
    return (lo, hi) if hi >= 0 else None


@lru_cache(maxsize=16)
def _lock_fractions(
    seed: int, width: int, height: int, sweep: float, x0: int, x1: int
) -> tuple[float, ...]:
    """Per cell: when it locks, as a fraction of the duration (0..1)."""
    sweep = min(1.0, max(0.0, sweep))
    span = x1 - x0
    col = [
        sweep * min(1.0, max(0.0, (x - x0) / span)) if span > 0 else 0.0 for x in range(width)
    ]
    rnd = cell_hashes(seed, _LOCK_SALT, width, height)
    scale = (1.0 - sweep) / 4294967296.0
    return tuple(col[i % width] + rnd[i] * scale for i in range(width * height))


@dataclass(frozen=True)
class Decode:
    """Every non-blank cell shows random glyphs until it "locks" to its real one.

    Lock times fall in [start, start + duration]: a random order (``sweep=0``),
    a left-to-right column sweep across the art (``sweep=1``), or a mix. Before
    locking a cell shows a glyph from ``charset`` that changes ``rate`` times a
    second, in ``highlight`` color (None: the cell's own fg), over the cell's bg.
    Blank cells stay blank.

    ``appear`` (0..1) makes the art materialize out of nothing: a cell stays
    blank until ``start + appear * (lock_time - start)``. With ``appear=0`` the
    scrambled art is visible from the beginning, also before ``start``.
    """

    start: float = 0.0
    duration: float = 1.0
    sweep: float = 0.6
    rate: float = 20.0
    charset: str = DECODE_CHARSET
    highlight: Color | None = DECODE_HIGHLIGHT
    appear: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        if not self.charset:
            raise ValueError("charset must not be empty")

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def lock_fractions(self, grid: Grid) -> tuple[float, ...] | None:
        """Per cell, when it locks as a fraction of ``duration`` (None if all blank).

        Cell i locks at ``start + duration * fractions[i]``.
        """
        extent = column_extent(grid)
        if extent is None:
            return None
        return _lock_fractions(self.seed, grid.width, grid.height, self.sweep, *extent)

    def apply(self, grid: Grid, t: float) -> Grid:
        t = finite_t(t)
        if t >= self.end:
            return grid.copy()
        locks = self.lock_fractions(grid)
        if locks is None:
            return grid.copy()

        p = (t - self.start) / self.duration if t >= self.start else -1.0
        appear = self.appear
        hl = self.highlight
        charset, nc = self.charset, len(self.charset)
        rnd = cell_hashes(self.seed, _GLYPH_SALT, grid.width, grid.height)
        b = hash32(self.seed, _GLYPH_SALT, time_bucket(t, self.rate))

        out = list(grid.cells)
        for i, c in enumerate(out):
            if c.ch == " " and c.bg is None:
                continue
            lock = locks[i]
            if lock <= p:
                continue
            if appear > 0 and p < appear * lock:
                out[i] = BLANK
            else:
                g = charset[remix(rnd[i], b) % nc]
                out[i] = Cell(g, c.fg if hl is None else hl, c.bg)
        return Grid(grid.width, grid.height, out)
