"""Fire: flames rising from the bottom of the grid, a pure function of t."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from ..core.grid import Cell, Color, Grid
from .base import GOLDEN32, M32, gradient

FIRE_RAMP = " .:░▒▓█"
"""Glyphs from the faintest flame to the hottest (index 0 is never drawn)."""

FIRE_RAMP_ASCII = " .,:;=+*#%@"
"""Plain-ASCII alternative to ``FIRE_RAMP``."""

FIRE_COLORS: tuple[Color, ...] = (
    (0, 0, 0), (110, 8, 0), (210, 32, 0), (255, 120, 0), (255, 205, 45), (255, 255, 225),
)
"""Classic flame: black -> dark red -> red -> orange -> yellow -> white."""

BLOOD_FIRE_COLORS: tuple[Color, ...] = ((0, 0, 0), (100, 0, 0), (220, 20, 60), (255, 215, 225))
"""Blood flame: black -> dark red -> crimson -> pink-white."""

_LEVELS = 64  # intensity quantisation steps
_PAD = 5 * _LEVELS  # the cell table covers intensities in [-5, 3); apply() stays in [-4.0, 1.2]

# Shape of the flames (tuned by eye).
_COARSE = (5.0, 9.0)  # lattice spacing (columns, rows) of the tongue-shaping noise
_FINE = (3.0, 4.0)  # ... and of the brightness noise
_FINE_SPEED = 1.7  # the fine noise rises this much faster than ``speed``
_TONGUE_MIN, _TONGUE_RANGE = 0.4, 1.2  # falloff rate per column height: 0.4..1.6
_FLICK_SPACING, _FLICK_AMOUNT = 10.0, 0.3  # per-column height flicker: +-15%
_SWAY = 2  # max sideways sway of the upper flames, in cells

# Distinct salts: lattice values for each noise field.
_COARSE_SALT = 0xF1E1
_FINE_SALT = 0xF1E2
_FLICK_SALT = 0xF1E3


@lru_cache(maxsize=1024)
def _lattice(seed: int, salt: int, k: int, n: int) -> tuple[float, ...]:
    """``noise(seed, salt, j, k)`` for j in range(n): one row of the value-noise lattice."""
    # hash32(seed, salt, j, k) inlined, with the (seed, salt) prefix computed once.
    g, c, m = GOLDEN32, 0x7F4A7C15, M32
    h0 = (seed * g + c) & m
    h0 = ((h0 ^ (salt & m)) * g + c) & m
    kk = k & m
    out = []
    for j in range(n):
        h = ((h0 ^ j) * g + c) & m
        h = ((h ^ kk) * g + c) & m
        h ^= h >> 16
        h = (h * 0x7FEB352D) & m
        h ^= h >> 15
        h = (h * 0x846CA68B) & m
        out.append((h ^ (h >> 16)) / 4294967296.0)
    return tuple(out)


def _smooth(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


@lru_cache(maxsize=64)
def _xmap(width: int, spacing: float) -> tuple[tuple[int, ...], tuple[float, ...], int]:
    """Per column: lattice index, smoothstep weight towards the next index; lattice size."""
    idx, wts = [], []
    for x in range(width):
        u = x / spacing
        i = int(u)
        idx.append(i)
        wts.append(_smooth(u - i))
    return tuple(idx), tuple(wts), (idx[-1] + 2 if idx else 1)


def _noise_row(
    seed: int, salt: int, y: float, xmap: tuple[tuple[int, ...], tuple[float, ...], int]
) -> list[float]:
    """Smooth value noise in [0, 1) along one row at lattice height ``y``."""
    idx, wts, n = xmap
    k = math.floor(y)
    fy = _smooth(y - k)
    a, b = _lattice(seed, salt, k, n), _lattice(seed, salt, k + 1, n)
    col = [p + (q - p) * fy for p, q in zip(a, b)]
    dif = [q - p for p, q in zip(col, col[1:])]
    return [col[i] + dif[i] * u for i, u in zip(idx, wts)]


@lru_cache(maxsize=16)
def _cell_table(ramp: str, colors: tuple[Color, ...]) -> tuple[Cell | None, ...]:
    """Cell for every quantised intensity, padded so any value in [-5, 3) indexes it."""
    shades = gradient(colors, _LEVELS)
    last = len(ramp) - 1
    core: list[Cell | None] = [None]
    for q in range(1, _LEVELS):
        g = ramp[max(1, min(last, round(q * last / (_LEVELS - 1))))]
        core.append(Cell(g, shades[q]))
    return (None,) * _PAD + tuple(core) + (core[-1],) * (2 * _LEVELS)


@dataclass(frozen=True)
class Fire:
    """Flames rising from the bottom of the grid.

    A two-octave value-noise field scrolls upward (the finer octave faster, so
    the flames churn rather than slide) and is shaped by a falloff that fades
    the flames out ``height`` of the way up the grid. Each column's flame
    height flickers smoothly ``flicker`` times a second. Intensity maps to a
    glyph from ``ramp`` and a color along ``colors`` (coolest stop first; use
    ``BLOOD_FIRE_COLORS`` for a blood-red fire).

    ``layer="under"`` only draws in blank cells, so art stays in front;
    ``"over"`` draws flames over everything.

    The flames grow in over ``fade`` seconds after ``start`` and die down over
    the last ``fade`` seconds of ``duration``; from ``start + duration`` on the
    output equals the input. ``duration=None`` burns forever (``end`` is inf).
    """

    start: float = 0.0
    duration: float | None = 4.0
    height: float = 0.55
    speed: float = 14.0  # rows per second the coarse octave rises
    flicker: float = 5.0
    fade: float = 0.6
    layer: Literal["under", "over"] = "under"
    ramp: str = FIRE_RAMP
    colors: tuple[Color, ...] = FIRE_COLORS
    seed: int = 0

    def __post_init__(self) -> None:
        if len(self.ramp) < 2:
            raise ValueError("ramp needs a blank plus at least one glyph")
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
        u = min(1.0, e / fade, (self.end - t) / fade)
        return _smooth(u)

    def apply(self, grid: Grid, t: float) -> Grid:
        w, h, cells = grid.width, grid.height, grid.cells
        env = self.envelope(t)
        if env <= 0.0 or not cells or self.height <= 0:
            return grid.copy()
        e = t - self.start
        seed = self.seed
        reach = max(1.0, self.height * h * env)  # typical flame height in rows

        # Each column's height flickers: smooth noise over (x, time).
        flick = _noise_row(seed, _FLICK_SALT, e * self.flicker, _xmap(w, _FLICK_SPACING))
        lo = 1.0 - _FLICK_AMOUNT / 2
        inv = [1.0 / (reach * (lo + _FLICK_AMOUNT * f)) for f in flick]
        # Tongues reach at most 1 / _TONGUE_MIN column heights.
        top = min(h, math.ceil(reach * (lo + _FLICK_AMOUNT) / _TONGUE_MIN) + 1)

        wide = w + 2 * _SWAY  # noise rows are sampled wider, then shifted per row
        xc, xf = _xmap(wide, _COARSE[0]), _xmap(wide, _FINE[0])
        vc = self.speed / _COARSE[1]  # lattice rows per second
        vf = self.speed * _FINE_SPEED / _FINE[1]
        table = _cell_table(self.ramp, self.colors)
        glv, pad = math.sqrt(env) * _LEVELS, float(_PAD)
        k0, k1 = _TONGUE_MIN + _TONGUE_RANGE, _TONGUE_RANGE
        under = self.layer == "under"
        out = list(cells)
        for yy in range(top):  # rows counted up from the bottom
            s = _SWAY + round(_SWAY * min(1.0, yy / reach) * math.sin(yy * 0.35 - e * 4.0))
            nc = _noise_row(seed, _COARSE_SALT, yy / _COARSE[1] - e * vc, xc)[s : s + w]
            nf = _noise_row(seed, _FINE_SALT, yy / _FINE[1] - e * vf, xf)[s : s + w]
            # Coarse noise stretches each column's falloff into tongues; fine
            # noise modulates brightness so the hot base breaks into lumps.
            hot = [
                table[int(((1.0 - (z * z if (z := yy * iv * (k0 - k1 * a)) < 2.0 else 4.0))
                           * (0.55 + 0.75 * b) - 0.1) * glv + pad)]
                for iv, a, b in zip(inv, nc, nf)
            ]
            base = (h - 1 - yy) * w
            row = cells[base : base + w]
            if under:
                out[base : base + w] = [
                    f if f is not None and c.ch == " " and c.bg is None else c
                    for f, c in zip(hot, row)
                ]
            else:
                out[base : base + w] = [c if f is None else f for f, c in zip(hot, row)]
        return Grid(w, h, out)
