"""Hologram: rolling scanlines, a red/cyan ghost and row dropouts over the art."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.grid import BLANK, Cell, Color, Grid
from .base import hash32, noise, time_bucket

HOLO_GHOST: tuple[Color, Color] = ((255, 30, 70), (0, 225, 255))
"""Ghost colors: the copy shifted left (red), the copy shifted right (cyan)."""

HOLO_SCAN: Color = (255, 255, 255)
"""What the rolling scanlines brighten glyphs toward."""

_DEFAULT_FG: Color = (200, 200, 200)  # stands in for "terminal default" when shading
_BAND = 1.6  # half-width of a rolling scanline, in rows
_GAP = 6.0  # rows of travel off-screen before a scanline comes round again
_SALT = 0x4010


def _mix(a: Color, b: Color, u: float) -> Color:
    return (
        round(a[0] + (b[0] - a[0]) * u),
        round(a[1] + (b[1] - a[1]) * u),
        round(a[2] + (b[2] - a[2]) * u),
    )


@dataclass(frozen=True)
class Hologram:
    """The art as a flickering hologram projection.

    * Every other row is dimmed (interlace) and two bright scanlines roll down
      the grid at ``speed`` rows per second, brightening glyphs toward ``scan``.
    * A ghost copy of the glyphs sits one cell to the left in ``ghost[0]``
      (red) and one cell to the right in ``ghost[1]`` (cyan), drawn only into
      blank cells, so the art gets chromatic fringes without being covered.
      Now and then the ghost jumps two cells out for a moment.
    * Short dropouts: ``rate`` times a second the pattern is re-rolled, and a
      band of 1-3 rows may vanish or flicker (dimmed and shifted one cell).

    ``intensity`` (0 = no-op, 1 = heavy) scales all of it. Identity before
    ``start`` and from ``start + duration`` on; ``duration=None`` is endless
    (``end`` is inf).
    """

    start: float = 0.0
    duration: float | None = 2.0
    intensity: float = 0.6
    seed: int = 0
    speed: float = 18.0
    rate: float = 15.0
    ghost: tuple[Color, Color] = HOLO_GHOST
    scan: Color = HOLO_SCAN

    def __post_init__(self) -> None:
        if len(self.ghost) != 2:
            raise ValueError("ghost needs two colors: (left, right)")
        object.__setattr__(self, "ghost", tuple(tuple(c) for c in self.ghost))
        object.__setattr__(self, "scan", tuple(self.scan))

    @property
    def end(self) -> float:
        return math.inf if self.duration is None else self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def scanlines(self, height: int, t: float) -> tuple[float, ...]:
        """Row positions (floats, may be off-grid) of the rolling scanlines at t."""
        period = height + _GAP
        q = ((t - self.start) * self.speed) % period
        return (q - _GAP / 2, (q + period / 2) % period - _GAP / 2)

    def dropouts(self, height: int, t: float) -> dict[int, str]:
        """Rows hit by a dropout at t: row -> "vanish" or "flicker"."""
        k = min(1.0, self.intensity)
        if height <= 0 or k <= 0:
            return {}
        b = time_bucket(t - self.start, self.rate)
        out: dict[int, str] = {}
        for j in range(2):
            r = hash32(self.seed, _SALT, b, j)
            if (r & 0xFFFF) / 65536.0 >= 0.3 * k:
                continue
            y0, rows = (r >> 16) % height, 1 + (r >> 8) % 3
            kind = "vanish" if (r >> 28) & 1 else "flicker"
            for y in range(y0, min(height, y0 + rows)):
                out[y] = kind
        return out

    def apply(self, grid: Grid, t: float) -> Grid:
        w, h, cells = grid.width, grid.height, grid.cells
        k = min(1.0, self.intensity)
        if t < self.start or t >= self.end or k <= 0 or not cells:
            return grid.copy()

        b = time_bucket(t - self.start, self.rate)
        flick = 1.0 - 0.18 * k * noise(self.seed, _SALT + 1, b)  # whole-image brightness wobble
        spread = 2 if noise(self.seed, _SALT + 2, b) < 0.12 * k else 1  # ghost jumps out
        lines = self.scanlines(h, t)
        drops = self.dropouts(h, t)
        left_c, right_c = (_mix((0, 0, 0), c, 0.25 + 0.45 * k) for c in self.ghost)
        scan = self.scan
        out = list(cells)
        for y in range(h):
            base = y * w
            row = cells[base : base + w]
            glow = max(0.0, 1.0 - min(abs(y - p) for p in lines) / _BAND) * 0.8 * k
            dim = 1.0 - flick * (1.0 - 0.32 * k * (y & 1))  # interlace: odd rows darker
            drop = drops.get(y)
            if drop == "vanish":
                out[base : base + w] = [BLANK] * w
                continue
            if drop == "flicker":
                dim = 1.0 - 0.3 * flick
            seen: dict[Color | None, Color] = {}
            new = []
            for c in row:
                if c.ch == " ":
                    new.append(c)
                    continue
                fg = seen.get(c.fg)
                if fg is None:
                    fg = _mix(_mix(c.fg or _DEFAULT_FG, (0, 0, 0), dim), scan, glow)
                    seen[c.fg] = fg
                new.append(Cell(c.ch, fg, c.bg))
            # The ghost: glyphs copied sideways into blank cells, red left, cyan right.
            for x, c in enumerate(row):
                if c.ch == " ":
                    continue
                for gx, color in ((x - spread, left_c), (x + spread, right_c)):
                    if 0 <= gx < w:
                        under = row[gx]
                        if under.ch == " " and under.bg is None:
                            new[gx] = Cell(c.ch, color)
            if drop == "flicker":  # torn sideways by a cell
                new = [BLANK, *new[:-1]] if (y + b) & 1 else [*new[1:], BLANK]
            out[base : base + w] = new
        return Grid(w, h, out)
