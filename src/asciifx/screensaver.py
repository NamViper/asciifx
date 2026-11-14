"""Idle screensaver: scenes in a shuffled rotation, each followed by matrix rain
with the time carved into it; repeats forever.

Scenes (``SCENES``; pick with ``scenes=``):

    skull     the logo decodes in, glitches, re-renders itself through the
              styles, dissolves (the classic cycle, below)
    warp      a starfield at speed; the logo flies in from far away, holds,
              then jumps to warp and shrinks into the distance
    inferno   fire; the logo rises out of the flames, burns, sinks back
    hologram  the logo projected from an emitter as a flickering hologram
    sysmon    a live system monitor (CPU, memory, load, uptime, tasks, net)

A *cycle* (``plan(cycle)``) is one pass through all the scenes in a seeded
order, reshuffled every cycle (never the same scene twice in a row across the
seam), with ``rain_seconds`` of rain after every scene. ``phase_at(t)`` says
which phase, scene and slot a time falls in.

The skull scene with the default 3 styles (seconds from its start)::

    0.0 -  3.0  intro     even cycles: the logo decodes out of nothing;
                          odd cycles: the rain keeps falling and paints it in
    3.0 -  6.5  hold      style 1, a couple of glitch bursts
    6.5 -  6.9  glitch    burst, then
    6.9 -  8.4  morph     re-render into style 2
    8.4 - 11.4  hold      style 2, glitch flickers
   11.4 - 11.8  glitch    burst, then
   11.8 - 13.3  morph     re-render into style 3
   13.3 - 16.3  hold      style 3, glitch flickers
   16.3 - 16.9  glitch    heavy
   16.9 - 18.3  dissolve  scrambles away to black

Cycle ``k`` starts the skull with ``styles[k % n]``; the other logo scenes use
the style of their slot. Seeds derive from the cycle and phase, so no two
cycles look the same. Rain drains to black before a scene, except before an
odd cycle's skull intro, which is the rain painting the logo in.

Overlays (all optional): during rain the current time (``HH:MM`` in the block
font) is carved into the rain: drops inside the digits are drawn bright, with
a faint outline. A dim ``idle 14m · user@host`` line sits bottom right.

``palette`` is "blood", "green", a Palette, or "auto": blood by day (07-19 h
local time) and green at night. It colors the rain, effects, panel and the
logo (green swaps its red and green channels).

``frame(t, width, height)`` is a function of (t, size) and of the wall clock,
read once per frame from the injectable ``clock`` (time of day, idle time,
palette). Logos are loaded once per screen size and size step and cached;
every cache is bounded. Without a logo it is endless rain (with the clock).
"""

from __future__ import annotations

import dataclasses
import getpass
import math
import os
import socket
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from .core.easing import in_quad, out_cubic
from .core.grid import BLANK, Cell, Color, Grid
from .core.layers import Layer, compose
from .effects import (
    BLOOD, GREEN, MORPH_CHARSET, PALETTES, Chain, Decode, Fire, Glitch, Hologram, Matrix, Morph,
    Palette, Reverse, Starfield, hash32,
)
from .effects.base import Effect, gradient, lerp_color
from .sysmon import Stats, SysMon, fmt_bytes, fmt_duration, fmt_rate

type Loader = Callable[[str, int, int], Grid | None]
"""(style, max_width, max_height) -> the colored logo grid, or None if there is no logo."""


class Monitor(Protocol):
    """Where the sysmon scene gets its numbers (``sysmon.SysMon`` or a fake)."""

    def stats(self) -> Stats: ...


SCENES = ("skull", "warp", "inferno", "hologram", "sysmon")
PALETTE_MODES = ("auto", "blood", "green")
DAY_HOURS = (7, 19)  # "auto" palette: blood from 07:00 until 19:00 local time, else green

# Skull phase lengths in seconds (see the module docstring).
INTRO = 3.0
HOLD = 3.5
BURST = 0.4
MORPH = 1.5
REHOLD = 3.0
OUTRO = 0.6
DISSOLVE = 1.4

# The other scenes: (phase name, seconds).
WARP_PHASES = (("approach", 5.5), ("cruise", 8.5), ("jump", 3.0))
INFERNO_PHASES = (("ignite", 1.5), ("rise", 4.0), ("burn", 9.5), ("sink", 3.0))
HOLOGRAM_PHASES = (("project", 2.0), ("transmit", 13.0), ("collapse", 2.0))
SYSMON_PHASES = (("boot", 1.5), ("monitor", 14.5), ("shutdown", 1.5))

BOX_MARGIN = (4, 2)  # the logo fits in (width - 4, height - 2)
MIN_BOX = (8, 4)  # smaller screens get rain only

RAIN_SEGMENT = 12.0  # long rain is drawn as overlapping Matrix runs of about this length
RAIN_SPEED = 22.0  # rows per second
RAIN_RAMP = 1.2  # every column's first drop enters within this many seconds

# Warp: the logo approaches from depth WARP_FAR (scale 1 / depth), after the
# stars have had WARP_EMPTY seconds alone; the jump speeds the stars up to
# 1 + WARP_BOOST times and sends the logo WARP_AWAY deep.
WARP_SPEED = 2.2
WARP_FAR = 14.0
WARP_EMPTY = 0.8
WARP_BOOST = 10.0
WARP_AWAY = 40.0
SCALE_STEPS = (0.1, 0.2, 0.35, 0.55, 0.8, 1.0)  # logo sizes asked of the loader (x the box)
INFERNO_SCALE = 0.8
HOLOGRAM_SCALE = 0.8

CLOCK_FORMAT = "%H:%M"
CLOCK_FADE = 1.5  # the outline fades in / out over this long at the rain's ends
CLOCK_BOX = (0.75, 0.35)  # the carved time is at most this share of the screen (w, h)
OUTLINE_CHAR = "·"

