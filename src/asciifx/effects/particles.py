"""Particles: the art assembles itself from flying sparks; plus a glint sweeping over it."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..core.easing import in_out_cubic, in_out_sine, out_cubic, out_quad
from ..core.grid import BLANK, Cell, Color, Grid
from .base import hash32, lerp_color

ASSEMBLE_HOT: Color = (255, 60, 40)
ASSEMBLE_WHITE_HOT: Color = (255, 226, 190)
_DEFAULT_FG: Color = (204, 204, 204)  # stands in for "terminal default" when fading colors
_BLACK: Color = (0, 0, 0)

_HOT_STEPS = 16  # in-flight colors, hot (slow) .. white-hot (fast)
_COOL_STEPS = 16  # colors while cooling after landing
_TRAIL_DIM = 0.6  # trail = the head's color this much of the way to black
_EDGE_MARGIN = 2.0  # "edges": starts this many cells outside the screen

TRAIL_GLYPHS = "⠒⡇⠑⠊"
"""Faint braille streaks for trails: horizontal, vertical, "\\" and "/" motion."""

_DELAY_SALT = 0xA55E
_START_SALT = 0xA55F
_GLYPH_SALT = 0xA560

ORIGINS = ("scatter", "edges", "center", "vortex", "explosion")
BURST_SHARE = 0.38
"""``origin="explosion"``: the fraction of a flight spent bursting outward (the rest is the pull back)."""
EASES = ("out_cubic", "out_back")


class Particle(NamedTuple):
    """One non-blank cell of the grid in flight (times are absolute seconds)."""

    x: int  # home cell
    y: int
    sx: float  # start position, in cells (may be off-screen)
    sy: float
    launch: float  # leaves (sx, sy)
    land: float  # arrives home; cools until land + cool_time
    mx: float = 0.0  # vortex: the centre it orbits; explosion: the far point it turns back at
    my: float = 0.0
    spin: float = 0.0  # vortex: radians it turns around (mx, my) on the way in


class _State(NamedTuple):
    """Everything about one grid that doesn't depend on t, sorted by landing time."""

    particles: tuple[Particle, ...]
    launches: list[float]
    lands: list[float]
    homes: list[int]  # home cell index per particle
    cells: list[Cell]  # the input cell per particle
    cool: list[tuple[Cell, ...]]  # cooling colors per particle
    flight: list[tuple[Any, ...]]  # per particle, for the hot loop (layout depends on the origin)


class _GridMemo:
    """A few per-grid results, reused while the grid's cells are unchanged.

    Keyed by size; a hit needs the cells to compare equal to a snapshot, which
    is fast for the usual case (the same cell objects every frame).
    """

    def __init__(self, size: int = 4) -> None:
        self.size = size
        self.entries: list[tuple[int, int, list[Cell], Any]] = []

    def get(self, grid: Grid, build: Callable[[Grid], Any]) -> Any:
        for k, (w, h, snap, value) in enumerate(self.entries):
            if w == grid.width and h == grid.height and snap == grid.cells:
                if k:
                    self.entries.insert(0, self.entries.pop(k))
                return value
        value = build(grid)
        self.entries.insert(0, (grid.width, grid.height, list(grid.cells), value))
        del self.entries[self.size :]
        return value


def _out_back(u: float, c1: float = 1.70158) -> float:
    m = u - 1.0
    return 1.0 + (c1 + 1.0) * m * m * m + c1 * m * m


def _visible(c: Cell) -> bool:
    return c.ch != " " or c.bg is not None


def _round_half_away(v: float) -> int:
    return int(math.copysign(math.floor(abs(v) + 0.5), v))


def _orientation(dx: float, dy: float) -> int:
    """0 horizontal, 1 vertical, 2 "\\", 3 "/" for motion (dx, dy) in cells,
    judged by the visual angle (cells are twice as tall as wide)."""
    a = math.degrees(math.atan2(2.0 * dy, dx)) % 180.0
    if a < 22.5 or a >= 157.5:
        return 0
    if 67.5 <= a < 112.5:
        return 1
    return 2 if a < 90.0 else 3


