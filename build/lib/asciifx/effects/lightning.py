"""Lightning: a jagged, branching bolt cracks down from the top of the screen,
a full-screen white flash, then a fading afterglow."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..core.grid import Cell, Color, Grid
from .base import hash32, lerp_color

BOLT_CORE: Color = (255, 255, 255)
BOLT_GLOW: Color = (255, 60, 70)
FLASH_COLOR: Color = (255, 255, 255)
SILHOUETTE: Color = (40, 0, 0)

_BLACK: Color = (0, 0, 0)
_DEFAULT_FG: Color = (204, 204, 204)
_SALT = 0x11E7

# Braille dot bits by (column, row) inside a cell (2 x 4 dots).
_DOT = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))


type _Path = list[tuple[float, float]]
type _Cells = tuple[tuple[int, tuple[tuple[float, int], ...]], ...]  # per cell index: (order, dot bit), earliest first


def _jag(points: _Path, depth: int, rough: float, seed: int, salt: int) -> _Path:
    """Midpoint displacement: each pass splits every segment and pushes the midpoint
    sideways (perpendicular to the segment) by up to ``rough`` x its length."""
    for d in range(depth):
        out = [points[0]]
        for k, ((x0, y0), (x1, y1)) in enumerate(zip(points, points[1:])):
            dx, dy = x1 - x0, y1 - y0
            n = math.hypot(dx, dy) or 1.0
            off = (hash32(seed, salt, d, k) / 4294967296.0 - 0.5) * 2.0 * rough * n
            out.append(((x0 + x1) / 2 - dy / n * off, (y0 + y1) / 2 + dx / n * off))
            out.append((x1, y1))
        points = out
    return points


def _raster(points: list[tuple[float, float]], thick: bool) -> list[tuple[int, int]]:
    """Dots along a polyline (in path order), optionally two dots wide."""
    dots: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        n = max(1, math.ceil(max(abs(x1 - x0), abs(y1 - y0))))
        for s in range(n + 1):
            u = s / n
            p = (round(x0 + (x1 - x0) * u), round(y0 + (y1 - y0) * u))
            for q in ((p, (p[0] + 1, p[1])) if thick else (p,)):
                if q not in seen:
                    seen.add(q)
                    dots.append(q)
    return dots


@dataclass(frozen=True)
class Lightning:
    """A lightning strike over the grid, all within [start, start + duration].

    * leader (``leader`` s): a jagged bolt (braille dots, aspect-correct) cracks
      from the top edge down to ``target`` (fractions of the grid: x, y), with
      ``branches`` forks splitting off sideways. The path depends only on
      ``seed`` and the grid size.
    * flash (``flash`` s, 1-2 frames at 30 FPS): the whole screen turns
      ``flash_color``; the art shows as a dark silhouette.
    * afterglow (the rest): the bolt flickers once more, then fades from
      ``color`` through ``glow`` to black, and the art, lit up by the flash,
      cools back to its colors (``afterglow`` = how strongly, 0..1).

    The bolt is drawn over the art, replacing the cells it crosses.
    Identity before ``start`` and from ``start + duration`` on.
    """

    start: float = 0.0
    duration: float = 1.2
    target: tuple[float, float] = (0.5, 0.7)
    leader: float = 0.14
    flash: float = 0.07
    branches: int = 4
    roughness: float = 0.28
    color: Color = BOLT_CORE
    glow: Color = BOLT_GLOW
    flash_color: Color = FLASH_COLOR
    silhouette: Color = SILHOUETTE
    afterglow: float = 0.7
    seed: int = 0
    _cache: dict[Any, Any] = field(init=False, repr=False, compare=False, hash=False, default_factory=dict)

    def __post_init__(self) -> None:
        if self.leader < 0 or self.flash < 0:
            raise ValueError("leader and flash must be >= 0")
        for name in ("color", "glow", "flash_color", "silhouette"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "target", tuple(self.target))

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    # -- geometry ------------------------------------------------------------

    @property
    def strike(self) -> float:
        """When the bolt reaches the target and the flash starts."""
        return self.start + min(self.leader, max(0.0, self.duration))

    def bolt(self, width: int, height: int) -> tuple[tuple[int, int, float], ...]:
        """Every bolt dot as (dot x, dot y, order): dots are 2 x 4 per cell and
        ``order`` (0 at the top .. 1 at the target) is when the leader reaches it."""
        key = (width, height)
        hit = self._cache.get(key)
        if hit is not None:
            return hit[0]
        dw, dh = 2 * width, 4 * height
        tx = min(dw - 1.0, max(0.0, self.target[0] * dw))
        ty = min(dh - 1.0, max(0.0, self.target[1] * dh))
        seed = self.seed
        drift = (hash32(seed, _SALT, 1) / 4294967296.0 - 0.5) * 0.7 * ty
        sx = min(dw - 1.0, max(0.0, tx + drift))
        length = math.hypot(tx - sx, ty)
        depth = max(1, math.ceil(math.log2(max(2.0, length / 3.0))))
        main = _jag([(sx, 0.0), (tx, ty)], depth, self.roughness, seed, 2)
        main_dots = _raster(main, thick=True)
        n = max(1, len(main_dots) - 1)
        dots: dict[tuple[int, int], float] = {}
        for k, d in enumerate(main_dots):
            dots.setdefault(d, k / n)
        for b in range(max(0, self.branches)):
            h = hash32(seed, _SALT, 10, b)
            k = int((0.12 + 0.6 * (h & 0xFFFF) / 65536.0) * (len(main) - 1))
            (x0, y0), (x1, y1) = main[k], main[min(k + 1, len(main) - 1)]
            ang = math.atan2(y1 - y0, x1 - x0) if (x1, y1) != (x0, y0) else math.pi / 2
            side = 1.0 if (h >> 16) & 1 else -1.0
            ang += side * (0.45 + 0.5 * ((h >> 17) & 0x3FF) / 1024.0)
            blen = length * (0.14 + 0.2 * ((h >> 8) & 0xFF) / 256.0)
            end = (x0 + math.cos(ang) * blen, y0 + abs(math.sin(ang)) * blen)
            bdepth = max(1, math.ceil(math.log2(max(2.0, blen / 3.0))))
            path = _jag([(x0, y0), end], bdepth, self.roughness * 1.2, seed, 100 + b)
            bdots = _raster(path, thick=False)
            fork = k / max(1, len(main) - 1)
            m = max(1, len(bdots) - 1)
            for j, d in enumerate(bdots):
                dots.setdefault(d, fork + (1.0 - fork) * 0.8 * j / m)
        out = tuple((x, y, o) for (x, y), o in dots.items() if 0 <= x < dw and 0 <= y < dh)
        cells = self._group(out, width)
        if len(self._cache) > 8:
            self._cache.clear()
        self._cache[key] = (out, cells)
        return out

    @staticmethod
    def _group(dots: tuple[tuple[int, int, float], ...], width: int) -> _Cells:
        """Per cell index: its dots as (order, bit), earliest first."""
        cells: dict[int, list[tuple[float, int]]] = {}
        for x, y, o in dots:
            cells.setdefault((y // 4) * width + x // 2, []).append((o, _DOT[x % 2][y % 4]))
        return tuple((i, tuple(sorted(v))) for i, v in sorted(cells.items()))

    def _cells(self, width: int, height: int) -> _Cells:
        self.bolt(width, height)
        cells: _Cells = self._cache[(width, height)][1]
        return cells

    # -- rendering -----------------------------------------------------------

    def bolt_level(self, t: float) -> float:
        """Bolt brightness in the afterglow: 1 = white-hot .. 0 = gone."""
        a = t - self.strike - self.flash
        rest = self.end - self.strike - self.flash
        if a < 0 or rest <= 0:
            return 1.0
        if 0.06 <= a < 0.1:  # the channel dims, then a second return stroke
            return 0.35
        u = min(1.0, a / rest)
        return (1.0 - u) ** 1.6

    def apply(self, grid: Grid, t: float) -> Grid:
        w, h = grid.width, grid.height
        if t < self.start or t >= self.end or not grid.cells:
            return grid.copy()
        strike = self.strike
        if strike <= t < strike + self.flash:
            return self._flash(grid)
        out = list(grid.cells)
        if t < strike:  # the leader cracking down: only dots it has reached, a little dimmer
            p = (t - self.start) / (strike - self.start)
            self._draw(out, w, h, p, lerp_color(self.glow, self.color, 0.7))
            return Grid(w, h, out)
        a = t - strike - self.flash
        lit = self.afterglow * math.exp(-a / 0.22)
        if lit > 0.02:
            q = round(lit * 24)
            cache: dict[Color | None, Color] = {}
            white = self.color
            for i, c in enumerate(out):
                if c.ch != " ":
                    fg = cache.get(c.fg)
                    if fg is None:
                        fg = cache[c.fg] = lerp_color(c.fg or _DEFAULT_FG, white, q / 24)
                    out[i] = Cell(c.ch, fg, c.bg)
        b = self.bolt_level(t)
        if b > 0.02:
            if b > 0.5:
                col = lerp_color(self.glow, self.color, (b - 0.5) / 0.5)
            else:
                col = lerp_color(_BLACK, self.glow, b / 0.5)
            self._draw(out, w, h, 1.0, col)
        return Grid(w, h, out)

    def _draw(self, out: list[Cell], w: int, h: int, progress: float, color: Color) -> None:
        for i, dots in self._cells(w, h):
            bits = 0
            for o, bit in dots:
                if o > progress:
                    break
                bits |= bit
            if bits:  # replaces the art: a crisp crack even through dense braille
                out[i] = Cell(chr(0x2800 + bits), color, out[i].bg)

    def _flash(self, grid: Grid) -> Grid:
        bg, dark = self.flash_color, self.silhouette
        blank = Cell(" ", None, bg)
        return Grid(grid.width, grid.height, [blank if c.ch == " " else Cell(c.ch, dark, bg) for c in grid.cells])
