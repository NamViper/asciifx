"""Typewriter: reveal the grid cell by cell in reading order."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.grid import BLANK, Cell, Color, Grid

_EPS = 1e-9  # so float fuzz like 0.3 * 10 = 2.999... still counts 3 cells


def row_spans(grid: Grid) -> list[tuple[int, int, int]]:
    """(y, first, stop) per row with content: the cells from its first to its last
    non-blank cell. Rows that are entirely blank are left out."""
    w, cells = grid.width, grid.cells
    spans = []
    for y in range(grid.height):
        base = y * w
        a, b = base, base + w
        while a < b and cells[a].ch == " " and cells[a].bg is None:
            a += 1
        while b > a and cells[b - 1].ch == " " and cells[b - 1].bg is None:
            b -= 1
        if a < b:
            spans.append((y, a - base, b - base))
    return spans


@dataclass(frozen=True)
class Typewriter:
    """Types the grid out in reading order at ``cps`` cells per second.

    Each row is typed from its first to its last non-blank cell (leading and
    trailing blanks take no time; inner spaces do). Untyped cells are BLANK.
    While typing (``start <= t < end``) a ``cursor`` glyph sits just after the
    last typed cell, colored ``cursor_fg`` or, if that is None, like the last
    typed cell. Before ``start`` the grid is empty.

    ``duration``, if set, overrides ``cps``: the whole grid takes exactly that
    long, whatever its content. ``seed`` is accepted for a uniform interface;
    typing is fully deterministic.

    Typing order is computed from the grid passed to ``apply``, so put a
    Typewriter first in a Chain (before effects that blank cells out).
    """

    start: float = 0.0
    cps: float = 80.0
    duration: float | None = None
    cursor: str | None = "█"
    cursor_fg: Color | None = None
    seed: int = 0

    def __post_init__(self) -> None:
        if self.duration is None and self.cps <= 0:
            raise ValueError(f"cps must be positive, got {self.cps}")

    @property
    def end(self) -> float:
        """``start + duration``. In cps mode the end depends on the text: use ``end_for``."""
        if self.duration is None:
            raise ValueError("Typewriter.end depends on the grid in cps mode: use end_for(grid)")
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        if self.duration is not None:
            return self.end
        return self.start + sum(b - a for _, a, b in row_spans(grid)) / self.cps

    def typed(self, t: float, total: int) -> int:
        """Number of cells typed at time t, out of ``total``."""
        if t < self.start:
            return 0
        # Done from the end on, compared as times: at a large start, (t - start) * rate
        # can round to just under ``total``; also covers t = inf and duration = nan.
        if self.duration is None:
            if t >= self.start + total / self.cps:  # end_for(grid)
                return total
            rate = self.cps
        elif not self.duration > 0 or t >= self.end:
            return total
        else:
            rate = total / self.duration
        return min(total, int((t - self.start) * rate + _EPS))

    def apply(self, grid: Grid, t: float) -> Grid:
        spans = row_spans(grid)
        total = sum(b - a for _, a, b in spans)
        n = self.typed(t, total)
        if n >= total:
            return grid.copy()

        w, cells = grid.width, grid.cells
        out = [BLANK] * len(cells)
        left = n
        last: tuple[int, int] | None = None  # (x, y) of the last typed cell
        next_pos: tuple[int, int] = (spans[0][1], spans[0][0])  # (x, y) of the next cell
        for i, (y, a, b) in enumerate(spans):
            k = min(left, b - a)
            if k:
                lo = y * w + a
                out[lo : lo + k] = cells[lo : lo + k]
                last = (a + k - 1, y)
                left -= k
            if k < b - a:
                next_pos = (a + k, y)
                break
            if left == 0:
                next_pos = (spans[i + 1][1], spans[i + 1][0])  # n < total: a next row exists
                break

        if self.cursor is not None and t >= self.start:
            if last is None:
                x, y = next_pos
                ref = cells[y * w + x]
            else:
                x, y = last[0] + 1, last[1]
                ref = cells[y * w + last[0]]
                if x >= w:
                    x, y = next_pos
            fg = self.cursor_fg if self.cursor_fg is not None else ref.fg
            out[y * w + x] = Cell(self.cursor, fg)
        return Grid(grid.width, grid.height, out)
