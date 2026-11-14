"""Hacker-style shell startup banner: logo + boot log + system info, animated inline.

Startup speed matters (this runs on every new shell): the logo is converted once
and cached as JSON, so the common path never imports numpy/Pillow.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core.grid import Cell, Color, Grid
from .core.layers import Layer, compose

CACHE_VERSION = 1
GAP = 4  # columns between logo and text
MARGIN = 2  # left margin
MAX_LOGO_W, MAX_LOGO_H = 40, 20
MIN_LOGO_W, MIN_LOGO_H = 16, 8
GOOD_SIDE_LOGO_W = 24  # below this, prefer stacking the logo above the text

BOOT_STEPS = ("establishing uplink", "bypassing firewall", "decrypting payload")
BOOT_WIDTH = 30  # "> label ....." column where "[ OK ]" starts
GRANTED = "ACCESS GRANTED"


# -- theme -------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    accent: Color = (225, 25, 35)  # ">" prompts, labels, ACCESS GRANTED
    leader: Color = (95, 12, 16)  # the "....." leaders
    text: Color = (205, 205, 205)  # boot text and info values
    ok: Color = (250, 250, 250)  # [ OK ]
    logo: Color = (240, 236, 236)  # the skull, top...
    logo_bottom: Color = (200, 20, 32)  # ...fading to blood red at the jaw


BLOOD = Theme()


# -- system info ---------------------------------------------------------------


def _os_name(os_release: Path = Path("/etc/os-release")) -> str:
    try:
        for line in os_release.read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except (OSError, ValueError):  # unreadable, or not valid text (UnicodeDecodeError)
        pass
    return f"{platform.system()} {platform.release()}".strip()


def format_uptime(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _uptime(proc_uptime: Path = Path("/proc/uptime")) -> str:
    try:
        return format_uptime(float(proc_uptime.read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return "unknown"


def sysinfo(now: float | None = None) -> list[tuple[str, str]]:
    """(label, value) pairs shown under the boot log. Fast: no subprocesses."""
    try:
        user = getpass.getuser()
    except Exception:  # getuser raises various errors when no user info is available
        user = os.environ.get("USER", "user")
    return [
        ("user", f"{user}@{socket.gethostname()}"),
        ("os", _os_name()),
        ("kernel", platform.release()),
        ("uptime", _uptime()),
        ("date", time.strftime("%a %d %b %Y %H:%M", time.localtime(now))),
    ]


# -- text block ----------------------------------------------------------------


@dataclass
class TextLine:
    """One line of the text block, positioned relative to the block's top-left."""

    row: int
    grid: Grid
    kind: str  # "boot" | "granted" | "info"
    index: int = 0  # position among lines of the same kind


def _segments(*parts: tuple[str, Color]) -> Grid:
    grid = Grid(sum(len(s) for s, _ in parts), 1)
    x = 0
    for s, color in parts:
        grid.put_text(x, 0, s, fg=color)
        x += len(s)
    return grid


def text_lines(info: list[tuple[str, str]], theme: Theme, max_width: int = 60) -> list[TextLine]:
    lines: list[TextLine] = []
    for i, step in enumerate(BOOT_STEPS):
        dots = "." * max(3, BOOT_WIDTH - len(step) - 3)
        grid = _segments(("> ", theme.accent), (step + " ", theme.text), (dots, theme.leader),
                         (" [", theme.accent), (" OK ", theme.ok), ("]", theme.accent))
        lines.append(TextLine(i, grid, "boot", i))
    lines.append(TextLine(len(BOOT_STEPS), _segments((GRANTED, theme.accent)), "granted"))
    label_w = max(len(label) for label, _ in info) + 2 if info else 0
    first = len(BOOT_STEPS) + 2  # blank line after ACCESS GRANTED
    for i, (label, value) in enumerate(info):
        value = value if label_w + len(value) <= max_width else value[: max_width - label_w - 1] + "…"
        grid = _segments((label.ljust(label_w), theme.accent), (value, theme.text))
        lines.append(TextLine(first + i, grid, "info", i))
    return lines


def block_size(lines: list[TextLine]) -> tuple[int, int]:
    return max(line.grid.width for line in lines), max(line.row for line in lines) + 1


# -- logo (cached image conversion) ---------------------------------------------

# Dark-ink-on-white logos: invert so the ink glows, drop the white background
# around the subject, trim it, and remove stray specks. Colors come from the theme.
LOGO_PARAMS: dict[str, Any] = {
    "mode": "braille", "invert": True, "transparent_bg": True, "autocrop": True,
    "despeckle": True, "color": False,
}


def cache_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "asciifx"


def _convert(path: Path, width: int, height: int, params: dict[str, Any]) -> Grid:
    from .sources.image import image_to_grid  # numpy + Pillow: only on a cache miss

    return image_to_grid(path, width, height, **params)


