"""Per-logo feature coordinates (eyes, jaw) from a sidecar JSON next to the logo,
and the two pure effects that use them: glowing eyes and a chattering jaw.

``skull_logo.png`` -> ``skull_logo.asciifx.json``::

    {
      "version": 1,
      "eyes": [{"cx": 0.36, "cy": 0.55, "rx": 0.075, "ry": 0.045}, ...],
      "jaw": {"y": 0.79, "x0": 0.28, "x1": 0.70}
    }

Coordinates are fractions (0..1) of the logo grid as rendered with
``startup.LOGO_PARAMS`` (autocropped), x of its width and y of its height, with
cell ``i`` covering ``[i, i + 1)``: the same fractions as on the autocropped
image, so they hold for every size and style. ``eyes`` are ellipses (centre and
radii), ``jaw`` is the line between the teeth: everything below ``y`` between
``x0`` and ``x1`` is the lower jaw. Both keys are optional (but not both missing).

``load_features(logo)`` reads and validates the sidecar (None if there is none);
``LogoFeatures.place(x, y, w, h)`` turns the fractions into screen cells for a
logo drawn at (x, y) with size w x h. ``EyeGlow`` and ``JawChatter`` are pure
``apply(grid, t) -> Grid`` effects. Stdlib only.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core.grid import BLANK, Cell, Color, Grid
from .effects.base import finite_t, lerp_color

SIDECAR_SUFFIX = ".asciifx.json"
FORMAT_VERSION = 1
MAX_EYES = 4

_DEFAULT_FG: Color = (204, 204, 204)


# -- data ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ellipse:
    """An ellipse in logo fractions: centre (cx, cy), radii rx (of the width), ry (of the height)."""

    cx: float
    cy: float
    rx: float
    ry: float


@dataclass(frozen=True)
class JawLine:
    """The line between the teeth, in logo fractions: row ``y`` from ``x0`` to ``x1``."""

    y: float
    x0: float
    x1: float


@dataclass(frozen=True)
class PlacedEye:
    """An eye in screen cells: centre (cx, cy) and radii (cell centres sit at i + 0.5)."""

    cx: float
    cy: float
    rx: float
    ry: float


@dataclass(frozen=True)
class PlacedJaw:
    """The lower jaw in screen cells: rows ``y`` and below, columns [x0, x1)."""

    y: int
    x0: int
    x1: int


@dataclass(frozen=True)
class LogoFeatures:
    eyes: tuple[Ellipse, ...] = ()
    jaw: JawLine | None = None

    def place(self, x: int, y: int, width: int, height: int) -> tuple[tuple[PlacedEye, ...], PlacedJaw | None]:
        """Eyes and jaw in screen cells for a ``width`` x ``height`` logo drawn at (x, y)."""
        eyes = tuple(
            PlacedEye(x + e.cx * width, y + e.cy * height, max(0.6, e.rx * width), max(0.6, e.ry * height))
            for e in self.eyes
        )
        jaw = None
        if self.jaw is not None:
            j = self.jaw
            x0 = x + math.floor(j.x0 * width)
            x1 = max(x0 + 1, x + math.ceil(j.x1 * width))
            jaw = PlacedJaw(y + min(height - 1, round(j.y * height)), x0, x1)
        return eyes, jaw


def sidecar_path(logo_path: str | os.PathLike[str]) -> Path:
    """``dir/name.png`` -> ``dir/name.asciifx.json``."""
    p = Path(logo_path).expanduser()
    return p.with_name(p.stem + SIDECAR_SUFFIX)


def _fraction(obj: Mapping[str, Any], key: str, where: str, *, positive: bool = False) -> float:
    v = obj.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{where}.{key} must be a number, got {v!r}")
    if not 0.0 <= v <= 1.0 or (positive and v <= 0.0):
        raise ValueError(f"{where}.{key} must be in {'(0, 1]' if positive else '[0, 1]'}, got {v!r}")
    return float(v)


def parse_features(data: Any) -> LogoFeatures:
    """Validate a decoded sidecar; raises ValueError naming the bad field."""
    if not isinstance(data, Mapping):
        raise ValueError("features must be a JSON object")
    version = data.get("version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        raise ValueError(f"unsupported features version {version!r} (expected {FORMAT_VERSION})")
    raw_eyes = data.get("eyes", [])
    if not isinstance(raw_eyes, list) or len(raw_eyes) > MAX_EYES:
        raise ValueError(f"eyes must be a list of at most {MAX_EYES} ellipses")
    eyes = []
    for k, e in enumerate(raw_eyes):
        if not isinstance(e, Mapping):
            raise ValueError(f"eyes[{k}] must be an object with cx, cy, rx, ry")
        w = f"eyes[{k}]"
        eyes.append(Ellipse(_fraction(e, "cx", w), _fraction(e, "cy", w),
                            _fraction(e, "rx", w, positive=True), _fraction(e, "ry", w, positive=True)))
    jaw = None
    raw_jaw = data.get("jaw")
    if raw_jaw is not None:
        if not isinstance(raw_jaw, Mapping):
            raise ValueError("jaw must be an object with y, x0, x1")
        jaw = JawLine(_fraction(raw_jaw, "y", "jaw"), _fraction(raw_jaw, "x0", "jaw"), _fraction(raw_jaw, "x1", "jaw"))
        if jaw.x0 >= jaw.x1:
            raise ValueError(f"jaw.x0 must be less than jaw.x1, got {jaw.x0} >= {jaw.x1}")
    if not eyes and jaw is None:
        raise ValueError("features need eyes, a jaw, or both")
    return LogoFeatures(tuple(eyes), jaw)


def load_features(logo_path: str | os.PathLike[str]) -> LogoFeatures | None:
    """The logo's sidecar features, or None if it has no sidecar.

    Raises ValueError if the sidecar exists but is not valid (bad JSON or fields)."""
    path = sidecar_path(logo_path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"{path}: {exc}") from None
    try:
        return parse_features(json.loads(text))
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise ValueError(f"{path}: {exc}") from None


# -- effects ------------------------------------------------------------------------


def heartbeat(t: float, bpm: float = 72.0) -> float:
    """0..1 pulse: a strong "lub", then a weaker "dub", then rest, ``bpm`` times a minute."""
    period = 60.0 / bpm
    ph = t % period
    lub = math.exp(-((ph - 0.07) / 0.05) ** 2) + math.exp(-((ph - 0.07 - period) / 0.05) ** 2)
    dub = 0.65 * math.exp(-((ph - 0.31) / 0.06) ** 2)
    return min(1.0, lub + dub)


EYE_RED: Color = (235, 10, 20)
EYE_CORE: Color = (255, 190, 160)
EYE_DARK: Color = (70, 0, 4)


@dataclass(frozen=True)
class EyeGlow:
    """The eyes ignite at ``start`` and then glow, pulsing like a heartbeat.

    ``eyes`` are screen-cell ellipses (``PlacedEye``). Over ``ignite`` seconds
    the glow flares up past full (the core turns white-hot), then settles into
    a ``bpm`` heartbeat between ``low`` and 1. Inside each ellipse glyphs turn
    ``color`` (``core`` at the centre when bright) and blank cells fill with
    ``fill``; out to ``halo`` x the radius glyphs are tinted red. Endless when
    ``duration`` is None, else the identity from ``start + duration``.
    """

    start: float = 0.0
    duration: float | None = None
    eyes: tuple[PlacedEye, ...] = ()
    ignite: float = 0.6
    bpm: float = 72.0
    low: float = 0.55
    color: Color = EYE_RED
    core: Color = EYE_CORE
    dark: Color = EYE_DARK
    fill: str = "⣿"
    halo: float = 1.8
    seed: int = 0
    _cache: dict[Any, Any] = field(init=False, repr=False, compare=False, hash=False, default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "eyes", tuple(self.eyes))
        if len(self.fill) != 1:
            raise ValueError("fill must be one glyph")

    @property
    def end(self) -> float:
        return math.inf if self.duration is None else self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def level(self, t: float) -> float:
        """Glow brightness at t: 0 before ``start``, up to ~1.5 in the flare, then low..1."""
        if t < self.start or t >= self.end:
            return 0.0
        tau = t - self.start
        beat = self.low + (1.0 - self.low) * heartbeat(tau - self.ignite, self.bpm)
        if tau >= self.ignite or self.ignite <= 0:
            return beat
        # Ignition: a stutter (on, off), a flare past full, then down into the beat.
        u = tau / self.ignite
        if u < 0.1:
            return 0.8
        if u < 0.17:
            return 0.12
        if u < 0.32:
            return 1.6 * math.sqrt((u - 0.17) / 0.15)
        s = (u - 0.32) / 0.68
        return 1.6 + (beat - 1.6) * s * s * (3.0 - 2.0 * s)

    def _cells(self, w: int, h: int) -> tuple[tuple[int, float], ...]:
        """(cell index, normalized distance) of every cell within ``halo`` of an eye."""
        key = (w, h)
        cells = self._cache.get(key)
        if cells is None:
            best: dict[int, float] = {}
            for e in self.eyes:
                rx, ry = e.rx * self.halo, e.ry * self.halo
                for y in range(max(0, math.floor(e.cy - ry)), min(h, math.ceil(e.cy + ry) + 1)):
                    for x in range(max(0, math.floor(e.cx - rx)), min(w, math.ceil(e.cx + rx) + 1)):
                        d = math.hypot((x + 0.5 - e.cx) / e.rx, (y + 0.5 - e.cy) / e.ry)
                        if d < self.halo and d < best.get(y * w + x, math.inf):
                            best[y * w + x] = d
            cells = tuple(sorted(best.items()))
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = cells
        return cells

    def apply(self, grid: Grid, t: float) -> Grid:
        if t >= self.end:  # before finite_t: an endless glow is still the identity at t = inf
            return grid.copy()
        lv = self.level(finite_t(t))
        if lv <= 0.0 or not self.eyes or not grid.cells:
            return grid.copy()
        out = list(grid.cells)
        red, core, dark, halo = self.color, self.core, self.dark, self.halo
        for i, d in self._cells(grid.width, grid.height):
            c = out[i]
            if d < 1.0:
                g = lv * (1.0 - 0.55 * d * d)  # brightest in the middle
                if g <= 1.0:
                    col = lerp_color(dark, red, g)
                else:
                    col = lerp_color(red, core, min(1.0, (g - 1.0) * 1.6))
                ch = c.ch if c.ch != " " else (self.fill if d < 0.8 else " ")
                if ch != " ":
                    out[i] = Cell(ch, col, c.bg)
            elif c.ch != " ":
                k = min(1.0, lv) * 0.85 * (1.0 - (d - 1.0) / (halo - 1.0)) ** 2
                out[i] = Cell(c.ch, lerp_color(c.fg or _DEFAULT_FG, red, k), c.bg)
        return Grid(grid.width, grid.height, out)


# Jaw drop in rows over time: open wide, then chatter twice, then shut.
# (seconds from start, rows) steps; the jaw holds each value until the next step.
JAW_STEPS: tuple[tuple[float, int], ...] = (
    (0.0, 1), (0.06, 2), (0.36, 1), (0.44, 0), (0.54, 1), (0.64, 0), (0.74, 1), (0.84, 0),
)


@dataclass(frozen=True)
class JawChatter:
    """The lower jaw (cells of rows >= ``jaw.y`` in columns [x0, x1)) drops and chatters.

    Follows ``steps`` ((time from start, rows down) pairs, default
    ``JAW_STEPS``: a 2-row drop and two quick chatters); the rows it opens up
    are blank, and cells pushed past the bottom are dropped. Identity before
    ``start`` and from ``start + duration`` (the jaw is shut again).
    """

    start: float = 0.0
    duration: float = 0.9
    jaw: PlacedJaw | None = None
    steps: tuple[tuple[float, int], ...] = JAW_STEPS
    seed: int = 0

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def drop(self, t: float) -> int:
        """Rows the jaw hangs down at t."""
        if self.jaw is None or t < self.start or t >= self.end:
            return 0
        tau, rows = t - self.start, 0
        for at, n in self.steps:
            if tau < at:
                break
            rows = n
        return max(0, rows)

    def apply(self, grid: Grid, t: float) -> Grid:
        d = self.drop(finite_t(t))
        jaw = self.jaw
        if d == 0 or jaw is None or not grid.cells:
            return grid.copy()
        w, h = grid.width, grid.height
        x0, x1 = max(0, jaw.x0), min(w, jaw.x1)
        if x0 >= x1 or not 0 <= jaw.y < h:
            return grid.copy()
        out = list(grid.cells)
        cells = grid.cells
        for y in range(h - 1, jaw.y - 1, -1):  # bottom up: read the unmodified input
            src = y - d
            base = y * w
            if src >= jaw.y:
                sb = src * w
                out[base + x0 : base + x1] = cells[sb + x0 : sb + x1]
            else:
                out[base + x0 : base + x1] = [BLANK] * (x1 - x0)
        return Grid(w, h, out)
