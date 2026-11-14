"""Easing curves (u in 0..1 -> 0..1) and small timing helpers."""

from __future__ import annotations

import math
from collections.abc import Callable

type Easing = Callable[[float], float]


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def progress(t: float, start: float, duration: float) -> float:
    """Fraction (clamped 0..1) of the way through [start, start + duration] at time t."""
    if duration <= 0:
        return 1.0 if t >= start else 0.0
    return clamp((t - start) / duration)


def ping_pong(x: float, length: float) -> float:
    """Triangle wave: 0 -> length -> 0 ... as x grows (period 2 * length)."""
    if length <= 0:
        return 0.0
    m = x % (2 * length)
    return m if m <= length else 2 * length - m


def linear(u: float) -> float:
    return u


def in_quad(u: float) -> float:
    return u * u


def out_quad(u: float) -> float:
    return 1 - (1 - u) * (1 - u)


def in_out_quad(u: float) -> float:
    return 2 * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 2 / 2


def in_cubic(u: float) -> float:
    return u**3


def out_cubic(u: float) -> float:
    return 1 - (1 - u) ** 3


def in_out_cubic(u: float) -> float:
    return 4 * u**3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def in_out_sine(u: float) -> float:
    return -(math.cos(math.pi * u) - 1) / 2


EASINGS: dict[str, Easing] = {
    f.__name__: f
    for f in (linear, in_quad, out_quad, in_out_quad, in_cubic, out_cubic, in_out_cubic, in_out_sine)
}
