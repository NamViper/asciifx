"""CRT: an old tube TV switching on (dot -> line -> picture) or off (the reverse)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from ..core.easing import in_out_cubic, in_quad, out_cubic, out_quad
from ..core.grid import BLANK, Cell, Color, Grid
from .base import cell_hashes, hash32, lerp_color, noise, remix, time_bucket

CRT_LINE: Color = (255, 236, 236)
CRT_GLOW: Color = (210, 28, 36)
STATIC_CHARSET = "·:·░:▒·░▓"
"""Snow glyphs, repeated to weight them: mostly specks, a few dense blocks."""
STATIC_COLORS: tuple[Color, ...] = (
    (58, 58, 58), (92, 92, 92), (132, 132, 132), (72, 66, 66), (112, 20, 24), (70, 10, 14),
)

_BLACK: Color = (0, 0, 0)
_STATIC_SALT = 0xC470
_FRAME_SALT = 0xC471  # must differ from _STATIC_SALT: equal salts make remix() cancel to 0

# Stage boundaries (a, b, c) on the power-ON axis u (0 = dark, 1 = picture):
#   [0, a)  the dot fades in      [a, b)  it stretches into a full-width line
#   [b, c)  the line holds        [c, 1]  the line opens into the picture
# "off" runs the same geometry backwards (u = 1 - elapsed fraction), with its own
# boundaries: squash 40%, line 8%, shrink 24%, dot fades 28% of the duration.
STAGES_ON = (0.10, 0.36, 0.40)
STAGES_OFF = (0.28, 0.52, 0.60)


class Beam(NamedTuple):
    """What the tube shows at one moment (see ``CRT.beam``)."""

    stage: str  # "dark" | "dot" | "line" | "band" | "picture"
    level: float  # dot/line brightness, 0..1
    x0: int  # dot/line columns [x0, x1)
    x1: int
    y0: int  # rows of the picture band [y0, y1); for dot/line: the line row
    y1: int
    opening: float  # band height progress: 0 = a line, 1 = full screen
    static: float  # fraction of band cells covered by snow this frame (before flicker)


def span(n: int, total: int) -> tuple[int, int]:
    """[lo, lo + n): ``n`` cells centred in ``total`` (odd leftovers go after)."""
    lo = (total - n) // 2
    return lo, lo + n


@dataclass(frozen=True)
class CRT:
    """A tube TV powering on (``mode="on"``) or off (``mode="off"``).

    "on": black, then a bright dot at the centre fades in and stretches into a
    full-width one-row line (``line_color`` core, ``glow_color`` half-block glow
    above, below and at the ends). The line opens vertically (ease in-out) into
    a band showing the grid; bright edges ride the band's top and bottom, the
    picture starts over-bright, and TV snow (``static_charset`` in
    ``static_colors``, re-rolled ``rate`` times a second) flickers inside the
    band, fading out as the band reaches full height. Blank before ``start``,
    exactly the grid from ``start + duration`` on.

    "off": the reverse. The picture squashes vertically into the line (snow
    growing), the line shrinks to the dot, the dot fades. The grid itself
    before ``start``, blank from ``start + duration`` on: unlike the other
    effects, "off" is not the identity once it ends.

    ``squash=True`` scales the picture into the band (a real tube); ``False``
    just crops it. ``static`` (0 disables) is the snow's peak coverage.
    """

    start: float = 0.0
    duration: float = 1.2
    mode: str = "on"
    line_color: Color = CRT_LINE
    glow_color: Color = CRT_GLOW
    static: float = 0.4
    static_colors: tuple[Color, ...] = STATIC_COLORS
    static_charset: str = STATIC_CHARSET
    rate: float = 24.0
    squash: bool = True
    seed: int = 0

    def __post_init__(self) -> None:
        if self.mode not in ("on", "off"):
            raise ValueError(f"mode must be 'on' or 'off', got {self.mode!r}")
        if not self.static_charset:
            raise ValueError("static_charset must not be empty")
        if not self.static_colors:
            raise ValueError("static_colors needs at least one color")
        if self.static is True or self.static is False:  # static=True/False reads naturally
            object.__setattr__(self, "static", 0.4 if self.static else 0.0)
        object.__setattr__(self, "static_colors", tuple(tuple(c) for c in self.static_colors))
        object.__setattr__(self, "line_color", tuple(self.line_color))
        object.__setattr__(self, "glow_color", tuple(self.glow_color))

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def end_for(self, grid: Grid) -> float:
        return self.end

    # -- geometry ------------------------------------------------------------

    def beam(self, t: float, width: int, height: int) -> Beam:
        """The tube's state at t on a width x height screen."""
        on = self.mode == "on"
        if t < self.start:
            return self._full("dark" if on else "picture", width, height)
        if t >= self.end:
            return self._full("picture" if on else "dark", width, height)
        f = (t - self.start) / self.duration
        u = f if on else 1.0 - f
        a, b, c = STAGES_ON if on else STAGES_OFF
        if u < a:
            x = u / a
            level = out_quad(x) if on else in_quad(x)  # off: quick drop, long afterglow
            x0, x1 = span(min(2, width), width)
            y0, y1 = span(1, height)
            return Beam("dot", level, x0, x1, y0, y1, 0.0, 0.0)
        if u < c:
            n = width if u >= b else round(min(2, width) + (width - min(2, width)) * out_cubic((u - a) / (b - a)))
            x0, x1 = span(n, width)
            y0, y1 = span(1, height)
            return Beam("line", 1.0, x0, x1, y0, y1, 0.0, 0.0)
        v = in_out_cubic((u - c) / (1.0 - c))
        n = round(1 + (height - 1) * v)
        y0, y1 = span(n, height)
        if n <= 1:
            return Beam("line", 1.0, 0, width, y0, y1, v, 0.0)
        return Beam("band", 1.0, 0, width, y0, y1, v, self.static * (1.0 - v) ** 2)

    @staticmethod
    def _full(stage: str, width: int, height: int) -> Beam:
        return Beam(stage, 0.0, 0, width, 0, height, 1.0 if stage == "picture" else 0.0, 0.0)

    # -- rendering -----------------------------------------------------------

    def apply(self, grid: Grid, t: float) -> Grid:
        w, h = grid.width, grid.height
        beam = self.beam(t, w, h)
        if beam.stage == "picture" or not grid.cells:
            return grid.copy()
        if beam.stage == "dark":
            return Grid(w, h)
        out = [BLANK] * (w * h)
        if beam.stage == "band":
            self._band(grid, out, beam, t)
        else:
            self._line(out, w, h, beam)
        return Grid(w, h, out)

    def _line(self, out: list[Cell], w: int, h: int, beam: Beam) -> None:
        """The dot or line: a one-row bar, white-hot in the middle, with glow."""
        x0, x1, y, level = beam.x0, beam.x1, beam.y0, beam.level
        if level <= 0.0:
            return
        line, glow = self.line_color, self.glow_color
        half = (x1 - x0) / 2.0
        mid = (x0 + x1) / 2.0
        base = y * w
        for x in range(x0, x1):
            d = abs(x + 0.5 - mid) / half
            out[base + x] = Cell("█", lerp_color(_BLACK, lerp_color(line, glow, 0.55 * d**4), level))
        g = lerp_color(_BLACK, glow, level)
        if y > 0:
            out[base - w + x0 : base - w + x1] = [Cell("▄", g)] * (x1 - x0)
        if y < h - 1:
            out[base + w + x0 : base + w + x1] = [Cell("▀", g)] * (x1 - x0)
        if x0 > 0:
            out[base + x0 - 1] = Cell("▐", g)
        if x1 < w:
            out[base + x1] = Cell("▌", g)

    def _band(self, grid: Grid, out: list[Cell], beam: Beam, t: float) -> None:
        """The picture inside rows [y0, y1), over-bright edges, and snow."""
        w, h, cells = grid.width, grid.height, grid.cells
        y0, y1, v = beam.y0, beam.y1, beam.opening
        n = y1 - y0
        boost = 0.85 * (1.0 - v) ** 2  # the picture is over-bright while the band is thin
        tinted: dict[Color, Color] = {}
        line = self.line_color
        for j in range(n):
            sy = (2 * j + 1) * h // (2 * n) if self.squash else y0 + j
            row = cells[sy * w : (sy + 1) * w]
            if boost > 0.02:
                row = list(row)
                for x, c in enumerate(row):
                    if c.fg is not None and c.ch != " ":
                        fg = tinted.get(c.fg)
                        if fg is None:
                            fg = tinted[c.fg] = lerp_color(c.fg, line, boost)
                        row[x] = Cell(c.ch, fg, c.bg)
            out[(y0 + j) * w : (y0 + j + 1) * w] = row

        fade = 1.0 - v
        if fade > 0.08:  # bright edges riding the band's top and bottom
            edge = lerp_color(_BLACK, lerp_color(line, self.glow_color, min(1.0, 1.5 * v)), fade)
            if y0 > 0:
                out[(y0 - 1) * w : y0 * w] = [Cell("▄", edge)] * w
            if y1 < h:
                out[y1 * w : (y1 + 1) * w] = [Cell("▀", edge)] * w

        if beam.static <= 0.0:
            return
        frame = time_bucket(t, self.rate)
        amount = beam.static * (0.55 + 0.45 * noise(self.seed, _FRAME_SALT, frame))  # flicker
        thr = int(amount * 65536)
        if thr <= 0:
            return
        snow = _snow_cells(self.static_charset, self.static_colors)
        ns = len(snow)
        rnd = cell_hashes(self.seed, _STATIC_SALT, w, h)
        b = hash32(self.seed, _FRAME_SALT, frame)
        for i in range(y0 * w, y1 * w):
            r = remix(rnd[i], b)
            if r < thr:
                out[i] = snow[r * ns // thr]  # r is uniform in [0, thr): reuse it to pick


_SNOW: dict[tuple[str, tuple[Color, ...]], tuple[Cell, ...]] = {}


def _snow_cells(charset: str, colors: tuple[Color, ...]) -> tuple[Cell, ...]:
    """Every (glyph, color) pair, interleaved so neighbours in the table differ."""
    key = (charset, colors)
    cells = _SNOW.get(key)
    if cells is None:
        cells = tuple(
            Cell(charset[k % len(charset)], colors[(k * 7 + k // len(charset)) % len(colors)])
            for k in range(len(charset) * len(colors))
        )
        if len(_SNOW) > 8:
            _SNOW.clear()
        _SNOW[key] = cells
    return cells
