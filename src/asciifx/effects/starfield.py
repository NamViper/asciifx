"""Starfield: 3D stars flying out of the centre of the grid toward the viewer."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, NamedTuple

from ..core.grid import CELL_ASPECT, Cell, Color, Grid
from .base import finite_t, gradient, hash32

STAR_RAMP = ".·+*#"
"""Star glyphs from the farthest to the nearest (single-width only)."""

STAR_COLORS: tuple[Color, ...] = ((70, 75, 110), (150, 160, 210), (255, 255, 255))
"""Star colors from the farthest (dim blue-grey) to the nearest (white)."""

STREAK_GLYPHS = "-\\|/"
"""Streak glyphs for motion that is mostly horizontal, down-right, vertical, up-right."""

STAR_SPEED = 0.35
"""Depth units per second at ``warp=1`` (stars live in depth (0, 1])."""

_NEAR = 0.04  # stars closer than this are behind the screen edge: not drawn
_LEVELS = 32  # brightness quantisation steps
_MAX_STREAK = 10  # longest streak, in cells
_TAN_22 = 0.4142  # tan(22.5 deg): splits directions into the 4 streak glyphs

_STAR_SALT = 0x57A1  # per-star constants; no per-frame hashing


class Star(NamedTuple):
    """A star projected at some t: grid position (floats), depth in (0, 1], id."""

    x: float
    y: float
    z: float
    index: int


@lru_cache(maxsize=16)
def _stars(seed: int, n: int) -> tuple[tuple[float, float, float, float], ...]:
    """(X, Y, z0, appear) per star: X, Y in [-1, 1), z0 and appear in [0, 1).

    The world position is (X, Y * ry), ry scaling Y to the grid's aspect ratio.
    """
    out = []
    inv = 1.0 / 4294967296.0
    for j in range(n):
        u = [hash32(seed, _STAR_SALT, j, k) * inv for k in range(4)]
        out.append((2.0 * u[0] - 1.0, 2.0 * u[1] - 1.0, u[2], u[3]))
    return tuple(out)


@lru_cache(maxsize=16)
def _tables(
    ramp: str, colors: tuple[Color, ...]
) -> tuple[tuple[Cell, ...], tuple[tuple[Cell, ...], ...]]:
    """Head cell per brightness level, and streak cells per direction per level."""
    shades = gradient(colors, _LEVELS)
    n = len(ramp)
    heads = tuple(Cell(ramp[min(n - 1, q * n // _LEVELS)], shades[q]) for q in range(_LEVELS))
    streaks = tuple(tuple(Cell(g, shades[q]) for q in range(_LEVELS)) for g in STREAK_GLYPHS)
    return heads, streaks


def project(width: int, height: int, wx: float, wy: float, z: float) -> tuple[float, float]:
    """Grid position (column, row; floats) of the world point (wx, wy) at depth z > 0.

    World units are half the grid's width as seen on screen: one unit spans
    width / 2 columns across but only width / (2 * CELL_ASPECT) rows down, so
    the ~2:1 cells don't stretch the field. (1, 0) at z = 1 is on the right edge.
    """
    k = width / (2.0 * z)
    return (width - 1) / 2 + wx * k, (height - 1) / 2 + wy * k / CELL_ASPECT


def _direction(dx: float, dy: float) -> int:
    """Index into STREAK_GLYPHS for motion by (dx columns, dy rows)."""
    vx, vy = abs(dx), abs(dy) * CELL_ASPECT  # as seen on screen
    if vy < _TAN_22 * vx:
        return 0  # -
    if vx < _TAN_22 * vy:
        return 2  # |
    return 1 if (dx > 0) == (dy > 0) else 3  # \ (rows grow downward) or /


def _smooth(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


@dataclass(frozen=True)
class Starfield:
    """Stars flying toward the viewer from the centre of the grid.

    Each star has a fixed position (X, Y) from a hash and a depth
    ``z = (z0 - speed * (t - start)) mod 1``, with ``speed = STAR_SPEED * warp``;
    it is drawn at its perspective projection, aspect-corrected for ~2:1 cells.
    Glyph (from ``ramp``) and color (along ``colors``) go from far to near.
    About ``density`` stars per cell exist in the 3D volume, and roughly a
    third of them are in view at any time. Near, fast stars trail a short
    streak (the path covered in the last ``streak`` seconds; 0 disables it).

    ``layer="under"`` only draws in blank cells, so art stays in front;
    ``"over"`` draws stars over everything.

    Stars fade in over ``fade`` seconds after ``start`` (each one at its own
    moment) and out over the last ``fade`` seconds of ``duration``; from
    ``start + duration`` on the output equals the input. ``duration=None``
    runs forever (``end`` is inf).
    """

    start: float = 0.0
    duration: float | None = 4.0
    warp: float = 1.0
    density: float = 0.1
    streak: float = 0.06
    fade: float = 0.6
    layer: Literal["under", "over"] = "under"
    ramp: str = STAR_RAMP
    colors: tuple[Color, ...] = STAR_COLORS
    seed: int = 0

    def __post_init__(self) -> None:
        if not self.ramp:
            raise ValueError("ramp must not be empty")
        if not self.colors:
            raise ValueError("colors needs at least one color")
        if self.layer not in ("under", "over"):
            raise ValueError(f"layer must be 'under' or 'over', got {self.layer!r}")
        object.__setattr__(self, "colors", tuple(tuple(c) for c in self.colors))

    @property
    def end(self) -> float:
        return math.inf if self.duration is None else self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def envelope(self, t: float) -> float:
        """0 before start and from the end on, ramping to 1 over ``fade`` seconds."""
        if t < self.start or t >= self.end:
            return 0.0
        e = t - self.start
        fade = self.fade
        if self.duration is not None:
            fade = min(fade, self.duration / 2)
        if fade <= 0:
            return 1.0
        return _smooth(min(1.0, e / fade, (self.end - t) / fade))

    @property
    def speed(self) -> float:
        """Depth units per second."""
        return STAR_SPEED * self.warp

    def stars(self, width: int, height: int, t: float) -> list[Star]:
        """Stars in view at t (projected onto the grid area), farthest first."""
        env = self.envelope(t)
        if env <= 0.0 or width <= 0 or height <= 0:
            return []
        n = round(self.density * width * height)
        travel = self.speed * (t - self.start)
        ry = height * CELL_ASPECT / width  # world Y range: fills the grid's height at z = 1
        xmax, ymax = width - 0.5, height - 0.5
        out = []
        for j, (wx, wy, z0, appear) in enumerate(_stars(self.seed, n)):
            if appear >= env:  # each star fades in / out at its own envelope level
                continue
            z = (z0 - travel) % 1.0
            if z < _NEAR:
                continue
            x, y = project(width, height, wx, wy * ry, z)
            if -0.5 <= x < xmax and -0.5 <= y < ymax:
                out.append(Star(x, y, z, j))
        out.sort(key=lambda s: -s.z)
        return out

    def apply(self, grid: Grid, t: float) -> Grid:
        if t >= self.end:  # before finite_t: an endless starfield is still the identity at t = inf
            return grid.copy()
        t = finite_t(t)
        w, h, cells = grid.width, grid.height, grid.cells
        visible = self.stars(w, h, t)
        if not visible:
            return grid.copy()
        heads, streaks = _tables(self.ramp, self.colors)
        under = self.layer == "under"
        out = list(cells)
        top = _LEVELS - 1
        # Where each star was ``streak`` seconds ago, as a depth offset.
        dz = self.speed * self.streak
        cx, cy = (w - 1) / 2, (h - 1) / 2
        for x, y, z, _ in visible:  # far to near: nearer stars win shared cells
            q = min(top, int((1.0 - z) * _LEVELS))
            ix, iy = int(x + 0.5), int(y + 0.5)
            if dz > 0 and z + dz < 1.0:
                # The star was at z + dz: on the same ray, (z / (z + dz)) as far out.
                k = 1.0 - z / (z + dz)
                ddx, ddy = (x - cx) * k, (y - cy) * k  # distance covered since, in cells
                span = max(abs(ddx), abs(ddy))
                length = min(_MAX_STREAK, int(span))
                if length >= 2:
                    glyphs = streaks[_direction(ddx, ddy)]
                    ux, uy = ddx / span, ddy / span
                    for s in range(length, 0, -1):  # from the tail end toward the head
                        fx, fy = x - ux * s, y - uy * s
                        if not (-0.5 <= fx < w - 0.5 and -0.5 <= fy < h - 0.5):
                            continue
                        i = int(fy + 0.5) * w + int(fx + 0.5)
                        if i == iy * w + ix:
                            continue
                        c = cells[i]
                        if under and not (c.ch == " " and c.bg is None):
                            continue
                        out[i] = glyphs[int(q * 0.7 * (1.0 - s / (length + 1)))]
            i = iy * w + ix
            c = cells[i]
            if under and not (c.ch == " " and c.bg is None):
                continue
            out[i] = heads[q]
        return Grid(w, h, out)
