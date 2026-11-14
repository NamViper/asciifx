"""The Effect protocol, chaining, and the deterministic noise all effects share.

An effect turns a grid into "that grid as it looks at time t": ``apply(grid, t)``
returns a new grid of the same size, never mutates its input, and is a pure
function of (effect params, grid, t). Once ``t >= end_for(grid)`` the output
equals the input, so a finished effect can stay in a pipeline for free.

Randomness comes from integer hashing (``hash32`` / ``noise``), never from the
``random`` module or the clock, so every frame is reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from ..core.grid import Cell, Color, Grid

M32 = 0xFFFFFFFF
"""Mask for 32-bit arithmetic in the hashing helpers."""

GOLDEN32 = 0x9E3779B1
"""Odd 32-bit multiplier (2^32 / golden ratio) used to scramble hashes."""

_INV32 = 1.0 / 4294967296.0


class Effect(Protocol):
    def apply(self, grid: Grid, t: float) -> Grid:
        """``grid`` as it looks at ``t`` seconds: a new grid of the same size."""
        ...

    @property
    def end(self) -> float:
        """Grid-independent time from which the effect is the identity."""
        ...

    def end_for(self, grid: Grid) -> float:
        """Time from which ``apply(grid, t)`` equals ``grid`` (may depend on content)."""
        ...


def hash32(seed: int, *ints: int) -> int:
    """Deterministic 32-bit hash of ``seed`` and any number of integers.

    Combines with multiply-add, then finishes with the "lowbias32" mixer so
    neighbouring inputs (x, x + 1, ...) give unrelated outputs.
    """
    h = (seed * GOLDEN32 + 0x7F4A7C15) & M32
    for i in ints:
        h = ((h ^ (i & M32)) * GOLDEN32 + 0x7F4A7C15) & M32
    h ^= h >> 16
    h = (h * 0x7FEB352D) & M32
    h ^= h >> 15
    h = (h * 0x846CA68B) & M32
    return h ^ (h >> 16)


def noise(seed: int, *ints: int) -> float:
    """Deterministic, stateless value in [0, 1) for (seed, *ints)."""
    return hash32(seed, *ints) * _INV32


@lru_cache(maxsize=32)
def cell_hashes(seed: int, salt: int, width: int, height: int) -> tuple[int, ...]:
    """``hash32(seed, salt, x, y)`` for every cell, row-major (cached per size).

    Hot loops combine these with one per-frame hash ``b`` via ``remix(h, b)``
    instead of hashing every cell every frame.
    """
    # hash32 inlined, with the (seed, salt, x) prefix computed once per column.
    g, c, m = GOLDEN32, 0x7F4A7C15, M32
    h0 = (seed * g + c) & m
    h0 = ((h0 ^ (salt & m)) * g + c) & m
    cols = [((h0 ^ x) * g + c) & m for x in range(width)]
    out = []
    for y in range(height):
        for hx in cols:
            h = ((hx ^ y) * g + c) & m
            h ^= h >> 16
            h = (h * 0x7FEB352D) & m
            h ^= h >> 15
            h = (h * 0x846CA68B) & m
            out.append(h ^ (h >> 16))
    return tuple(out)


@lru_cache(maxsize=32)
def hash_table(seed: int, salt: int, n: int) -> tuple[int, ...]:
    """``hash32(seed, salt, j)`` for j in range(n) (cached)."""
    return tuple(hash32(seed, salt, j) for j in range(n))


def remix(h: int, b: int) -> int:
    """Cheap 16-bit value from two already-random 32-bit hashes (for hot loops)."""
    return (((h ^ b) * GOLDEN32) & M32) >> 16


def remix32(h: int, b: int) -> int:
    """Like ``remix`` but keeps 32 bits."""
    r = ((h ^ b) * GOLDEN32) & M32
    return r ^ (r >> 16)


def is_blank(cell: Cell) -> bool:
    """A space with no background: nothing visible (and transparent when layered)."""
    return cell.ch == " " and cell.bg is None


def lerp_color(a: Color, b: Color, u: float) -> Color:
    return (
        round(a[0] + (b[0] - a[0]) * u),
        round(a[1] + (b[1] - a[1]) * u),
        round(a[2] + (b[2] - a[2]) * u),
    )


def gradient(stops: tuple[Color, ...], n: int) -> tuple[Color, ...]:
    """``n`` colors evenly spaced along the piecewise-linear path through ``stops``."""
    if n <= 0 or not stops:
        return ()
    if len(stops) == 1 or n == 1:
        return (stops[0],) * n
    out = []
    last = len(stops) - 1
    for i in range(n):
        pos = i * last / (n - 1)
        k = min(int(pos), last - 1)
        out.append(lerp_color(stops[k], stops[k + 1], pos - k))
    return tuple(out)


def time_bucket(t: float, rate: float, phase: float = 0.0) -> int:
    """Index of the 1/rate-second slot containing t (0 when rate <= 0)."""
    return int((t * rate + phase) // 1) if rate > 0 else 0


T_LIMIT = 1e9
"""Seconds (about 31 years): where ``finite_t`` puts t = +-inf."""


def finite_t(t: float) -> float:
    """``t`` safe for int(), floor() and hashing: NaN reads as 0.0 (the start of
    the timeline), +-inf as +-T_LIMIT; finite values pass through unchanged."""
    if math.isfinite(t):
        return t
    return 0.0 if math.isnan(t) else math.copysign(T_LIMIT, t)


@dataclass(frozen=True)
class Chain:
    """Apply ``effects`` in order, all at the same t."""

    effects: tuple[Effect, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "effects", tuple(self.effects))

    def apply(self, grid: Grid, t: float) -> Grid:
        out = grid
        for effect in self.effects:
            out = effect.apply(out, t)
        return grid.copy() if out is grid else out

    @property
    def end(self) -> float:
        return max((e.end for e in self.effects), default=0.0)

    def end_for(self, grid: Grid) -> float:
        return max((e.end_for(grid) for e in self.effects), default=0.0)


def chain(*effects: Effect) -> Chain:
    return Chain(effects)
