"""Glitch: short bursts of row tearing, junk glyphs and RGB-split colors."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.grid import BLANK, Cell, Color, Grid
from .base import hash32, hash_table, noise, remix32, time_bucket

GLITCH_COLORS: tuple[Color, ...] = ((0, 255, 255), (255, 0, 255), (255, 40, 40))
GLITCH_CHARSET = "░▒▓█#$%&@!?/\\<>=+*~^"

_SALT = 0x6117


@dataclass(frozen=True)
class Glitch:
    """A few short bursts within [start, start + duration]; identity in between.

    ``bursts`` bursts of ``burst_min``..``burst_max`` seconds each, one placed
    at random inside each equal slice of the duration (so they never overlap).
    During a burst the pattern changes ``rate`` times a second; each pattern:
      * tears a few bands of 1-3 rows sideways by 1-3 cells, some recolored
        with one of ``colors`` for an RGB-split look,
      * replaces some visible cells with junk glyphs from ``charset``,
      * swaps some glyph colors to one of ``colors``.
    ``intensity`` (0 = no-op, 1 = heavy) scales how many rows and cells are hit.
    """

    start: float = 0.0
    duration: float = 1.0
    bursts: int = 3
    burst_min: float = 0.05
    burst_max: float = 0.12
    intensity: float = 0.6
    rate: float = 30.0
    colors: tuple[Color, ...] = GLITCH_COLORS
    charset: str = GLITCH_CHARSET
    seed: int = 0

    def __post_init__(self) -> None:
        if not self.charset:
            raise ValueError("charset must not be empty")
        if not self.colors:
            raise ValueError("colors needs at least one color")
        object.__setattr__(self, "colors", tuple(tuple(c) for c in self.colors))

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def windows(self) -> list[tuple[float, float]]:
        """(begin, end) of every burst, in time order; a burst is active for begin <= t < end."""
        if self.bursts <= 0 or self.duration <= 0:
            return []
        slot = self.duration / self.bursts
        out = []
        for k in range(self.bursts):
            u = noise(self.seed, _SALT, k, 0)
            length = min(slot, self.burst_min + (self.burst_max - self.burst_min) * u)
            begin = self.start + k * slot + noise(self.seed, _SALT, k, 1) * (slot - length)
            out.append((begin, begin + length))
        return out

    def burst_at(self, t: float) -> int | None:
        """Index of the burst active at t, if any."""
        for k, (begin, end) in enumerate(self.windows()):
            if begin <= t < end:
                return k
        return None

    def apply(self, grid: Grid, t: float) -> Grid:
        w, h, cells = grid.width, grid.height, grid.cells
        if t >= self.end or self.intensity <= 0 or not cells:
            return grid.copy()
        k = self.burst_at(t)
        if k is None:
            return grid.copy()

        n = len(cells)
        colors, nc = self.colors, len(self.colors)
        charset, nch = self.charset, len(self.charset)
        frame = hash32(self.seed, _SALT, k, time_bucket(t, self.rate))
        out = list(cells)

        # Tear bands of rows sideways, some in a split color.
        for j in range(max(1, round(3 * self.intensity))):
            r = hash32(frame, 1, j)
            y0, rows = r % h, 1 + (r >> 8) % 3
            dx = (1 + (r >> 12) % 3) * (-1 if (r >> 16) & 1 else 1)
            tint = colors[(r >> 18) % nc] if (r >> 17) & 1 else None
            for y in range(y0, min(h, y0 + rows)):
                row = out[y * w : (y + 1) * w]
                if abs(dx) >= w:
                    row = [BLANK] * w
                elif dx > 0:
                    row = [BLANK] * dx + row[: w - dx]
                else:
                    row = row[-dx:] + [BLANK] * -dx
                if tint is not None:
                    row = [c if c.ch == " " else Cell(c.ch, tint, c.bg) for c in row]
                out[y * w : (y + 1) * w] = row

        # Junk glyphs on visible cells.
        for h_j in hash_table(self.seed, _SALT + 2, round(0.08 * self.intensity * n)):
            r = remix32(h_j, frame)
            i = r % n
            c = out[i]
            if c.ch != " " or c.bg is not None:
                out[i] = Cell(charset[(r >> 12) % nch], c.fg, c.bg)

        # Color swaps on glyphs.
        for h_j in hash_table(self.seed, _SALT + 3, round(0.15 * self.intensity * n)):
            r = remix32(h_j, frame)
            i = r % n
            c = out[i]
            if c.ch != " ":
                out[i] = Cell(c.ch, colors[(r >> 12) % nc], c.bg)
        return Grid(w, h, out)