@dataclass(frozen=True)
class Assemble:
    """Every non-blank cell flies in from somewhere and lands on its home cell.

    Start positions (deterministic noise per cell):
      * ``"scatter"``: anywhere on the screen or up to ``overscan`` x its size
        beyond each edge,
      * ``"edges"``: just outside the border, roughly in line with the centre
        and the home cell (so they stream inwards),
      * ``"center"``: a small burst at the centre, flying outwards,
      * ``"vortex"``: far outside the screen, spiralling in around the centre
        along curved orbits (``turns`` x a full turn, +-30 %),
      * ``"explosion"``: a small ball at the centre bursts outward, every
        particle overshooting its home by ``burst`` x the half-diagonal of the
        screen (+-40 %), then is sucked back into place: the first
        ``BURST_SHARE`` of the flight goes out (ease out), the rest back in
        (ease in-out). Use a small ``spread`` so they burst together.
    Distances and directions are measured with cells ``CELL_ASPECT`` (2) times
    as tall as wide, so motion looks right. ``ease`` applies to the
    straight-line origins; vortex flights ease out (cubic).

    Timing: landings must be done ``cool_time`` before ``start + duration``.
    Launches are spread over the first ``spread`` fraction of that window,
    each particle at a noise-based delay; every flight lasts the rest of it.
    The position is ``lerp(start, home, ease(progress))`` with ``ease``
    ``"out_cubic"`` or ``"out_back"`` (overshoots a little, then settles).

    In flight a particle shows its own glyph (or one from ``glyphs``) in a
    color from ``white_hot`` (fast, just launched) to ``hot`` (slow, landing);
    fast ones (``trail_speed`` cells/s or more) leave a dim one-cell trail: a
    streak from ``trail_glyphs`` (horizontal, vertical, "\\", "/") matching the
    direction of travel, or the particle's own glyph if None.
    After landing a cell cools from ``hot`` to its true color over
    ``cool_time``. Draw order is landing order: a later particle covers an
    earlier one on the same cell; trails never cover particles.

    Blank before ``start``; exactly the grid from ``start + duration`` on.
    """

    start: float = 0.0
    duration: float = 2.0
    origin: str = "scatter"
    spread: float = 0.6
    cool_time: float = 0.4
    hot: Color = ASSEMBLE_HOT
    white_hot: Color = ASSEMBLE_WHITE_HOT
    glyphs: str | None = None
    ease: str = "out_cubic"
    overscan: float = 0.25
    trail_speed: float = 30.0
    trail_glyphs: str | None = TRAIL_GLYPHS
    turns: float = 1.0
    burst: float = 0.45
    seed: int = 0
    _memo: _GridMemo = field(
        init=False, repr=False, compare=False, hash=False, default_factory=_GridMemo
    )

    def __post_init__(self) -> None:
        if self.trail_glyphs is not None and len(self.trail_glyphs) != 4:
            raise ValueError("trail_glyphs must be None or 4 glyphs: horizontal, vertical, \\, /")
        if self.origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}, got {self.origin!r}")
        if self.ease not in EASES:
            raise ValueError(f"ease must be one of {EASES}, got {self.ease!r}")
        if self.glyphs is not None and not self.glyphs:
            raise ValueError("glyphs must be None or a non-empty string")
        object.__setattr__(self, "spread", min(1.0, max(0.0, self.spread)))
        object.__setattr__(self, "hot", tuple(self.hot))
        object.__setattr__(self, "white_hot", tuple(self.white_hot))

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    # -- schedule ------------------------------------------------------------

    @property
    def cooling(self) -> float:
        """Seconds each cell takes to cool (``cool_time``, capped by the duration)."""
        return min(max(0.0, self.cool_time), max(0.0, self.duration))

    @property
    def flight_time(self) -> float:
        """Seconds every particle spends in the air."""
        return (max(0.0, self.duration) - self.cooling) * (1.0 - self.spread)

    def delay(self, x: int, y: int) -> float:
        """Seconds after ``start`` that the particle of cell (x, y) launches."""
        window = max(0.0, self.duration) - self.cooling
        return window * self.spread * (hash32(self.seed, _DELAY_SALT, x, y) / 4294967296.0)

    def start_position(self, x: int, y: int, width: int, height: int) -> tuple[float, float]:
        """Where the particle of cell (x, y) starts, in (fractional) cells."""
        return self._path(x, y, width, height)[:2]

    def _path(self, x: int, y: int, width: int, height: int) -> tuple[float, float, float, float, float]:
        """(sx, sy, mx, my, spin) of the particle of cell (x, y): see ``Particle``."""
        h1 = hash32(self.seed, _START_SALT, x, y)
        u1, u2 = (h1 & 0xFFFF) / 65536.0, (h1 >> 16) / 65536.0
        cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
        if self.origin in ("vortex", "explosion"):
            reach = math.hypot(width / 2.0, float(height))  # half-diagonal, in visual units
            if self.origin == "vortex":
                spin = 2.0 * math.pi * self.turns * (0.7 + 0.6 * u1)
                r0 = reach * (1.05 + 0.35 * u2)
                a0 = math.atan2(2.0 * (y - cy), x - cx) + spin
                return cx + r0 * math.cos(a0), cy + r0 * math.sin(a0) / 2.0, cx, cy, spin
            h2 = hash32(self.seed, _START_SALT + 7, x, y)
            v1, v2 = (h2 & 0xFFFF) / 65536.0, (h2 >> 16) / 65536.0
            r, a = 1.5 * math.sqrt(u1), 2 * math.pi * u2  # the ball it bursts from
            sx, sy = cx + r * math.cos(a), cy + r * math.sin(a) / 2.0
            ox, oy = x - cx, 2.0 * (y - cy)
            heading = math.atan2(oy, ox) if (ox or oy) else 2 * math.pi * v1
            heading += (v1 - 0.5) * 0.5
            far = self.burst * reach * (0.6 + 0.8 * v2)
            return sx, sy, x + far * math.cos(heading), y + far * math.sin(heading) / 2.0, 0.0
        return (*self._straight_start(x, y, width, height, u1, u2), 0.0, 0.0, 0.0)

    def _straight_start(self, x: int, y: int, width: int, height: int, u1: float, u2: float) -> tuple[float, float]:
        cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
        if self.origin == "scatter":
            o = self.overscan
            return (-o + (1 + 2 * o) * u1) * width - 0.5, (-o + (1 + 2 * o) * u2) * height - 0.5
        if self.origin == "center":  # a burst ~3 cells wide (visually round)
            r, a = 1.5 * math.sqrt(u1), 2 * math.pi * u2
            return cx + r * math.cos(a), cy + r * math.sin(a) / 2.0
        # edges: along the ray centre -> home (with jitter), just outside the border
        a = math.atan2(2.0 * (y - cy), x - cx) + (u1 - 0.5) * 0.9
        dx, dy = math.cos(a), math.sin(a) / 2.0  # one visual unit, in cells
        m = _EDGE_MARGIN + 3.0 * u2
        reach = []
        if dx:
            reach.append(((width - 1 + m - cx) if dx > 0 else (cx + m)) / abs(dx))
        if dy:
            reach.append(((height - 1 + m - cy) if dy > 0 else (cy + m)) / abs(dy))
        k = min(reach)
        return cx + dx * k, cy + dy * k

    def particles(self, grid: Grid) -> tuple[Particle, ...]:
        """All particles, in landing (= drawing) order; ties by home position."""
        return self._state(grid).particles

    def position(self, p: Particle, t: float) -> tuple[float, float]:
        """Where ``p`` is at t (its start before launch, home once landed)."""
        f = self.flight_time
        if t >= p.land or f <= 0:
            return (float(p.x), float(p.y)) if t >= p.launch else (p.sx, p.sy)
        u = max(0.0, (t - p.launch) / f)
        if self.origin == "vortex":
            e = out_cubic(u)
            r0, a0 = math.hypot(p.sx - p.mx, 2.0 * (p.sy - p.my)), math.atan2(2.0 * (p.sy - p.my), p.sx - p.mx)
            rh = math.hypot(p.x - p.mx, 2.0 * (p.y - p.my))
            r, a = r0 + (rh - r0) * e, a0 - p.spin * e
            return p.mx + r * math.cos(a), p.my + r * math.sin(a) / 2.0
        if self.origin == "explosion":
            if u < BURST_SHARE:
                e = out_cubic(u / BURST_SHARE)
                return p.sx + (p.mx - p.sx) * e, p.sy + (p.my - p.sy) * e
            e = in_out_cubic((u - BURST_SHARE) / (1.0 - BURST_SHARE))
            return p.mx + (p.x - p.mx) * e, p.my + (p.y - p.my) * e
        e = out_cubic(u) if self.ease == "out_cubic" else _out_back(u)
        return p.sx + (p.x - p.sx) * e, p.sy + (p.y - p.sy) * e

    def _state(self, grid: Grid) -> _State:
        return self._memo.get(grid, self._build)

    def _build(self, grid: Grid) -> _State:
        w, h = grid.width, grid.height
        f = self.flight_time
        start = self.start
        hot, white = self.hot, self.white_hot
        hot_colors = [lerp_color(hot, white, k / (_HOT_STEPS - 1)) for k in range(_HOT_STEPS)]
        trail_colors = [lerp_color(c, _BLACK, _TRAIL_DIM) for c in hot_colors]
        hot_ramps: dict[str, tuple[Cell, ...]] = {}
        trail_ramps: dict[str, tuple[Cell, ...]] = {}
        cool_ramps: dict[Cell, tuple[Cell, ...]] = {}
        glyphs = self.glyphs

        rows = []
        for i, c in enumerate(grid.cells):
            if not _visible(c):
                continue
            x, y = i % w, i // w
            launch = start + self.delay(x, y)
            sx, sy, mx, my, spin = self._path(x, y, w, h)
            rows.append((launch + f, y, x, i, c, Particle(x, y, sx, sy, launch, launch + f, mx, my, spin)))
        rows.sort(key=lambda r: r[:3])

        flight = []
        for _, _, _, i, c, p in rows:
            g = glyphs[hash32(self.seed, _GLYPH_SALT, p.x, p.y) % len(glyphs)] if glyphs else c.ch
            if g == " ":
                g = "█"  # a background-only cell flies as a solid block
            ramp = hot_ramps.get(g)
            if ramp is None:
                ramp = hot_ramps[g] = tuple(Cell(g, col) for col in hot_colors)
            def trail(dx: float, dy: float) -> tuple[tuple[Cell, ...], int, int]:
                """Trail ramp and cell offset for motion (dx, dy): one step back along it."""
                big = max(abs(dx), abs(dy)) or 1.0
                tg = g if self.trail_glyphs is None else self.trail_glyphs[_orientation(dx, dy)]
                tramp = trail_ramps.get(tg)
                if tramp is None:
                    tramp = trail_ramps[tg] = tuple(Cell(tg, col) for col in trail_colors)
                return tramp, -_round_half_away(dx / big), -_round_half_away(dy / big)

            if self.origin == "vortex":
                r0 = math.hypot(p.sx - p.mx, 2.0 * (p.sy - p.my))
                a0 = math.atan2(2.0 * (p.sy - p.my), p.sx - p.mx)
                rh = math.hypot(p.x - p.mx, 2.0 * (p.y - p.my))
                tramps = tuple(trail(*d) for d in ((1, 0), (0, 1), (1, 1), (-1, 1)))  # by _orientation
                flight.append((p.launch, p.mx, p.my, r0, rh - r0, a0, p.spin, ramp, tramps))
            elif self.origin == "explosion":
                ox, oy = p.mx - p.sx, p.my - p.sy
                ix, iy = p.x - p.mx, p.y - p.my
                d_out, d_in = math.hypot(ox, 2.0 * oy), math.hypot(ix, 2.0 * iy)
                flight.append((p.launch, p.sx, p.sy, ox, oy, p.mx, p.my, ix, iy, ramp,
                               trail(ox, oy), trail(ix, iy), d_out, d_in))
            else:
                dx, dy = p.x - p.sx, p.y - p.sy
                dist = math.hypot(dx, 2.0 * dy)  # in visual (cell-width) units
                tramp, tx, ty = trail(dx, dy)
                # out_cubic speed = dist * 3 (1 - u)^2 / f: trail while (1 - u)^2 >= trail_v2.
                trail_v2 = self.trail_speed * f / (3.0 * dist) if dist > 0 and f > 0 else math.inf
                flight.append((p.launch, p.sx, dx, p.sy, dy, ramp, tramp, tx, ty, trail_v2))
            if c not in cool_ramps:
                cool_ramps[c] = self._cool_ramp(c)

        parts = tuple(r[5] for r in rows)
        return _State(
            particles=parts,
            launches=[p.launch for p in parts],
            lands=[p.land for p in parts],
            homes=[r[3] for r in rows],
            cells=[r[4] for r in rows],
            cool=[cool_ramps[r[4]] for r in rows],
            flight=flight,
        )

    def _cool_ramp(self, c: Cell) -> tuple[Cell, ...]:
        """``c`` cooling from hot (index 0) toward its true color."""
        hot = self.hot
        out = []
        for k in range(_COOL_STEPS):
            u = out_quad(k / _COOL_STEPS)
            if c.ch == " ":  # background-only cell: the bg cools
                out.append(Cell(" ", c.fg, lerp_color(hot, c.bg or _DEFAULT_FG, u)))
            else:
                out.append(Cell(c.ch, lerp_color(hot, c.fg or _DEFAULT_FG, u), c.bg))
        return tuple(out)

    # -- rendering -----------------------------------------------------------

    def apply(self, grid: Grid, t: float) -> Grid:
        w, h = grid.width, grid.height
        if t >= self.end:
            return grid.copy()
        if t < self.start or not grid.cells:
            return Grid(w, h)
        st = self._state(grid)
        out = [BLANK] * (w * h)
        homes = st.homes
        cool = self.cooling

        # Landed and cooled: the true cell. Landed, cooling: hot fading to true.
        n_cooled = bisect_right(st.lands, t - cool) if cool > 0 else bisect_right(st.lands, t)
        n_landed = bisect_right(st.lands, t)
        cells = st.cells
        for j in range(n_cooled):
            out[homes[j]] = cells[j]
        if n_landed > n_cooled:
            lands, ramps = st.lands, st.cool
            scale = _COOL_STEPS / cool
            for j in range(n_cooled, n_landed):
                out[homes[j]] = ramps[j][min(_COOL_STEPS - 1, int((t - lands[j]) * scale))]

        # In flight, in landing order (later ones cover earlier ones).
        n_launched = bisect_right(st.launches, t)
        if n_launched <= n_landed:
            return Grid(w, h, out)
        inv_f = 1.0 / self.flight_time
        back = self.ease == "out_back"
        top = _HOT_STEPS - 1
        xmax, ymax = w - 0.5, h - 0.5
        if self.origin == "vortex":
            self._fly_vortex(st, out, w, t, n_landed, n_launched)
            return Grid(w, h, out)
        if self.origin == "explosion":
            self._fly_explosion(st, out, w, t, n_landed, n_launched)
            return Grid(w, h, out)
        for j in range(n_landed, n_launched):
            launch, sx, dx, sy, dy, ramp, tramp, tx, ty, trail_v2 = st.flight[j]
            v = 1.0 - (t - launch) * inv_f  # 1 at launch .. 0 at landing
            v2 = v * v
            e = 1.0 - v2 * v if not back else _out_back(1.0 - v)
            xf, yf = sx + dx * e, sy + dy * e
            if not (-0.5 <= xf < xmax and -0.5 <= yf < ymax):
                continue
            xi, yi = int(xf + 0.5), int(yf + 0.5)
            k = int(v2 * top + 0.5)  # faster = whiter
            if v2 >= trail_v2:
                txi, tyi = xi + tx, yi + ty
                if 0 <= txi < w and 0 <= tyi < h and out[tyi * w + txi] is BLANK:
                    out[tyi * w + txi] = tramp[k]
            out[yi * w + xi] = ramp[k]
        return Grid(w, h, out)

    def _fly_vortex(self, st: _State, out: list[Cell], w: int, t: float, lo: int, hi: int) -> None:
        """Particles lo..hi-1 on their spirals (see ``position``)."""
        h = len(out) // w if w else 0
        f = self.flight_time
        inv_f = 1.0 / f
        top = _HOT_STEPS - 1
        xmax, ymax = w - 0.5, h - 0.5
        lim2 = (self.trail_speed * f / 3.0) ** 2  # trail while (dr^2 + (r spin)^2) v^4 >= lim2
        cos, sin = math.cos, math.sin
        for j in range(lo, hi):
            launch, mx, my, r0, dr, a0, spin, ramp, tramps = st.flight[j]
            v = 1.0 - (t - launch) * inv_f
            v2 = v * v
            e = 1.0 - v2 * v
            r = r0 + dr * e
            a = a0 - spin * e
            ca, sa = cos(a), sin(a)
            xf, yf = mx + r * ca, my + 0.5 * r * sa
            if not (-0.5 <= xf < xmax and -0.5 <= yf < ymax):
                continue
            xi, yi = int(xf + 0.5), int(yf + 0.5)
            k = int(v2 * top + 0.5)
            rs = r * spin
            if (dr * dr + rs * rs) * v2 * v2 >= lim2:
                # velocity along e: d/de (r cos a, r sin a / 2) with da/de = -spin
                dx, dy = dr * ca + rs * sa, 0.5 * (dr * sa - rs * ca)
                tramp, tx, ty = tramps[_orientation(dx, dy)]
                big = max(abs(dx), abs(dy)) or 1.0
                txi, tyi = xi - _round_half_away(dx / big), yi - _round_half_away(dy / big)
                if 0 <= txi < w and 0 <= tyi < h and out[tyi * w + txi] is BLANK:
                    out[tyi * w + txi] = tramp[k]
            out[yi * w + xi] = ramp[k]

    def _fly_explosion(self, st: _State, out: list[Cell], w: int, t: float, lo: int, hi: int) -> None:
        """Particles lo..hi-1 bursting out, then pulled back (see ``position``)."""
        h = len(out) // w if w else 0
        f = self.flight_time
        inv_f = 1.0 / f
        top = _HOT_STEPS - 1
        xmax, ymax = w - 0.5, h - 0.5
        q = BURST_SHARE
        v_out, v_in = 3.0 / (q * f), 12.0 / ((1.0 - q) * f)  # peak speed per unit of distance
        lim = self.trail_speed
        for j in range(lo, hi):
            launch, sx, sy, ox, oy, mx, my, ix, iy, ramp, t_out, t_in, d_out, d_in = st.flight[j]
            u = (t - launch) * inv_f
            if u < q:
                w1 = 1.0 - u / q
                s = w1 * w1  # 1 at the burst .. 0 at the far point
                e = 1.0 - s * w1
                xf, yf = sx + ox * e, sy + oy * e
                fast, tr = d_out * v_out * s >= lim, t_out
            else:
                v = (u - q) / (1.0 - q)
                m = v if v < 0.5 else 1.0 - v
                s = 4.0 * m * m  # 0 at the far point .. 1 half-way back
                e = 4.0 * v * v * v if v < 0.5 else 1.0 - 4.0 * (1.0 - v) ** 3
                xf, yf = mx + ix * e, my + iy * e
                fast, tr = d_in * v_in * m * m >= lim, t_in
            if not (-0.5 <= xf < xmax and -0.5 <= yf < ymax):
                continue
            xi, yi = int(xf + 0.5), int(yf + 0.5)
            k = int(s * top + 0.5)
            if fast:
                tramp, tx, ty = tr
                txi, tyi = xi + tx, yi + ty
                if 0 <= txi < w and 0 <= tyi < h and out[tyi * w + txi] is BLANK:
                    out[tyi * w + txi] = tramp[k]
            out[yi * w + xi] = ramp[k]


