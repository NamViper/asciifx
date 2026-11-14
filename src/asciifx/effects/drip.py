"""Drip: drops of blood swell, run down the screen and pool at the bottom."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..core.grid import Cell, Color, Grid
from .base import hash32, lerp_color

DRIP_TRAIL: Color = (130, 0, 6)
DRIP_HEAD: Color = (235, 25, 35)
DRIP_POOL: Color = (105, 0, 4)

_BLACK: Color = (0, 0, 0)
_SALT = 0xD819

# Braille bits: one dot column, top to bottom.
_COL = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))
# Pool glyphs from thin (edge) to thick (middle): the bottom 1, 2 or 3 dot rows filled.
_POOL = ("⣀", "⣤", "⣶")


class Drop(NamedTuple):
    """One drip's schedule (absolute seconds) and path."""

    x: int  # origin cell
    y: int
    launch: float  # starts to swell at the origin
    run: float  # starts to run down (launch + form)
    bottom: float  # reaches the bottom row (inf if it never does)
    pool: int  # max pool half-width, in cells
    sway: tuple[int, ...]  # dot column (0 = the origin cell's left one) per row below the origin


@dataclass(frozen=True)
class Drip:
    """Drops of blood swell at ``origins`` ((x, y) cells), run down and pool.

    With no ``origins``, ``count`` drops hang from the bottom edge of the art
    (just below the lowest glyph of seeded columns in its middle 60 %, at
    least 3 columns apart; on the bottom row if the art reaches it). Drop k starts to
    swell at a seeded time within ``stagger`` s of ``start``, runs after
    ``form`` s with ``speed`` rows/s plus ``accel`` rows/s², swaying by half a
    cell now and then, and leaves a ``trail``-colored braille streak behind
    its ``head``. At the bottom row it spreads into a pool up to ``pool``
    cells either side (seeded, at least 2). Everything is drawn over the grid;
    with ``stain`` braille art under a drop keeps its dots and turns red, and
    other glyphs it runs over keep their shape in its color.
    The last ``fade`` s it all fades to black; identity before ``start`` and
    from ``start + duration`` on. A pure function of t.
    """

    start: float = 0.0
    duration: float = 4.0
    origins: tuple[tuple[int, int], ...] = ()
    count: int = 4
    stagger: float = 0.9
    form: float = 0.35
    speed: float = 4.0
    accel: float = 12.0
    trail: Color = DRIP_TRAIL
    head: Color = DRIP_HEAD
    pool_color: Color = DRIP_POOL
    pool: int = 6
    fade: float = 0.4
    stain: bool = True
    seed: int = 0
    _cache: dict[Any, Any] = field(init=False, repr=False, compare=False, hash=False, default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "origins", tuple((int(x), int(y)) for x, y in self.origins))
        for name in ("trail", "head", "pool_color"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.speed <= 0 and self.accel <= 0:
            raise ValueError("a drop needs speed > 0 or accel > 0")

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    # -- schedule ------------------------------------------------------------

    def fall(self, tau: float) -> float:
        """Rows a drop has run ``tau`` seconds after it starts to run."""
        return self.speed * tau + 0.5 * self.accel * tau * tau if tau > 0 else 0.0

    def _time_to(self, rows: float) -> float:
        """Seconds of running to cover ``rows``."""
        a, v = self.accel, self.speed
        if a <= 0:
            return rows / v
        return (-v + math.sqrt(v * v + 2.0 * a * rows)) / a

    def auto_origins(self, grid: Grid) -> tuple[tuple[int, int], ...]:
        """``count`` origins on the bottom edge of the art (seeded columns)."""
        w, h, cells = grid.width, grid.height, grid.cells
        lows: dict[int, int] = {}
        for i in range(len(cells) - 1, -1, -1):
            x = i % w
            if x not in lows and cells[i].ch != " ":
                lows[x] = i // w
        if not lows:
            return ()
        xs = sorted(lows)
        lo, hi = xs[0], xs[-1]
        lo, hi = lo + (hi - lo) // 5, hi - (hi - lo) // 5
        pick = sorted((x for x in xs if lo <= x <= hi), key=lambda x: hash32(self.seed, _SALT, 50, x)) or xs
        out: list[tuple[int, int]] = []
        for x in pick:  # seeded order, at least 3 columns apart
            if len(out) >= self.count:
                break
            if all(abs(x - ox) >= 3 for ox, _ in out):
                out.append((x, min(lows[x] + 1, h - 1)))  # just below the art (or on the bottom row)
        return tuple(out)

    def drops(self, grid: Grid) -> tuple[Drop, ...]:
        """Every drop's schedule for this grid's size (and content, for auto origins)."""
        origins = self.origins or self.auto_origins(grid)
        key = (grid.width, grid.height, origins)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        h = grid.height
        out = []
        for k, (x, y) in enumerate(origins):
            r = hash32(self.seed, _SALT, k)
            launch = self.start + self.stagger * (r & 0xFFFF) / 65536.0
            run = launch + self.form
            rows = h - 1 - y
            bottom = run + self._time_to(rows) if rows > 0 else run
            pool = max(2, round(self.pool * (0.55 + 0.45 * ((r >> 16) & 0xFF) / 255.0)))
            col, sway = 2 * x + ((r >> 24) & 1), []
            for j in range(max(0, rows + 1)):
                s = hash32(self.seed, _SALT, k, j + 1)
                if s % 5 == 0:  # now and then the stream shifts half a cell
                    col += 1 if (s >> 8) & 1 else -1
                    col = min(2 * x + 2, max(2 * x - 1, col))
                sway.append(col)
            out.append(Drop(x, y, launch, run, bottom, pool, tuple(sway)))
        res = tuple(out)
        if len(self._cache) > 8:
            self._cache.clear()
        self._cache[key] = res
        return res

    # -- rendering -----------------------------------------------------------

    def apply(self, grid: Grid, t: float) -> Grid:
        w, h = grid.width, grid.height
        if t < self.start or t >= self.end or not grid.cells:
            return grid.copy()
        drops = self.drops(grid)
        out = list(grid.cells)
        dim = 1.0
        if self.fade > 0 and t > self.end - self.fade:
            dim = max(0.0, (self.end - t) / self.fade)

        def col(c: Color) -> Color:
            return c if dim >= 1.0 else lerp_color(_BLACK, c, dim)

        def put(x: int, y: int, bits: int, color: Color) -> None:
            if 0 <= x < w and 0 <= y < h and bits:
                i = y * w + x
                prev = out[i]
                if 0x2800 <= ord(prev.ch) <= 0x28FF:
                    if self.stain or prev.fg == color:
                        bits |= ord(prev.ch) - 0x2800  # blood soaks into braille art
                elif prev.ch != " " and self.stain:  # other glyphs keep their shape, stained red
                    out[i] = Cell(prev.ch, color, prev.bg)
                    return
                out[i] = Cell(chr(0x2800 + bits), color, prev.bg)

        trail, head = col(self.trail), col(self.head)
        pools: list[tuple[int, int]] = []
        for d in drops:
            if t < d.launch or not 0 <= d.y < h:
                continue
            if t < d.run:  # swelling: 1..4 dots grow down the origin cell
                n = 1 + min(3, int(4 * (t - d.launch) / max(1e-9, self.form)))
                c = d.sway[0]
                put(c // 2, d.y, sum(_COL[c % 2][:n]), head)
                continue
            dots = 4 * self.fall(t - d.run) + 3  # head position in dot rows below the origin's top
            last = min(4 * (h - d.y) - 1, int(dots))
            for j, c in enumerate(d.sway):
                top = 4 * j
                if top > last:
                    break
                k = last - top  # the lowest wet dot row in this cell (>= 4: all four)
                bits = sum(_COL[c % 2][: min(4, k + 1)])
                if k < 4:  # the head: a fat drop, two dots wide at its bottom
                    other = _COL[1 - c % 2]
                    bits |= other[k] | (other[k - 1] if k > 0 else 0)
                put(c // 2, d.y + j, bits, head if k < 6 else trail)
            if t >= d.bottom:
                hw = min(d.pool, round(0.5 + 6.0 * math.sqrt(t - d.bottom)))
                pools.append((d.sway[-1] // 2, hw))
        if pools:
            pool = col(self.pool_color)
            depth: dict[int, int] = {}
            for x, hw in pools:
                for dx in range(-hw, hw + 1):
                    k = 2 if abs(dx) * 3 < hw else 1 if abs(dx) * 3 < 2 * hw else 0
                    depth[x + dx] = max(depth.get(x + dx, -1), k)
            base = (h - 1) * w
            for x, k in depth.items():
                if 0 <= x < w:
                    out[base + x] = Cell(_POOL[k], pool, out[base + x].bg)
        return Grid(w, h, out)
