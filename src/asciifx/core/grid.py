"""Character grid: a width x height array of cells (glyph + fg/bg color)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

type Color = tuple[int, int, int]
"""24-bit RGB color, each channel 0..255."""

CELL_ASPECT = 2.0
"""Terminal cells are roughly twice as tall as they are wide (height / width)."""


@dataclass(frozen=True, slots=True)
class Cell:
    """One terminal cell. ``None`` colors mean "terminal default"."""

    ch: str = " "
    fg: Color | None = None
    bg: Color | None = None


BLANK = Cell()


def _printable(ch: str) -> str:
    """Replace control characters so they can't break the screen layout."""
    return " " if ch < " " or "\x7f" <= ch < "\xa0" else ch


@dataclass(eq=True)
class Grid:
    """A ``width`` x ``height`` grid of cells, stored row-major.

    Drawing methods (``set``, ``put_text``) silently clip anything outside the
    grid, so callers can draw partially off-screen sprites without checks.
    """

    width: int
    height: int
    cells: list[Cell] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError(f"grid size must be non-negative, got {self.width}x{self.height}")
        size = self.width * self.height
        if not self.cells:
            self.cells = [BLANK] * size
        elif len(self.cells) != size:
            raise ValueError(f"expected {size} cells, got {len(self.cells)}")

    @classmethod
    def filled(cls, width: int, height: int, cell: Cell) -> Grid:
        return cls(width, height, [cell] * (width * height))

    @classmethod
    def from_text(cls, text: str, fg: Color | None = None, bg: Color | None = None) -> Grid:
        """Build a grid from multi-line text; short lines are padded with BLANK cells."""
        lines = text.split("\n")
        width = max((len(line) for line in lines), default=0)
        grid = cls(width, len(lines))
        grid.put_text(0, 0, text, fg, bg)
        return grid

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, x: int, y: int) -> Cell:
        if not self.in_bounds(x, y):
            raise IndexError(f"({x}, {y}) outside {self.width}x{self.height} grid")
        return self.cells[y * self.width + x]

    def set(self, x: int, y: int, cell: Cell) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.cells[y * self.width + x] = cell

    def put_text(
        self, x: int, y: int, text: str, fg: Color | None = None, bg: Color | None = None
    ) -> None:
        """Write text at (x, y); each ``\\n`` starts a new line at column x."""
        for dy, line in enumerate(text.split("\n")):
            row = y + dy
            if not 0 <= row < self.height:
                continue
            base = row * self.width
            for dx, ch in enumerate(line):
                col = x + dx
                if 0 <= col < self.width:
                    self.cells[base + col] = Cell(_printable(ch), fg, bg)

    def fill(self, cell: Cell) -> None:
        self.cells = [cell] * (self.width * self.height)

    def copy(self) -> Grid:
        return Grid(self.width, self.height, self.cells.copy())

    def rows(self) -> Iterator[list[Cell]]:
        w = self.width
        for y in range(self.height):
            yield self.cells[y * w : (y + 1) * w]

    def to_text(self, rstrip: bool = True) -> str:
        """Plain characters only (no colors), one line per row."""
        lines = ("".join(c.ch for c in row) for row in self.rows())
        return "\n".join(line.rstrip() if rstrip else line for line in lines)

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible form: a palette of distinct cells + one palette index per cell."""
        index: dict[Cell, int] = {}
        cells = [index.setdefault(c, len(index)) for c in self.cells]
        palette = [[c.ch, list(c.fg) if c.fg else None, list(c.bg) if c.bg else None] for c in index]
        return {"width": self.width, "height": self.height, "palette": palette, "cells": cells}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Grid:
        palette = [
            Cell(ch, tuple(fg) if fg else None, tuple(bg) if bg else None)  # type: ignore[arg-type]
            for ch, fg, bg in data["palette"]
        ]
        return cls(data["width"], data["height"], [palette[i] for i in data["cells"]])