@dataclass(frozen=True)
class Glint:
    """A soft diagonal band of light sweeping once across the art.

    The band runs "/" (aspect-correct 45 degrees) and travels from the
    top-left to the bottom-right of the non-blank area over
    [start, start + duration] (ease in-out). Glyph colors inside it are pulled
    toward ``color`` by up to ``strength``, falling off smoothly over
    ``width`` cell-widths either side of its centre. Glyphs, blank cells and
    backgrounds are untouched; identity outside the window. ``seed`` is
    accepted for a uniform interface; the glint is not random.
    """

    start: float = 0.0
    duration: float = 1.2
    color: Color = (255, 250, 245)
    width: float = 6.0
    strength: float = 0.8
    seed: int = 0
    _memo: _GridMemo = field(
        init=False, repr=False, compare=False, hash=False, default_factory=_GridMemo
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "color", tuple(self.color))

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    def centre(self, grid: Grid, t: float) -> float | None:
        """The band's centre at t, as a value of ``x + 2 * y`` (None: nothing to light)."""
        if t < self.start or t >= self.end:
            return None
        extent = self._memo.get(grid, _diagonal_extent)
        if extent is None:
            return None
        lo, hi = extent
        p = in_out_sine((t - self.start) / self.duration)
        return lo - self.width + (hi - lo + 2 * self.width) * p

    def apply(self, grid: Grid, t: float) -> Grid:
        w, h = grid.width, grid.height
        c = self.centre(grid, t) if self.width > 0 and self.strength > 0 else None
        if c is None:
            return grid.copy()
        out = list(grid.cells)
        # strength > 1 would extrapolate past ``color``: RGB outside 0..255.
        width, color, strength = self.width, self.color, min(1.0, self.strength)
        lit: dict[tuple[Color | None, int], Color] = {}
        for y in range(h):
            base = y * w
            for x in range(max(0, math.ceil(c - width - 2 * y)), min(w, math.floor(c + width - 2 * y) + 1)):
                cell = out[base + x]
                if cell.ch == " ":
                    continue
                d = (x + 2 * y - c) / width
                q = round(32 * strength * (1.0 - d * d) ** 2)  # quantized: few distinct colors
                if q <= 0:
                    continue
                fg = lit.get((cell.fg, q))
                if fg is None:
                    fg = lit[(cell.fg, q)] = lerp_color(cell.fg or _DEFAULT_FG, color, q / 32)
                out[base + x] = Cell(cell.ch, fg, cell.bg)
        return Grid(w, h, out)


def _diagonal_extent(grid: Grid) -> tuple[int, int] | None:
    """(min, max) of ``x + 2 * y`` over the grid's glyph cells, or None."""
    w = grid.width
    lo, hi = math.inf, -math.inf
    for i, c in enumerate(grid.cells):
        if c.ch != " ":
            s = i % w + 2 * (i // w)
            if s < lo:
                lo = s
            if s > hi:
                hi = s
    return None if hi < lo else (int(lo), int(hi))
