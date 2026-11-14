"""Shockwave: an aspect-correct ring expands from a point, brightening and
pushing the cells it passes."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.easing import out_quad
from ..core.grid import Cell, Color, Grid
from .base import finite_t, lerp_color

SHOCK_COLOR: Color = (255, 230, 225)
SHOCK_RING: Color = (255, 70, 70)

_BLACK: Color = (0, 0, 0)
_DEFAULT_FG: Color = (204, 204, 204)
_LEVELS = 24  # brightening is quantized to this many steps (few distinct colors)

# Braille dot centres inside a cell (x right, y down, in cells) and their bits.
_DOTS = tuple(
    ((col + 0.5) / 2.0 - 0.5, (row + 0.5) / 4.0 - 0.5, bit)
    for col, bits in enumerate(((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80)))
    for row, bit in enumerate(bits)
)


@dataclass(frozen=True)
class Shockwave:
    """A ring expanding from ``center`` (fractions of the grid: x, y) over
    [start, start + duration].

    Distances are visual (cells are ``CELL_ASPECT`` = 2 times as tall as wide)
    so the ring is round on screen. Its radius grows to ``radius`` cell widths
    (None: far enough to clear the farthest corner), fast at first (ease out),
    while it fades. Cells within ``width`` behind the crest (half that ahead of
    it) are pushed outward by up to ``push`` cells and their glyphs pulled
    toward ``color`` by up to ``strength``; over blank cells a thin braille
    ring in ``ring`` color traces the crest (None: no ring line). Identity
    before ``start`` and from ``start + duration``. ``seed`` is accepted for a
    uniform interface; the wave is not random.
    """

    start: float = 0.0
    duration: float = 1.0
    center: tuple[float, float] = (0.5, 0.5)
    radius: float | None = None
    width: float = 3.0
    color: Color = SHOCK_COLOR
    strength: float = 0.85
    push: float = 1.5
    ring: Color | None = SHOCK_RING
    seed: int = 0

    def __post_init__(self) -> None:
        if self.width <= 0:
            raise ValueError(f"width must be positive, got {self.width}")
        object.__setattr__(self, "center", tuple(self.center))
        object.__setattr__(self, "color", tuple(self.color))
        if self.ring is not None:
            object.__setattr__(self, "ring", tuple(self.ring))

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def origin(self, width: int, height: int) -> tuple[float, float]:
        """The centre in cells (cell i spans [i, i + 1))."""
        return self.center[0] * width, self.center[1] * height

    def max_radius(self, width: int, height: int) -> float:
        if self.radius is not None:
            return max(0.0, self.radius)
        cx, cy = self.origin(width, height)
        return max(math.hypot(x - cx, 2.0 * (y - cy)) for x in (0, width) for y in (0, height)) + self.width

    def crest(self, t: float, width: int, height: int) -> tuple[float, float] | None:
        """(radius in cell widths, amplitude 0..1) at t, or None outside the window."""
        if t < self.start or t >= self.end or self.duration <= 0:
            return None
        u = (t - self.start) / self.duration
        return self.max_radius(width, height) * out_quad(u), (1.0 - u) ** 1.3

    def apply(self, grid: Grid, t: float) -> Grid:
        t = finite_t(t)
        w, h = grid.width, grid.height
        state = self.crest(t, w, h) if grid.cells else None
        if state is None:
            return grid.copy()
        r, amp = state
        cx, cy = self.origin(w, h)
        wb, wf = self.width, self.width * 0.5  # behind / ahead of the crest
        cells = grid.cells
        out = list(cells)
        push, strength, color = self.push * amp, self.strength * amp, self.color
        ring = lerp_color(_BLACK, self.ring, min(1.0, 1.4 * amp)) if self.ring is not None else None
        lit: dict[tuple[Color | None, int], Color] = {}
        outer, inner = r + wf, r - wb
        for y in range(h):
            dyv = 2.0 * (y + 0.5 - cy)
            if abs(dyv) >= outer:
                continue
            half = math.sqrt(outer * outer - dyv * dyv)
            spans = [(cx - half, cx + half)]
            if inner > 0 and abs(dyv) < inner:
                hole = math.sqrt(inner * inner - dyv * dyv)
                spans = [(cx - half, cx - hole), (cx + hole, cx + half)]
            base = y * w
            for lo, hi in spans:
                for x in range(max(0, math.floor(lo - 0.5)), min(w, math.ceil(hi - 0.5) + 1)):
                    dx = x + 0.5 - cx
                    d = math.hypot(dx, dyv)
                    off = d - r
                    f = 1.0 - (off / wf if off >= 0 else -off / wb)
                    if f <= 0.0:
                        continue
                    f = f * f * (3.0 - 2.0 * f)
                    c = cells[base + x]
                    s = push * f
                    if s >= 0.5 and d > 0:
                        sx = x - round(dx / d * s)
                        sy = y - round(dyv / d * s / 2.0)
                        if 0 <= sx < w and 0 <= sy < h:
                            c = cells[sy * w + sx]
                    if c.ch != " ":
                        q = round(_LEVELS * strength * f)
                        if q > 0:
                            fg = lit.get((c.fg, q))
                            if fg is None:
                                fg = lit[(c.fg, q)] = lerp_color(c.fg or _DEFAULT_FG, color, min(1.0, q / _LEVELS))
                            c = Cell(c.ch, fg, c.bg)
                    elif ring is not None and c.bg is None and abs(off) < 1.0:
                        bits = 0
                        for ox, oy, bit in _DOTS:
                            if abs(math.hypot(dx + ox, dyv + 2.0 * oy) - r) < 0.3:
                                bits |= bit
                        if bits:
                            c = Cell(chr(0x2800 + bits), ring)
                    out[base + x] = c
        return Grid(w, h, out)