_CACHED_SIZES = 4
_CACHED_LOGOS = 40
_CYCLE_SALT = 0x5C2E
_RAIN_SALT = 0x5C2F
_ORDER_SALT = 0x5C30
_SCENE_SALT = 0x5C31
_WHITE: Color = (255, 255, 255)
_BLACK: Color = (0, 0, 0)


@dataclass(frozen=True)
class Phase:
    """One step of a cycle. ``start``/``end`` are absolute seconds."""

    name: str  # see the *_PHASES tables; skull: intro | hold | glitch | morph | dissolve; rain
    start: float
    end: float
    cycle: int
    style: str | None  # logo style on screen (morph: the style it turns into); None for rain/sysmon
    prev: str | None = None  # morph: the style it turns from
    # Applied to the scene's content (skull: the centred logo). None for morph and rain.
    effect: Effect | None = None
    seed: int = 0
    scene: str = "skull"  # one of SCENES, or "rain"
    slot: int = 0  # position of the scene in the cycle's order
    backdrop: Effect | None = None  # warp: the Starfield; inferno: the fires


# (phase name, length in seconds, style index, previous style index, effect variant)
type _Span = tuple[str, float, int | None, int | None, str]


def _rain_tail(height: int) -> int:
    """Trail length: longer on tall screens so the rain looks equally dense."""
    return max(12, round(0.3 * height))


def _rain_overlap(height: int) -> float:
    """How long a rain run keeps going after the next one starts: about the time
    the old run needs to thin out while the new one fills the screen, so the
    density stays roughly constant across the seam."""
    return 0.75 * (height + _rain_tail(height)) / RAIN_SPEED + RAIN_RAMP / 2


def rain_layers(
    t: float,
    height: int,
    start: float,
    end: float,
    seed: int,
    extend: float = 0.0,
    palette: Palette = BLOOD,
) -> list[Matrix]:
    """The Matrix runs (``reveal=False``) of a steady rain over [start, end] that
    are active at ``t``, oldest first; apply them in order onto the background.

    The rain fills the screen within about ``RAIN_RAMP`` plus the fall time and
    thins out before ``end + extend``. ``end`` may be ``math.inf``. Consecutive
    runs overlap so the seams don't show.
    """
    length = end - start
    if not length > 0:
        return []
    n = None if math.isinf(length) else max(1, round(length / RAIN_SEGMENT))
    seg = RAIN_SEGMENT if n is None else length / n
    j = math.floor((t - start) / seg)
    if n is not None:
        j = min(j, n - 1)
    tail = _rain_tail(height)
    overlap = _rain_overlap(height)
    out = []
    for i in (j - 1, j):
        if i < 0:
            continue
        s = start + i * seg
        e = s + seg + overlap
        if n is not None:
            e = end + extend if i == n - 1 else min(e, end + extend)
        if t >= e:
            continue
        out.append(Matrix(
            start=s, duration=e - s, reveal=False, speed=RAIN_SPEED,
            spread=min(0.95, RAIN_RAMP / (e - s)), drops=int((e - s) * RAIN_SPEED) + 2,
            tail_len=tail, head=palette.head, tail=palette.tail, seed=hash32(seed, _RAIN_SALT, i),
        ))  # fmt: skip
    return out