def load_logo(
    path: str | os.PathLike[str],
    width: int,
    height: int,
    params: dict[str, Any] | None = None,
    *,
    cache: Path | None = None,
    convert: Callable[[Path, int, int, dict[str, Any]], Grid] = _convert,
) -> Grid:
    """Image -> Grid, cached on disk keyed by file identity, size and parameters."""
    params = LOGO_PARAMS if params is None else params
    p = Path(path).expanduser().resolve()
    st = p.stat()
    from .sources.styles import CONVERTER_VERSION  # plain data, no numpy

    ident = [CACHE_VERSION, CONVERTER_VERSION, str(p), st.st_mtime_ns, st.st_size, width, height, params]
    key = hashlib.sha256(json.dumps(ident, sort_keys=True, default=str).encode()).hexdigest()[:20]
    file = (cache or cache_dir()) / f"logo-{key}.json"
    try:
        return Grid.from_dict(json.loads(file.read_text()))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    grid = convert(p, width, height, params)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(grid.to_dict(), separators=(",", ":")))
        os.replace(tmp, file)
    except OSError:
        pass  # caching is best-effort
    return grid


def recolor(grid: Grid, top: Color, bottom: Color | None = None) -> Grid:
    """Same glyphs; non-blank cells colored with a vertical gradient top -> bottom."""
    bottom = top if bottom is None else bottom
    out = []
    for y, row in enumerate(grid.rows()):
        u = y / max(1, grid.height - 1)
        fg = tuple(round(a + (b - a) * u) for a, b in zip(top, bottom))
        out.extend(c if c.ch == " " and c.bg is None else Cell(c.ch, fg, c.bg) for c in row)  # type: ignore[arg-type]
    return Grid(grid.width, grid.height, out)


# -- layout --------------------------------------------------------------------


@dataclass
class Layout:
    width: int
    height: int  # includes one blank row at the bottom, before the prompt
    logo_xy: tuple[int, int] | None
    text_xy: tuple[int, int]


def choose_arrangement(
    cols: int, rows: int, text_w: int, text_h: int, max_logo: tuple[int, int] = (MAX_LOGO_W, MAX_LOGO_H)
) -> tuple[str, tuple[int, int] | None] | None:
    """("side" | "stacked", max logo size) or ("text", None); None if nothing fits.

    Leaves a few rows free so the banner never pushes the prompt off screen.
    """
    max_h = rows - 3
    if MARGIN + text_w > cols or text_h + 1 > max_h:
        return None
    max_w, max_h_logo = max_logo
    side = (min(max_w, cols - MARGIN - GAP - text_w - 1), min(max_h_logo, max_h - 1))
    stacked = (min(max_w, cols - 2 * MARGIN), min(max_h_logo, max_h - text_h - 2))
    side_ok = side[0] >= MIN_LOGO_W and side[1] >= MIN_LOGO_H
    stacked_ok = stacked[0] >= MIN_LOGO_W and stacked[1] >= MIN_LOGO_H
    if side_ok and (side[0] >= GOOD_SIDE_LOGO_W or not stacked_ok):
        return "side", side
    if stacked_ok:  # a squeezed logo next to the text looks worse than a full one above it
        return "stacked", stacked
    return "text", None


