"""Procedural sources: frames computed purely from (t, width, height)."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from ..core.easing import ping_pong
from ..core.grid import CELL_ASPECT, Cell, Color, Grid
from ..core.layers import Layer, compose

LOGO = "╭─────────╮\n│ asciifx │\n╰─────────╯"

_GOLDEN = 0.618033988749895


def bounce_color(bounces: int) -> Color:
    """A distinct, bright color per bounce (hue stepped by the golden ratio)."""
    r, g, b = colorsys.hsv_to_rgb((0.55 + bounces * _GOLDEN) % 1.0, 0.65, 1.0)
    return round(r * 255), round(g * 255), round(b * 255)


@dataclass
class Bounce:
    """A sprite bouncing off the screen edges, changing color on every bounce.

    ``speed`` is horizontal cells/second; vertical speed is divided by the cell
    aspect ratio so the sprite moves at the same visual speed on both axes.
    """

    text: str = LOGO
    speed: float = 16.0
    sprite_bg: Color | None = (18, 18, 30)
    dot: Cell | None = Cell("·", (70, 70, 90))
    dot_spacing: int = 6  # columns between dots; rows use spacing / CELL_ASPECT

    def position(self, t: float, width: int, height: int) -> tuple[int, int, int]:
        """(x, y, number of wall bounces so far) at time t."""
        sprite_w, sprite_h = self._size()
        span_x, span_y = max(0, width - sprite_w), max(0, height - sprite_h)
        dist_x = self.speed * t
        dist_y = self.speed / CELL_ASPECT * t
        bounces = sum(int(d // span) for d, span in ((dist_x, span_x), (dist_y, span_y)) if span)
        return round(ping_pong(dist_x, span_x)), round(ping_pong(dist_y, span_y)), bounces

    def frame(self, t: float, width: int, height: int) -> Grid:
        x, y, bounces = self.position(t, width, height)
        sprite = Grid.from_text(self.text, fg=bounce_color(bounces), bg=self.sprite_bg)
        return compose(width, height, [Layer(self._background(width, height)), Layer(sprite, x, y)])

    def _size(self) -> tuple[int, int]:
        lines = self.text.split("\n")
        return max(len(line) for line in lines), len(lines)

    def _background(self, width: int, height: int) -> Grid:
        grid = Grid(width, height)
        if self.dot is not None and self.dot_spacing > 0:
            step_x = self.dot_spacing
            step_y = max(1, round(self.dot_spacing / CELL_ASPECT))
            for row in range(step_y // 2, height, step_y):
                for col in range(step_x // 2, width, step_x):
                    grid.set(col, row, self.dot)
        return grid