def _centred(logo: Grid, width: int, height: int) -> Grid:
    return compose(width, height, [Layer(logo, (width - logo.width) // 2, (height - logo.height) // 2)])


def _swap_rg(grid: Grid) -> Grid:
    """Red and green channels swapped: the blood-red logo turns matrix green."""

    def sw(c: Color | None) -> Color | None:
        return None if c is None else (c[1], c[0], c[2])

    return Grid(grid.width, grid.height, [
        c if c.fg is None and c.bg is None else Cell(c.ch, sw(c.fg), sw(c.bg)) for c in grid.cells
    ])  # fmt: skip


def _resample(grid: Grid, width: int, height: int) -> Grid:
    """Nearest-neighbour resize (used between the loader's size steps)."""
    if (width, height) == (grid.width, grid.height):
        return grid
    gw, cells = grid.width, grid.cells
    xs = [min(gw - 1, int((x + 0.5) * gw / width)) for x in range(width)]
    out = []
    for y in range(height):
        base = min(grid.height - 1, int((y + 0.5) * grid.height / height)) * gw
        out.extend(cells[base + x] for x in xs)
    return Grid(width, height, out)


def user_at_host() -> str:
    """``user@host`` of this session (short host name)."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if not user:
        try:
            user = getpass.getuser()
        except (OSError, KeyError, ImportError):
            user = "user"
    try:
        host = socket.gethostname().split(".")[0] or "localhost"
    except OSError:
        host = "localhost"
    return f"{user}@{host}"


# -- the carved clock ---------------------------------------------------------------


@lru_cache(maxsize=8)
def clock_mask(text: str, width: int, height: int) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """Cells (row-major indices into a width x height grid) covered by ``text``
    in the block font, scaled up and centred: (inside, outline). The outline is
    the inside cells next to an outside cell. None if it doesn't fit.

    Each block-font cell is two pixels tall (half blocks), so scale ``k`` makes
    a pixel ``k`` columns wide and ``k / 2`` rows tall: square on ~2:1 cells.
    """
    from .fonts import render_text

    g = render_text(text, "block")
    pw, ph = g.width, 2 * g.height
    if pw == 0:
        return None
    px = [[False] * pw for _ in range(ph)]
    for y, row in enumerate(g.rows()):
        for x, c in enumerate(row):
            px[2 * y][x] = c.ch in "█▀"
            px[2 * y + 1][x] = c.ch in "█▄"
    k = min(int(CLOCK_BOX[0] * width / pw), int(2 * CLOCK_BOX[1] * height / ph))
    if k >= 2:
        mw, mh = pw * k, (ph * k + 1) // 2

        def on(cx: int, cy: int) -> bool:
            return px[min(ph - 1, int((cy + 0.5) * ph / mh))][cx // k]
    elif g.width <= width - 2 and g.height <= height - 2:  # small screens: the font as is
        mw, mh = g.width, g.height

        def on(cx: int, cy: int) -> bool:
            return g.cells[cy * g.width + cx].ch != " "
    else:
        return None
    x0, y0 = (width - mw) // 2, (height - mh) // 2
    mask = [[on(x, y) for x in range(mw)] for y in range(mh)]
    inside, edge = [], []
    for y in range(mh):
        for x in range(mw):
            if not mask[y][x]:
                continue
            i = (y0 + y) * width + x0 + x
            inside.append(i)
            if any(not (0 <= x + dx < mw and 0 <= y + dy < mh and mask[y + dy][x + dx])
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):  # fmt: skip
                edge.append(i)
    return tuple(inside), tuple(edge)


# -- the sysmon panel ----------------------------------------------------------------

_SPARK = " ▁▂▃▄▅▆▇█"


def _bar_cells(frac: float | None, width: int, palette: Palette) -> list[Cell]:
    """One row of a horizontal bar: filled █ along a dark-to-bright ramp, rest ░."""
    if width <= 0:
        return []
    ramp = gradient((palette.tail[2], palette.tail[0], palette.head), width)
    filled = 0 if frac is None else round(max(0.0, min(1.0, frac)) * width)
    empty = Cell("░", palette.tail[-1])
    return [Cell("█", ramp[x]) if x < filled else empty for x in range(width)]


def _spark(values: tuple[float, ...], width: int) -> str:
    """Sparkline of the last ``width`` percentages."""
    vals = values[-width:] if width > 0 else ()
    return "".join(_SPARK[min(8, max(0, round(v / 100 * 8)))] for v in vals)


def _pct(v: float | None) -> str:
    return "--" if v is None else f"{v:.0f}%"


def sysmon_panel(stats: Stats, width: int, height: int, palette: Palette, title: str = "") -> Grid:
    """The monitor scene: big CPU / MEM numbers and bars, load, tasks, network,
    busiest processes; centred on a width x height grid. Pure (no I/O)."""
    from .fonts import render_text, text_width

    out = Grid(width, height)
    pw = min(width - 2, 140)
    if pw < 30 or height < 8:
        out.put_text(max(0, (width - 7) // 2), height // 2, f"CPU {_pct(stats.cpu)}", palette.head)
        return out
    border, label, dim = palette.tail[1], palette.highlight, palette.tail[2]
    value = lerp_color(palette.head, _WHITE, 0.3)
    iw = pw - 4  # inside the frame, one column of padding each side
    big = height >= 26
    rows: list[list[Cell] | None] = []  # None: blank row

    def text_row(parts: list[tuple[str, Color]]) -> list[Cell]:
        cells = [Cell(ch, color) for s, color in parts for ch in s][:iw]
        return cells + [BLANK] * (iw - len(cells))

    def big_row(name: str, pct: float | None, side: list[list[tuple[str, Color]]]) -> None:
        number = render_text(_pct(pct), "block", gradient=(palette.head, palette.tail[0]))
        nw = text_width("100%", "block")
        left = 6 + nw + 3
        for y in range(number.height):
            row = text_row([(name if y == 0 else "", label)])
            row = row[:6] + [BLANK] * (nw - number.width) + number.cells[y * number.width : (y + 1) * number.width]
            row = row[:left] + [BLANK] * (left - len(row))
            if y < len(side):
                row += text_row(side[y])[: iw - left]
            rows.append((row + [BLANK] * iw)[:iw])

    load = stats.load
    load_txt = "n/a" if load is None else f"{load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}"
    load_frac = None if load is None else load[0] / max(1, stats.ncpu)
    mem = f"{fmt_bytes(stats.mem_used)} / {fmt_bytes(stats.mem_total)}"
    swap = f"{fmt_bytes(stats.swap_used)} / {fmt_bytes(stats.swap_total)}"
    cores = _spark(stats.cores, iw - 14) if stats.cores else "n/a"
    history = _spark(stats.history, iw - 14) if stats.history else "n/a"
    top = "  ".join(f"{name[:15]} {pct:.1f}%" for name, pct in stats.top) or "n/a"
    tasks = "n/a" if stats.tasks is None else str(stats.tasks)
    running = "n/a" if stats.running is None else str(stats.running)

    if big:
        big_row("CPU", stats.cpu, [
            [("cores ", dim), (cores, value)], [("history ", dim), (history, value)],
            [], [("load  ", dim), (load_txt, value)],
        ])  # fmt: skip
        rows += [_bar_cells(None if stats.cpu is None else stats.cpu / 100, iw, palette)] * 2
        rows.append(None)
        big_row("MEM", stats.mem_percent, [
            [("used  ", dim), (mem, value)], [("swap  ", dim), (swap, value)],
            [], [("uptime  ", dim), (fmt_duration(stats.uptime), value)],
        ])  # fmt: skip
        rows += [_bar_cells(None if stats.mem_percent is None else stats.mem_percent / 100, iw, palette)] * 2
        rows.append(None)
        rows.append(text_row([("LOAD   ", label), (load_txt, value), (f"   per core of {stats.ncpu}", dim)]))
        rows.append(_bar_cells(None if load_frac is None else min(1.0, load_frac), iw, palette))
    else:
        bar_w = max(0, iw - 22)
        rows.append(text_row([("CPU   ", label), (f"{_pct(stats.cpu):>5}  ", value)])[:14]
                    + _bar_cells(None if stats.cpu is None else stats.cpu / 100, bar_w, palette)
                    + [BLANK] * (iw - 14 - bar_w))
        rows.append(text_row([("cores ", dim), (cores, value)]))
        rows.append(text_row([("hist  ", dim), (history, value)]))
        rows.append(text_row([("MEM   ", label), (f"{_pct(stats.mem_percent):>5}  ", value)])[:14]
                    + _bar_cells(None if stats.mem_percent is None else stats.mem_percent / 100, bar_w, palette)
                    + [BLANK] * (iw - 14 - bar_w))
        rows.append(text_row([("      ", dim), (mem, value), ("   swap ", dim), (swap, value)]))
        rows.append(text_row([("LOAD  ", label), (load_txt, value)]))
    rows.append(None)
    rows.append(text_row([
        ("TASKS  ", label), (tasks, value), ("   running ", dim), (running, value),
        ("   UP ", label), (fmt_duration(stats.uptime), value),
        ("   NET ", label), ("↓ ", dim), (fmt_rate(stats.net_rx), value),
        ("  ↑ ", dim), (fmt_rate(stats.net_tx), value),
    ]))  # fmt: skip
    rows.append(text_row([("TOP    ", label), (top, value)]))

    rows = rows[: height - 4]
    ph = len(rows) + 4
    x0, y0 = (width - pw) // 2, max(0, (height - ph) // 2)
    head = f"[ SYSMON ]═{f'[ {title} ]' if title else ''}"
    out.put_text(x0, y0, ("╔═" + head + "═" * pw)[: pw - 1] + "╗", border)
    for c, x in zip(head, range(x0 + 2, x0 + pw - 1)):
        if c not in "═[]":
            out.set(x, y0, Cell(c, label))
    for y in range(y0 + 1, y0 + ph - 1):
        out.set(x0, y, Cell("║", border))
        out.set(x0 + pw - 1, y, Cell("║", border))
    out.put_text(x0, y0 + ph - 1, "╚" + "═" * (pw - 2) + "╝", border)
    for j, row in enumerate(rows):
        if row is not None:
            y = y0 + 2 + j
            out.cells[y * width + x0 + 2 : y * width + x0 + 2 + iw] = row
    return out


# -- the screensaver -------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Screensaver:
    """Full-screen frame Source rotating through ``scenes`` (see module doc).

    ``loader(style, max_w, max_h)`` returns the colored logo for a style, or
    None; it is called once per style, screen size and size step, with boxes
    up to ``(width - 4, height - 2)``. If it returns None for every style (or
    is None, or the screen is tiny) the screensaver is endless rain; styles
    without a logo borrow another style's. ``rain_seconds`` is the rain after
    every scene (0: none), ``seed`` varies everything.

    ``palette``: "auto" | "blood" | "green" | a Palette. ``clock`` gives the
    epoch seconds (read once per frame): time of day for the carved clock and
    the auto palette, and the idle time, counted from ``idle_since`` (epoch of
    the last keypress; None: from when the screensaver started).
    ``show_clock`` / ``clock_outline`` / ``show_idle`` switch the overlays;
    ``user_host`` overrides the detected ``user@host``. ``monitor`` feeds the
    sysmon scene (default: a ``SysMon`` reading /proc).
    """

    loader: Loader | None
    styles: tuple[str, ...] = ("dots", "sketch", "outline")
    rain_seconds: float = 30.0
    palette: str | Palette = "auto"
    seed: int = 0
    scenes: tuple[str, ...] = SCENES
    clock: Callable[[], float] = time.time
    show_clock: bool = True
    clock_outline: bool = True
    idle_since: float | None = None
    show_idle: bool = True
    user_host: str | None = None
    monitor: Monitor | None = None
    _spans: dict[str, tuple[_Span, ...]] = field(init=False, repr=False)
    _scenes: OrderedDict[tuple[int, int, bool], dict[str, Grid] | None] = field(
        init=False, repr=False, default_factory=OrderedDict
    )
    _loaded: OrderedDict[tuple[str, int, int, bool], Grid | None] = field(
        init=False, repr=False, default_factory=OrderedDict
    )
    _plans: dict[tuple[int, Palette], tuple[Phase, ...]] = field(init=False, repr=False, default_factory=dict)
    _morphs: dict[tuple[float, int, int], Morph] = field(
        init=False, repr=False, default_factory=dict
    )
    _state: dict[str, Any] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        styles = tuple(self.styles)
        if not styles:
            raise ValueError("styles must not be empty")
        if not self.rain_seconds >= 0:
            raise ValueError(f"rain_seconds must be >= 0, got {self.rain_seconds}")
        scenes = tuple(dict.fromkeys(self.scenes))
        if not scenes:
            raise ValueError("scenes must not be empty")
        unknown = [s for s in scenes if s not in SCENES]
        if unknown:
            raise ValueError(f"unknown scene(s) {', '.join(unknown)}; choose from {', '.join(SCENES)}")
        if not isinstance(self.palette, Palette) and self.palette not in PALETTE_MODES:
            choices = ", ".join(PALETTE_MODES)
            raise ValueError(f"palette must be a Palette or one of {choices}, got {self.palette!r}")
        object.__setattr__(self, "styles", styles)
        object.__setattr__(self, "scenes", scenes)
        last = len(styles) - 1
        skull: list[_Span] = [("intro", INTRO, 0, None, "intro"), ("hold", HOLD, 0, None, "hold")]
        for i in range(1, len(styles)):
            skull += [
                ("glitch", BURST, i - 1, None, "burst"),
                ("morph", MORPH, i, i - 1, "morph"),
                ("hold", REHOLD, i, None, "rehold"),
            ]
        skull += [("glitch", OUTRO, last, None, "outro"), ("dissolve", DISSOLVE, last, None, "dissolve")]
        spans: dict[str, tuple[_Span, ...]] = {"skull": tuple(skull)}
        for name, table in (("warp", WARP_PHASES), ("inferno", INFERNO_PHASES),
                            ("hologram", HOLOGRAM_PHASES), ("sysmon", SYSMON_PHASES)):  # fmt: skip
            si = None if name == "sysmon" else 0
            spans[name] = tuple((p, length, si, None, p) for p, length in table)
        object.__setattr__(self, "_spans", spans)

    # -- timeline -------------------------------------------------------------

    def scene_length(self, scene: str) -> float:
        """Seconds of one scene, without the rain after it."""
        return sum(span[1] for span in self._spans[scene])

    @property
    def cycle_length(self) -> float:
        """Seconds of one cycle: every scene once, each followed by the rain."""
        return sum(self.scene_length(s) + self.rain_seconds for s in self.scenes)

    @property
    def logo_length(self) -> float:
        """Seconds from a skull scene's start until its rain begins."""
        return self.scene_length("skull")

    def cycle_at(self, t: float) -> int:
        return math.floor(t / self.cycle_length)

    def styles_for(self, cycle: int) -> tuple[str, ...]:
        """The style order of a cycle: ``styles`` rotated by ``cycle``."""
        r = cycle % len(self.styles)
        return self.styles[r:] + self.styles[:r]

    def _shuffled(self, cycle: int) -> list[str]:
        order = list(self.scenes)
        for i in range(len(order) - 1, 0, -1):  # Fisher-Yates from hashes
            j = hash32(self.seed, _ORDER_SALT, cycle, i) % (i + 1)
            order[i], order[j] = order[j], order[i]
        return order

    def scene_order(self, cycle: int) -> tuple[str, ...]:
        """The scenes of a cycle in play order: a seeded shuffle, never starting
        with the scene the previous cycle ended on (two scenes: always A, B)."""
        if len(self.scenes) <= 2:
            return self.scenes
        order = self._shuffled(cycle)
        if order[0] == self._shuffled(cycle - 1)[-1]:  # that cycle's fix only swaps its first two
            order[0], order[1] = order[1], order[0]
        return tuple(order)

    def palette_at(self, now: float | None = None) -> Palette:
        """The palette in use at epoch ``now`` (default: the clock's time)."""
        p = self.palette
        if isinstance(p, Palette):
            return p
        if p == "auto":
            hour = time.localtime(self.clock() if now is None else now).tm_hour
            return BLOOD if DAY_HOURS[0] <= hour < DAY_HOURS[1] else GREEN
        palette: Palette = PALETTES[p]
        return palette

    def plan(self, cycle: int, palette: Palette | None = None) -> tuple[Phase, ...]:
        """All phases of a cycle, in order, with their effects (absolute times),
        colored with ``palette`` (default: the palette at the clock's time)."""
        pal = self.palette_at() if palette is None else palette
        phases = self._plans.get((cycle, pal))
        if phases is not None:
            return phases
        order = self.styles_for(cycle)
        t = cycle * self.cycle_length
        out = []
        j = 0
        for slot, scene in enumerate(self.scene_order(cycle)):
            length = self.scene_length(scene)
            shared = self._scene_effects(scene, cycle, slot, t, length, pal)
            style = order[slot % len(order)]
            for name, span, si, pi, variant in self._spans[scene]:
                seed = hash32(self.seed, _CYCLE_SALT, cycle, j)
                effect, backdrop = self._effect(scene, variant, cycle, t, span, seed, shared, pal)
                if scene == "skull":
                    phase_style = None if si is None else order[si]
                else:
                    phase_style = None if si is None else style
                prev = None if pi is None else order[pi]
                out.append(Phase(name, t, t + span, cycle, phase_style, prev, effect, seed, scene, slot, backdrop))
                t += span
                j += 1
            if self.rain_seconds > 0:
                seed = hash32(self.seed, _CYCLE_SALT, cycle, j)
                out.append(Phase("rain", t, t + self.rain_seconds, cycle, None, None, None, seed, "rain", slot))
                t += self.rain_seconds
                j += 1
        phases = tuple(out)
        if len(self._plans) >= 8:
            self._plans.clear()
        self._plans[(cycle, pal)] = phases
        return phases

    def phase_at(self, t: float, palette: Palette | None = None) -> Phase:
        """The phase playing at t (``.scene``, ``.slot``, ``.name``, ``.style``...)."""
        cycle = self.cycle_at(t)
        local = t - cycle * self.cycle_length
        phases = self.plan(cycle, palette)
        base = phases[0].start
        for phase in phases:
            if local < phase.end - base:
                return phase
        return phases[-1]  # float rounding at the very end of a cycle

    def scene_at(self, t: float) -> str:
        """Name of the scene playing at t ("rain" between scenes)."""
        return self.phase_at(t).scene

    def _neighbour(self, phase: Phase, step: int, palette: Palette) -> Phase:
        """The phase ``step`` (+1 / -1) away from ``phase``, across cycle seams."""
        phases = self.plan(phase.cycle, palette)
        i = next(k for k, p in enumerate(phases) if p.start == phase.start) + step
        if 0 <= i < len(phases):
            return phases[i]
        other = self.plan(phase.cycle + step, palette)
        return other[0] if step > 0 else other[-1]

    def _scene_effects(self, scene: str, cycle: int, slot: int, start: float, length: float,
                       p: Palette) -> dict[str, Effect]:  # fmt: skip
        """Effects shared by all phases of one scene (so they run on seamlessly)."""
        seed = hash32(self.seed, _SCENE_SALT, cycle, slot)
        match scene:
            case "warp":
                return {"stars": Starfield(start=start, duration=None, warp=WARP_SPEED, density=0.08,
                                           fade=1.0, seed=seed, **p.kwargs("starfield"))}
            case "inferno":
                fire = p.kwargs("fire")
                return {"fire": Chain((
                    Fire(start=start, duration=length, height=0.55, fade=1.5, seed=seed, **fire),
                    Fire(start=start, duration=length, height=0.16, fade=1.5, layer="over",
                         speed=18.0, seed=seed + 1, **fire),
                ))}  # fmt: skip
            case "hologram":
                return {"holo": Hologram(start=start, duration=length, intensity=0.55, seed=seed)}
        return {}

    def _effect(self, scene: str, variant: str, cycle: int, start: float, length: float, seed: int,
                shared: dict[str, Effect], p: Palette) -> tuple[Effect | None, Effect | None]:  # fmt: skip
        """(effect, backdrop) of one phase."""
        at: dict[str, Any] = {"start": start, "duration": length, "seed": seed}
        glitch: dict[str, Any] = {**at, "colors": p.glitch}
        decode: dict[str, Any] = {
            **at, "sweep": 0.5, "appear": 0.6, "charset": MORPH_CHARSET, "highlight": p.highlight,
        }  # fmt: skip
        flicker = Glitch(**glitch, bursts=2, burst_min=0.05, burst_max=0.12, intensity=0.35)
        match scene, variant:
            case "skull", "intro" if cycle % 2 == 0:
                return Decode(**decode), None
            case "skull", "intro":
                return Matrix(**at, reveal=True, speed=RAIN_SPEED, spread=0.35, drops=3,
                              tail_len=12, head=p.head, tail=p.tail), None
            case "skull", "hold":
                return Glitch(**glitch, bursts=2, burst_min=0.08, burst_max=0.2, intensity=0.55), None
            case "skull", "rehold":
                return flicker, None
            case "skull", "burst":
                return Glitch(**glitch, bursts=1, burst_min=length, burst_max=length, intensity=0.8), None
            case "skull", "outro":
                return Glitch(**glitch, bursts=4, burst_min=0.1, burst_max=0.15, intensity=1.0), None
            case "skull" | "sysmon", "dissolve" | "shutdown":
                return Reverse(Decode(**decode)), None
            case "warp", "cruise":
                return flicker, shared["stars"]
            case "warp", _:
                return None, shared["stars"]
            case "inferno", "burn":
                return flicker, shared["fire"]
            case "inferno", _:
                return None, shared["fire"]
            case "hologram", "transmit":
                return shared["holo"], None
            case "hologram", _:  # power-up and collapse: heavier interference
                return dataclasses.replace(shared["holo"], intensity=0.9), None
            case "sysmon", "boot":
                return Decode(**decode), None
            case "sysmon", "monitor":
                return flicker, None
        return None, None  # skull morph is built per screen size

    # -- logos ----------------------------------------------------------------

    def _tinted(self, palette: Palette) -> bool:
        return palette == GREEN

    def _load(self, style: str, box_w: int, box_h: int, green: bool) -> Grid | None:
        """The loader's logo for (style, box), recolored for the palette; cached."""
        key = (style, box_w, box_h, green)
        if key in self._loaded:
            self._loaded.move_to_end(key)
            return self._loaded[key]
        if green:
            raw = self._load(style, box_w, box_h, False)
            grid = None if raw is None else _swap_rg(raw)
        else:
            assert self.loader is not None
            grid = self.loader(style, box_w, box_h)
        self._loaded[key] = grid
        while len(self._loaded) > _CACHED_LOGOS:
            self._loaded.popitem(last=False)
        return grid

    def logos(self, width: int, height: int, palette: Palette | None = None) -> dict[str, Grid] | None:
        """Each style's logo centred on a width x height screen (None: rain only).

        Loaded on the first call for a size, then cached (a few sizes)."""
        green = self._tinted(self.palette_at() if palette is None else palette)
        key = (width, height, green)
        if key in self._scenes:
            self._scenes.move_to_end(key)
            return self._scenes[key]
        scene = None
        box_w, box_h = width - BOX_MARGIN[0], height - BOX_MARGIN[1]
        if self.loader is not None and box_w >= MIN_BOX[0] and box_h >= MIN_BOX[1]:
            loaded = {s: self._load(s, box_w, box_h, green) for s in dict.fromkeys(self.styles)}
            fallback = next((g for g in loaded.values() if g is not None), None)
            if fallback is not None:
                scene = {
                    s: _centred(g if g is not None else fallback, width, height)
                    for s, g in loaded.items()
                }
        self._scenes[key] = scene
        while len(self._scenes) > _CACHED_SIZES:
            self._scenes.popitem(last=False)
        return scene

    def scaled_logo(self, style: str, scale: float, width: int, height: int,
                    palette: Palette | None = None) -> Grid | None:  # fmt: skip
        """The logo at ``scale`` x its full size on this screen (not centred).

        The loader is asked for the size steps in ``SCALE_STEPS`` only (cached);
        sizes in between are resampled from the next larger step."""
        green = self._tinted(self.palette_at() if palette is None else palette)
        box_w, box_h = width - BOX_MARGIN[0], height - BOX_MARGIN[1]
        if self.loader is None or box_w < MIN_BOX[0] or box_h < MIN_BOX[1]:
            return None
        styles = [style, *(s for s in self.styles if s != style)]
        full = next((g for s in styles if (g := self._load(s, box_w, box_h, green)) is not None), None)
        if full is None:
            return None
        if scale >= 1.0:
            return full
        style = next(s for s in styles if self._load(s, box_w, box_h, green) is full)
        tw, th = round(full.width * scale), round(full.height * scale)
        if tw < 1 or th < 1:
            return Grid(0, 0)
        step = next(s for s in SCALE_STEPS if s >= scale - 1e-9)
        src = full if step >= 1.0 else self._load(style, max(1, round(box_w * step)),
                                                    max(1, round(box_h * step)), green)  # fmt: skip
        if src is None or src.width < tw or src.height < th:
            src = full
        if step == scale and src is not full:
            return src
        return _resample(src, min(tw, src.width), min(th, src.height))

    # -- rendering ------------------------------------------------------------

    def _rain_over(self, grid: Grid, t: float, rain: Phase, palette: Palette) -> Grid:
        """``grid`` with the rain of phase ``rain`` drawn over it at t. The rain
        runs on into the next phase when that is a skull intro the rain paints in."""
        nxt = self._neighbour(rain, +1, palette)
        feeds = nxt.scene == "skull" and isinstance(nxt.effect, Matrix)
        extend = min(INTRO, _rain_overlap(grid.height)) if feeds else 0.0
        for layer in rain_layers(t, grid.height, rain.start, rain.end, rain.seed, extend, palette):
            grid = layer.apply(grid, t)
        return grid

    def _morph(self, phase: Phase, target: Grid, palette: Palette) -> Morph:
        key = (phase.start, target.width, target.height)
        morph = self._morphs.get(key)
        if morph is None or morph.target is not target:
            if len(self._morphs) >= 4:
                self._morphs.clear()
            morph = Morph(
                target, start=phase.start, duration=phase.end - phase.start, sweep=0.5,
                scramble=0.35, highlight=palette.highlight, seed=phase.seed,
            )  # fmt: skip
            self._morphs[key] = morph
        return morph

    def _carve(self, grid: Grid, t: float, now: float, palette: Palette, start: float, end: float) -> Grid:
        """The time carved into the rain on ``grid`` (modified in place)."""
        if not self.show_clock:
            return grid
        mask = clock_mask(time.strftime(CLOCK_FORMAT, time.localtime(now)), grid.width, grid.height)
        if mask is None:
            return grid
        inside, edge = mask
        cells = grid.cells
        bright: dict[Color | None, Color] = {}
        for i in inside:
            c = cells[i]
            if c.ch != " ":
                fg = bright.get(c.fg)
                if fg is None:
                    fg = bright[c.fg] = lerp_color(c.fg or palette.head, _WHITE, 0.75)
                cells[i] = Cell(c.ch, fg, c.bg)
        if self.clock_outline:
            a = max(0.0, min(1.0, (t - start) / CLOCK_FADE, (end - t) / CLOCK_FADE))
            if a > 0:
                faint = Cell(OUTLINE_CHAR, lerp_color(_BLACK, palette.tail[0], 0.5 * a))
                for i in edge:
                    c = cells[i]
                    if c.ch == " " and c.bg is None:
                        cells[i] = faint
        return grid

    def idle_text(self, t: float, now: float) -> str:
        """``idle 14m · user@host`` at screensaver time t and epoch now."""
        since = self.idle_since if self.idle_since is not None else now - t
        who = self._state.get("user_host")
        if who is None:
            who = self._state["user_host"] = self.user_host or user_at_host()
        return f"idle {fmt_duration(now - since)} · {who}"

    def _idle(self, grid: Grid, t: float, now: float, palette: Palette) -> Grid:
        if not self.show_idle or grid.height < 3:
            return grid
        text = self.idle_text(t, now)
        if len(text) + 2 > grid.width:
            text = text.partition(" · ")[0]
            if len(text) + 2 > grid.width:
                return grid
        grid.put_text(grid.width - len(text) - 1, grid.height - 1, text, palette.tail[1])
        return grid

    def _monitor(self) -> Monitor:
        if self.monitor is not None:
            return self.monitor
        mon = self._state.get("monitor")
        if mon is None:
            mon = self._state["monitor"] = SysMon()
        monitor: Monitor = mon
        return monitor

    def _warp(self, phase: Phase, t: float, width: int, height: int, palette: Palette) -> Grid:
        u = (t - phase.start) / (phase.end - phase.start)
        stars = phase.backdrop
        assert isinstance(stars, Starfield)
        scale = 1.0
        if phase.name == "approach":
            lead = min(WARP_EMPTY, 0.5 * (phase.end - phase.start))
            v = (t - phase.start - lead) / (phase.end - phase.start - lead)
            scale = 0.0 if v < 0 else 1.0 / (1.0 + (WARP_FAR - 1.0) * (1.0 - v) ** 2)
            if v < 0:  # stars only: load every size step now rather than mid-flight
                for step in SCALE_STEPS:
                    self.scaled_logo(phase.style or self.styles[0], step, width, height, palette)
        elif phase.name == "jump":
            scale = 1.0 / (1.0 + WARP_AWAY * in_quad(u))
            span = phase.end - phase.start
            t = t + WARP_BOOST * span * u**3 / 3  # the stars' clock speeds up
            stars = dataclasses.replace(stars, streak=stars.streak * (1.0 + WARP_BOOST * u * u))
        out = Grid(width, height)
        if scale > 0:
            logo = self.scaled_logo(phase.style or self.styles[0], scale, width, height, palette)
            if logo is not None and logo.width and logo.height:
                out = _centred(logo, width, height)
            elif logo is not None:  # too far away to draw: a point of light
                out.set(width // 2, height // 2, Cell("+", palette.head))
        if phase.effect is not None:
            out = phase.effect.apply(out, t)
        return stars.apply(out, t)

    def _inferno(self, phase: Phase, t: float, width: int, height: int, palette: Palette) -> Grid:
        logo = self.scaled_logo(phase.style or self.styles[0], INFERNO_SCALE, width, height, palette)
        out = Grid(width, height)
        if logo is not None:
            rest = max(0, (height - logo.height) // 2 - height // 12)
            u = (t - phase.start) / (phase.end - phase.start)
            y = {
                "ignite": height,
                "rise": round(height + (rest - height) * out_cubic(u)),
                "burn": rest,
                "sink": round(rest + (height - rest) * in_quad(u)),
            }[phase.name]
            out = compose(width, height, [Layer(logo, (width - logo.width) // 2, y)])
        if phase.effect is not None:
            out = phase.effect.apply(out, t)
        assert phase.backdrop is not None
        return phase.backdrop.apply(out, t)

    def _hologram(self, phase: Phase, t: float, width: int, height: int, palette: Palette) -> Grid:
        logo = self.scaled_logo(phase.style or self.styles[0], HOLOGRAM_SCALE, width, height, palette)
        out = Grid(width, height)
        if logo is None:
            return out
        lw, lh = logo.width, logo.height
        ey = height - 3  # the emitter's top row; the idle line stays free below it
        x0 = (width - lw) // 2
        y0 = max(0, (ey - 2 - lh) // 2)
        out = compose(width, height, [Layer(logo, x0, y0)])
        u = (t - phase.start) / (phase.end - phase.start)
        cut = None  # rows of the logo hidden: the projection builds up / collapses
        if phase.name == "project":
            shown = math.ceil(out_cubic(u) * lh)
            cut = (y0, y0 + lh - shown)
        elif phase.name == "collapse":
            cut = (y0, y0 + math.ceil(in_quad(u) * lh))
        if cut is not None:
            a, b = cut
            out.cells[a * width : b * width] = [BLANK] * ((b - a) * width)
            if 0 <= b < y0 + lh and b < height:  # the projector's leading edge
                edge = Cell("▔", palette.head)
                for x in range(x0, x0 + lw):
                    if out.cells[b * width + x].ch != " ":
                        out.cells[b * width + x] = edge
        # The emitter and a faint light cone up to the logo.
        ew = max(4, lw // 4)
        if ey > y0 + lh:
            ex = (width - ew) // 2
            out.put_text(ex, ey, "▄" * ew, palette.tail[0])
            out.put_text(ex + 1, ey + 1, "▀" * (ew - 2), palette.tail[2])
            beam = lerp_color(_BLACK, palette.tail[1], 0.8)
            rows = ey - (y0 + lh)
            for k in range(rows):
                y = ey - 1 - k
                v = (k + 1) / (rows + 1)
                for x in (round(ex + (x0 - ex) * v), round(ex + ew - 1 + (x0 + lw - 1 - ex - ew + 1) * v)):
                    if 0 <= x < width and out.cells[y * width + x].ch == " ":
                        out.cells[y * width + x] = Cell("·", beam)
        assert phase.effect is not None
        return phase.effect.apply(out, t)

    def _sysmon(self, phase: Phase, t: float, width: int, height: int, palette: Palette, now: float) -> Grid:
        stats = self._monitor().stats()
        rows = height - 1 if self.show_idle else height  # keep the idle line's row free
        title = time.strftime("%H:%M:%S", time.localtime(now))
        key = (stats, width, rows, palette, title)
        cached = self._state.get("panel")
        if cached is not None and cached[0] == key:
            panel = cached[1]
        else:
            panel = sysmon_panel(stats, width, rows, palette, title)
            if rows < height:
                panel = Grid(width, height, panel.cells + [BLANK] * width)
            self._state["panel"] = (key, panel)
        assert phase.effect is not None
        return phase.effect.apply(panel, t)

    def _scene(self, phase: Phase, t: float, width: int, height: int, logos: dict[str, Grid],
               palette: Palette, now: float) -> Grid:  # fmt: skip
        match phase.scene:
            case "warp":
                return self._warp(phase, t, width, height, palette)
            case "inferno":
                return self._inferno(phase, t, width, height, palette)
            case "hologram":
                return self._hologram(phase, t, width, height, palette)
            case "sysmon":
                return self._sysmon(phase, t, width, height, palette, now)
        assert phase.style is not None
        logo = logos[phase.style]
        if phase.name == "morph":
            assert phase.prev is not None
            return self._morph(phase, logo, palette).apply(logos[phase.prev], t)
        assert phase.effect is not None
        out = phase.effect.apply(logo, t)
        if phase.name == "intro" and isinstance(phase.effect, Matrix):
            rain = self._neighbour(phase, -1, palette)
            if rain.name == "rain":
                out = self._rain_over(out, t, rain, palette)  # the last rain drains over it
        return out

    def frame(self, t: float, width: int, height: int) -> Grid:
        width, height = max(0, width), max(0, height)
        now = self.clock()
        palette = self.palette_at(now)
        logos = self.logos(width, height, palette)
        if logos is None:  # endless rain
            out = Grid(width, height)
            for layer in rain_layers(t, height, 0.0, math.inf, self.seed, palette=palette):
                out = layer.apply(out, t)
            out = self._carve(out, t, now, palette, 0.0, math.inf)
        else:
            phase = self.phase_at(t, palette)
            if phase.name == "rain":
                out = self._rain_over(Grid(width, height), t, phase, palette)
                out = self._carve(out, t, now, palette, phase.start, phase.end)
            else:
                out = self._scene(phase, t, width, height, logos, palette, now)
        return self._idle(out, t, now, palette)


def default_loader(logo_path: str | os.PathLike[str], *, cache: Path | None = None) -> Loader:
    """A loader for an image file: converted with the startup banner's logo
    settings plus the style, cached on disk, colored with the BLOOD theme.

    Returns None (rain only) if the file is missing or unreadable. If the image
    converter doesn't know the style, the plain logo settings are used instead.
    """
    from .startup import BLOOD as THEME
    from .startup import LOGO_PARAMS, load_logo, recolor

    path = Path(logo_path).expanduser()

    def load(style: str, max_w: int, max_h: int) -> Grid | None:
        if not path.is_file():
            return None
        try:
            try:
                grid = load_logo(path, max_w, max_h, {**LOGO_PARAMS, "style": style}, cache=cache)
            except (TypeError, ValueError, KeyError):  # converter without this style
                grid = load_logo(path, max_w, max_h, LOGO_PARAMS, cache=cache)
        except OSError:
            return None
        return recolor(grid, THEME.logo, THEME.logo_bottom)

    return load