def arrange(kind: str, logo: Grid | None, text_w: int, text_h: int) -> Layout:
    """Positions for the actual logo size: side by side (vertically centered) or stacked."""
    if logo is None or kind == "text":
        return Layout(MARGIN + text_w, text_h + 1, None, (MARGIN, 0))
    if kind == "side":
        h = max(logo.height, text_h)
        return Layout(MARGIN + logo.width + GAP + text_w, h + 1,
                      (MARGIN, (h - logo.height) // 2), (MARGIN + logo.width + GAP, (h - text_h) // 2))
    return Layout(MARGIN + max(logo.width, text_w), logo.height + 1 + text_h + 1,
                  (MARGIN, 0), (MARGIN, logo.height + 1))


# -- the banner ----------------------------------------------------------------


# Timeline for the default 3.5 s run (seconds); scaled to the requested duration.
#   0.0-1.3  red rain sweeps the banner and paints it in
#   0.3-1.7  the skull decodes from scrambled braille
#   1.2-2.2  boot log types out
#   2.2-2.6  ACCESS GRANTED decodes
#   1.7-3.3  glitch bursts over everything
#   2.5-3.3  system info types out, then hold
TIMELINE_LENGTH = 3.5


def _block(lines: list[TextLine], width: int) -> tuple[Grid, int]:
    """The given text lines as one grid, plus the row it starts at in the text block."""
    top = min(line.row for line in lines)
    grid = Grid(width, max(line.row for line in lines) - top + 1)
    for line in lines:
        for x, cell in enumerate(line.grid.cells):
            grid.set(x, line.row - top, cell)
    return grid, top


@dataclass
class StartupBanner:
    """Final banner = logo + text block; ``frame(t)`` animates toward it."""

    layout: Layout
    logo: Grid | None
    lines: list[TextLine]
    theme: Theme = BLOOD
    duration: float = TIMELINE_LENGTH
    logo_charset: str | None = None  # scramble glyphs for the logo decode (None = braille)
    _parts: list[tuple[Grid, tuple[int, int], Any]] = field(init=False, repr=False)
    _overlay: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from .effects import BLOOD as FX, BRAILLE_CHARSET, chain, make

        k = self.duration / TIMELINE_LENGTH  # stretch/compress the whole timeline

        def at(start: float, duration: float) -> dict[str, float]:
            return {"start": start * k, "duration": duration * k}

        self._parts = []
        if self.logo is not None and self.layout.logo_xy is not None:
            skull_fx = make("decode", FX, **at(0.3, 1.4), sweep=0.5,
                            charset=self.logo_charset or BRAILLE_CHARSET)
            self._parts.append((self.logo, self.layout.logo_xy, skull_fx))
        text_w = max(line.grid.width for line in self.lines)
        tx, ty = self.layout.text_xy
        effects = {
            "boot": make("typewriter", FX, **at(1.2, 1.0)),
            "granted": make("decode", FX, **at(2.2, 0.4), appear=0.6, highlight=(255, 255, 255)),
            "info": make("typewriter", FX, **at(2.5, 0.8)),
        }
        for kind, fx in effects.items():
            lines = [line for line in self.lines if line.kind == kind]
            if lines:
                grid, row = _block(lines, text_w)
                self._parts.append((grid, (tx, ty + row), fx))
        self._overlay = chain(
            make("matrix", FX, **at(0.0, 1.3)),
            make("glitch", FX, **at(1.7, 1.6), bursts=3),
        )

    def final(self) -> Grid:
        """The finished banner, without any effects."""
        layers = [Layer(grid, *xy) for grid, xy, _ in self._parts]
        return compose(self.layout.width, self.layout.height, layers)

    def frame(self, t: float) -> Grid:
        if t >= self.duration:
            return self.final()
        layers = [Layer(fx.apply(grid, t), *xy) for grid, xy, fx in self._parts]
        return self._overlay.apply(compose(self.layout.width, self.layout.height, layers), t)


def build(
    cols: int,
    rows: int,
    logo_path: str | os.PathLike[str] | None,
    *,
    theme: Theme = BLOOD,
    duration: float = 3.5,
    info: list[tuple[str, str]] | None = None,
    logo_params: dict[str, Any] | None = None,
    style: str | None = None,
    size: tuple[int, int] | None = None,
    cache: Path | None = None,
) -> StartupBanner | None:
    """Lay out the banner for a cols x rows terminal; None if it can't fit.

    ``size`` is the max logo (columns, rows); default: the style's ``preferred_size``.
    """
    lines = text_lines(sysinfo() if info is None else info, theme, max_width=min(60, cols - MARGIN - 1))
    text_w, text_h = block_size(lines)
    from .sources.styles import preferred_size, scramble_charset

    choice = choose_arrangement(cols, rows, text_w, text_h, size or preferred_size(style))
    if choice is None:
        return None
    kind, box = choice
    logo = None
    if box is not None and logo_path is not None and Path(logo_path).expanduser().is_file():
        params = dict(LOGO_PARAMS if logo_params is None else logo_params)
        if style is not None:
            params["style"] = style
        logo = recolor(load_logo(logo_path, *box, params, cache=cache), theme.logo, theme.logo_bottom)
    return StartupBanner(arrange(kind, logo, text_w, text_h), logo, lines, theme, duration,
                         scramble_charset(style))


def run(
    logo_path: str | os.PathLike[str] | None,
    *,
    duration: float = 3.5,
    theme: Theme = BLOOD,
    style: str | None = None,
    size: tuple[int, int] | None = None,
    terminal: Any = None,
) -> None:
    """Show the startup banner inline below the cursor; any key skips to the end."""
    from .api import animate_inline
    from .term import Terminal

    term = terminal if terminal is not None else Terminal(fullscreen=False)
    cols, rows = term.size()
    try:
        banner = build(cols, rows, logo_path, theme=theme, duration=duration, style=style, size=size)
    except Exception:  # corrupt/truncated logo: the text-only banner, not an error line in every new shell
        if logo_path is None or os.environ.get("ASCIIFX_DEBUG"):
            raise
        banner = build(cols, rows, None, theme=theme, duration=duration, style=style, size=size)
    if banner is not None:
        animate_inline(banner.frame, banner.duration, terminal=term)
