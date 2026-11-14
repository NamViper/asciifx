"""Summon: a ~13 s full-screen show of the logo, run on demand (never at shell startup).

Timeline (seconds)::

     0.0 -  2.4  countdown  SUMMONING, then 3, 2, 1 in the block font, each one
                            flicking on and off like a CRT
     2.4 -  2.6  black
     2.6 -  3.8  crt_on     the tube switches on: dot, line, then an empty screen
                            opening up with a flicker of snow
     3.8 -  7.2  assemble   the logo builds itself out of sparks (the assembly
                            variant), landing red-hot and cooling; the last one
                            lands at 7.2
     7.2 -  8.6  finale     the finale variant hits as the last spark lands; then
                            blood starts to drip from the teeth (or off the logo)
     8.6 -  9.7  reveal     the name decodes in under the logo, then a glitch hit
     9.7 - 11.7  hold       a glint sweeps across, a hologram flicker; the drips
                            reach the bottom and pool
    11.7 - 12.7  crt_off    the picture squashes into a line, then a dot, then out
    12.7 - 13.0  black

Variants (picked from ``seed`` unless given):

* assembly: ``scatter`` (sparks from all over), ``vortex`` (they spiral in
  along curved orbits), ``explosion`` (a burst from the centre that overshoots,
  then is sucked back into place).
* finale: ``shockwave`` (a round ring expands from the logo, brightening and
  pushing what it passes), ``lightning`` (a branching bolt cracks down onto
  the logo, a white flash, an afterglow), ``eyes`` (the eyes ignite and pulse
  like a heartbeat, the jaw drops and chatters). ``eyes`` needs the logo's
  ``LogoFeatures`` (see ``logofeatures``); without them it falls back to
  ``shockwave``.

``name`` is rendered under the logo in the block font (the small font, then
plain text if it doesn't fit; over the logo's lower part if there is no room
below it, or not at all if it is too wide).

If there is no logo (no loader, the loader returns None, or the screen is too
small) it is a short CRT-only run: on, a moment of empty screen, off (3.5 s).

``frame(t, width, height)`` is a pure function of (t, size); the logo is
loaded once per screen size (box ``width - 4`` x ``height - 2``, less the
rows for the name, like the screensaver), placed and cached.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any

from .core.grid import BLANK, Cell, Color, Grid
from .core.layers import Layer, compose, composite
from .effects import BLOOD, CRT, Assemble, Decode, Glint, Glitch, Palette, hash32, noise
from .effects.base import lerp_color
from .effects.drip import Drip
from .effects.lightning import Lightning
from .effects.shockwave import Shockwave
from .logofeatures import EyeGlow, JawChatter, LogoFeatures
from .screensaver import BOX_MARGIN, MIN_BOX, Loader

# (phase name, seconds)
TIMELINE: tuple[tuple[str, float], ...] = (
    ("countdown", 2.4), ("black", 0.2), ("crt_on", 1.2), ("assemble", 3.4), ("finale", 1.4),
    ("reveal", 1.1), ("hold", 2.0), ("crt_off", 1.0), ("black", 0.3),
)
NO_LOGO_TIMELINE: tuple[tuple[str, float], ...] = (
    ("black", 0.4), ("crt_on", 1.4), ("hold", 0.4), ("crt_off", 1.0), ("black", 0.3),
)
RECOMMENDED_FPS = 30.0

ASSEMBLIES = ("scatter", "vortex", "explosion")
FINALES = ("shockwave", "lightning", "eyes")

# Countdown screens: (text, start, length) in seconds from the start of the countdown.
COUNTDOWN: tuple[tuple[str, float, float], ...] = (
    ("SUMMONING", 0.0, 1.0), ("3", 1.06, 0.42), ("2", 1.5, 0.42), ("1", 1.94, 0.42),
)
COOL_TIME = 0.8  # sparks cool for this long after landing (into the finale)
GLINT_DELAY = 0.1  # into the hold
GLINT_LENGTH = 1.0
HOLO_DELAY = 1.15  # into the hold: the hologram flicker (or the fallback glitch burst)
HOLO_LENGTH = 0.7
NAME_DECODE = 0.75  # the name decodes in over this many seconds of the reveal
NAME_GAP = 1  # blank rows between the logo and the name
NAME_SHARE = 0.38  # the name and its gap may take at most this share of the box height
NAME_FONTS = ("block", "small", "plain")
NAME_MAX = 40

_CACHED_SIZES = 4
_SALT = 0x5A11


@dataclass(frozen=True)
class Phase:
    """One step of the timeline (absolute seconds)."""

    name: str  # countdown | black | crt_on | assemble | finale | reveal | hold | crt_off
    start: float
    end: float


def _plan(spans: tuple[tuple[str, float], ...]) -> tuple[Phase, ...]:
    out, t = [], 0.0
    for name, length in spans:
        end = round(t + length, 9)  # 0.4 + 1.4 is 1.8, not 1.7999999999999998
        out.append(Phase(name, t, end))
        t = end
    return tuple(out)


_FULL = _plan(TIMELINE)
_SHORT = _plan(NO_LOGO_TIMELINE)


def pick_variants(seed: int) -> tuple[str, str]:
    """(assembly, finale) for a seed: every seed gets one of each, all pairs occur."""
    return ASSEMBLIES[hash32(seed, _SALT, 100) % len(ASSEMBLIES)], FINALES[hash32(seed, _SALT, 101) % len(FINALES)]


def scale_blocks(grid: Grid, k: int) -> Grid:
    """A half-block (█ ▀ ▄) drawing ``k`` times bigger, pixel for pixel; colors are kept."""
    if k <= 1:
        return grid.copy()
    w, h = grid.width, grid.height
    out = Grid(w * k, h * k)
    for y in range(h * k):
        top_px, bot_px = (2 * y) // k, (2 * y + 1) // k  # source pixel rows of this cell's halves
        for x in range(w * k):
            sx = x // k
            ct, cb = grid.cells[(top_px // 2) * w + sx], grid.cells[(bot_px // 2) * w + sx]
            t_on = ct.ch in "█▀" if top_px % 2 == 0 else ct.ch in "█▄"
            b_on = cb.ch in "█▀" if bot_px % 2 == 0 else cb.ch in "█▄"
            if t_on or b_on:
                ch = "█" if t_on and b_on else "▀" if t_on else "▄"
                out.cells[y * out.width + x] = Cell(ch, (ct if t_on else cb).fg)
    return out


def _shade(grid: Grid, top: Color, bottom: Color) -> Grid:
    """Every glyph colored by its row: ``top`` .. ``bottom``."""
    n = max(1, grid.height - 1)
    rows = [lerp_color(top, bottom, y / n) for y in range(grid.height)]
    w = grid.width
    cells = [c if c.ch == " " else Cell(c.ch, rows[i // w], c.bg) for i, c in enumerate(grid.cells)]
    return Grid(w, grid.height, cells)


def _text_art(text: str, font: str, top: Color, bottom: Color) -> Grid:
    from .fonts import render_text  # the font tables are big: only when there is text

    if font == "plain":
        return Grid.from_text(text, fg=top)
    return _shade(render_text(text, font), top, bottom)


def _clean_name(name: str | None) -> str | None:
    if name is None:
        return None
    name = re.sub(r"\s+", " ", "".join(ch if ch.isprintable() else " " for ch in name)).strip()
    return name[:NAME_MAX] or None


@dataclass
class _Scene:
    """Everything about one screen size that doesn't depend on t."""

    logo: Grid  # full screen: the logo alone, in place
    box: tuple[int, int, int, int]  # the logo's x, y, width, height on screen
    name: Grid | None = None
    name_at: tuple[int, int] = (0, 0)
    name_over: bool = False  # drawn over the logo's lower part (opaque) instead of under it
    fx: dict[str, Any] = field(default_factory=dict)  # per-size effects
    texts: dict[str, Grid] = field(default_factory=dict)  # countdown screens


@dataclass(frozen=True, eq=False)
class Summon:
    """Full-screen frame Source: the logo summoned by a countdown, CRT, sparks and a finale.

    ``loader(style, max_w, max_h)`` returns the colored logo, or None (see
    ``screensaver.default_loader``). ``palette`` colors the CRT line, the
    texts and the glitches; ``seed`` varies everything and picks the variants.
    ``assembly`` (``ASSEMBLIES``) and ``finale`` (``FINALES``) choose them
    instead; after construction both hold the variant actually played (an
    ``"eyes"`` finale without ``features`` becomes ``"shockwave"``). ``name``
    is revealed under the logo (None: no name). ``features`` locates the
    logo's eyes and jaw (``logofeatures.load_features``). Play it for
    ``duration_for(width, height)`` (or ``duration``) seconds.
    """

    loader: Loader | None
    style: str = "dots"
    palette: Palette = BLOOD
    seed: int = 0
    name: str | None = None
    assembly: str | None = None
    finale: str | None = None
    features: LogoFeatures | None = None
    _effects: dict[bool, dict[str, Any]] = field(init=False, repr=False)
    _scenes: OrderedDict[tuple[int, int], _Scene | None] = field(
        init=False, repr=False, default_factory=OrderedDict
    )

    def __post_init__(self) -> None:
        if self.assembly is not None and self.assembly not in ASSEMBLIES:
            raise ValueError(f"assembly must be one of {', '.join(ASSEMBLIES)}, got {self.assembly!r}")
        if self.finale is not None and self.finale not in FINALES:
            raise ValueError(f"finale must be one of {', '.join(FINALES)}, got {self.finale!r}")
        assembly, finale = pick_variants(self.seed)
        assembly = self.assembly or assembly
        finale = self.finale or finale
        if finale == "eyes" and self.features is None:
            finale = "shockwave"
        object.__setattr__(self, "assembly", assembly)
        object.__setattr__(self, "finale", finale)
        object.__setattr__(self, "name", _clean_name(self.name))
        object.__setattr__(self, "_effects", {True: self._build(_FULL), False: self._build(_SHORT)})

    # -- timeline -------------------------------------------------------------

    @staticmethod
    def plan(logo: bool = True) -> tuple[Phase, ...]:
        """All phases in order: the full show, or (``logo=False``) the CRT-only one."""
        return _FULL if logo else _SHORT

    @staticmethod
    def phase(name: str, logo: bool = True) -> Phase:
        """The first phase called ``name``."""
        return next(p for p in Summon.plan(logo) if p.name == name)

    def phase_at(self, t: float, *, logo: bool = True) -> Phase:
        """The phase at t; before 0 the first, from the end on the last (black)."""
        phases = self.plan(logo)
        for phase in phases:
            if t < phase.end:
                return phase
        return phases[-1]

    @property
    def duration(self) -> float:
        """Length of the show: the full one if there is a loader (it may still
        find no logo at some size: see ``duration_for``)."""
        return self.plan(self.loader is not None)[-1].end

    def duration_for(self, width: int, height: int) -> float:
        """Length of the show on a width x height screen (loads the logo)."""
        return self.plan(self.logo(width, height) is not None)[-1].end

    def _seed(self, k: int) -> int:
        return hash32(self.seed, _SALT, k)

    def _build(self, phases: tuple[Phase, ...]) -> dict[str, Any]:
        """The effects that don't depend on the screen size."""
        p = self.palette
        at = {ph.name: ph for ph in phases if ph.name != "black"}
        seed = self._seed
        on, off, hold = at["crt_on"], at["crt_off"], at["hold"]
        fx: dict[str, Any] = {
            "crt_on": CRT(start=on.start, duration=on.end - on.start, mode="on",
                          line_color=p.head, glow_color=p.tail[0], seed=seed(1)),
            "crt_off": CRT(start=off.start, duration=off.end - off.start, mode="off",
                           line_color=p.head, glow_color=p.tail[0], seed=seed(2)),
        }  # fmt: skip
        if "assemble" not in at:
            return fx
        a, reveal, cd = at["assemble"], at["reveal"], at["countdown"]
        kind = {
            "scatter": {"origin": "scatter", "spread": 0.6},
            "vortex": {"origin": "vortex", "spread": 0.45, "turns": 0.9},
            "explosion": {"origin": "explosion", "spread": 0.06, "burst": 0.45},
        }[str(self.assembly)]
        fx["assemble"] = Assemble(start=a.start, duration=a.end - a.start + COOL_TIME, cool_time=COOL_TIME,
                                  seed=seed(4), **kind)
        fx["glint"] = Glint(start=hold.start + GLINT_DELAY, duration=GLINT_LENGTH, color=(255, 255, 255),
                            width=7.0, strength=0.85)
        fx["decode"] = Decode(start=reveal.start, duration=NAME_DECODE, sweep=0.5, appear=0.6,
                              highlight=p.highlight, seed=seed(5))
        fx["hit"] = Glitch(start=reveal.start + NAME_DECODE, duration=0.24, bursts=1, burst_min=0.13,
                           burst_max=0.17, intensity=0.85, colors=p.glitch, seed=seed(6))
        fx["holo"] = _hologram(hold.start + HOLO_DELAY, HOLO_LENGTH, seed(7), p)
        fx["countdown"] = tuple(
            (text, CRT(start=cd.start + s, duration=min(0.22, 0.3 * n), mode="on", line_color=p.head,
                       glow_color=p.tail[0], static=0.25, seed=seed(20 + k)),
             CRT(start=cd.start + s + n - min(0.18, 0.25 * n), duration=min(0.18, 0.25 * n), mode="off",
                 line_color=p.head, glow_color=p.tail[0], static=0.25, seed=seed(30 + k)))
            for k, (text, s, n) in enumerate(COUNTDOWN)
        )  # fmt: skip
        return fx

    # -- layout ---------------------------------------------------------------

    def logo(self, width: int, height: int) -> Grid | None:
        """The logo in place on a width x height screen, or None (CRT only).

        Without a name it is centred; with one, the logo and the name below it
        are centred together. Loaded on the first call for a size, then cached
        (a few sizes)."""
        scene = self._scene(width, height)
        return None if scene is None else scene.logo

    def _scene(self, width: int, height: int) -> _Scene | None:
        key = (width, height)
        if key in self._scenes:
            self._scenes.move_to_end(key)
            return self._scenes[key]
        scene = self._layout(width, height)
        if scene is not None:
            self._dress(scene, width, height)
        self._scenes[key] = scene
        while len(self._scenes) > _CACHED_SIZES:
            self._scenes.popitem(last=False)
        return scene

    def _layout(self, width: int, height: int) -> _Scene | None:
        box_w, box_h = width - BOX_MARGIN[0], height - BOX_MARGIN[1]
        if self.loader is None or box_w < MIN_BOX[0] or box_h < MIN_BOX[1]:
            return None
        top, bottom = self.palette.head, self.palette.tail[0]
        name, under = None, False
        if self.name:
            fitting = [g for g in (_text_art(self.name, f, top, bottom) for f in NAME_FONTS) if g.width <= box_w]
            for g in fitting:
                need = g.height + NAME_GAP
                if need <= NAME_SHARE * box_h and box_h - need >= MIN_BOX[1]:
                    name, under = g, True
                    break
            else:
                name = fitting[0] if fitting else None  # over the logo's lower part, or no name at all
        art = self.loader(self.style, box_w, box_h - (name.height + NAME_GAP if name is not None and under else 0))
        if art is None:
            return None
        group = art.height + (name.height + NAME_GAP if name is not None and under else 0)
        x, y = (width - art.width) // 2, (height - group) // 2
        scene = _Scene(compose(width, height, [Layer(art, x, y)]), (x, y, art.width, art.height))
        if name is not None:
            ny = y + art.height + NAME_GAP if under else y + art.height - name.height
            scene.name, scene.name_at, scene.name_over = name, ((width - name.width) // 2, ny), not under
        return scene

    def _dress(self, scene: _Scene, width: int, height: int) -> None:
        """The size-dependent effects and texts of a scene."""
        fx = self._effects[True]
        fx["assemble"].particles(scene.logo)  # precompute now, not mid-animation
        lx, ly, lw, lh = scene.box
        finale = self.phase("finale")
        cx, cy = lx + lw / 2.0, ly + lh / 2.0
        seed = self._seed
        sfx = scene.fx
        eyes, jaw = self.features.place(lx, ly, lw, lh) if self.features is not None else ((), None)
        if self.finale == "eyes":
            if eyes:
                sfx["eyes"] = EyeGlow(start=finale.start, eyes=eyes, seed=seed(40))
            if jaw is not None:
                sfx["jaw"] = JawChatter(start=finale.start + 0.6, jaw=jaw, seed=seed(41))
        if self.finale == "shockwave":
            sfx["finale"] = Shockwave(start=finale.start, duration=1.2, center=(cx / width, cy / height),
                                      color=(255, 235, 230), ring=self.palette.tail[0], push=1.6, seed=seed(42))
        elif self.finale == "lightning":  # strikes between the eyes, or the logo's upper middle
            tx, ty = (sum(e.cx for e in eyes) / len(eyes), sum(e.cy for e in eyes) / len(eyes)) if eyes \
                else (cx, ly + 0.45 * lh)
            sfx["finale"] = Lightning(start=finale.start - 0.14, duration=1.35, leader=0.14,
                                      target=(tx / width, ty / height), glow=self.palette.tail[0], seed=seed(43))
        drip_start = finale.start + (1.5 if self.finale == "eyes" else 0.6)  # eyes: after the jaw shuts
        end = self.plan()[-1].end
        drip = Drip(start=drip_start, duration=end - drip_start, fade=0.0, count=4, stagger=0.8, speed=6.0,
                    accel=16.0, trail=self.palette.tail[1], head=self.palette.tail[0], seed=seed(44))
        if jaw is not None:  # from the teeth: seeded columns in the middle of the jaw line
            a, b = jaw.x0, jaw.x1
            inner = sorted(range(a + (b - a) // 6, b - (b - a) // 6), key=lambda x: hash32(seed(45), x))
            origins: list[tuple[int, int]] = []
            for x in inner:
                if len(origins) < drip.count and all(abs(x - ox) >= 3 for ox, _ in origins):
                    origins.append((x, jaw.y))
            sfx["drip"] = replace(drip, origins=tuple(origins))
        else:  # off the bottom edge of the logo
            sfx["drip"] = replace(drip, origins=drip.auto_origins(scene.logo))
        box_w, box_h = width - BOX_MARGIN[0], height - BOX_MARGIN[1]
        top, bottom = self.palette.head, self.palette.tail[0]
        for text, _, _ in COUNTDOWN:
            scene.texts[text] = _countdown_screen(text, width, height, box_w, box_h, top, bottom)

    # -- rendering ------------------------------------------------------------

    def frame(self, t: float, width: int, height: int) -> Grid:
        width, height = max(0, width), max(0, height)
        scene = self._scene(width, height)
        fx = self._effects[scene is not None]
        match self.phase_at(t, logo=scene is not None).name:
            case "countdown" if scene is not None:
                return self._countdown(scene, t, width, height)
            case "crt_on":
                return fx["crt_on"].apply(Grid(width, height), t)
            case "assemble" | "finale" | "reveal" | "hold" if scene is not None:
                return self._show(scene, t)
            case "crt_off":
                return fx["crt_off"].apply(self._show(scene, t) if scene is not None else Grid(width, height), t)
        return Grid(width, height)

    def _show(self, scene: _Scene, t: float) -> Grid:
        """The logo with everything on it at t (from the assembly to the power-off)."""
        fx, sfx = self._effects[True], scene.fx
        g = fx["assemble"].apply(scene.logo, t)
        glint = fx["glint"]
        if glint.start <= t < glint.end:
            g = glint.apply(g, t)
        for key in ("jaw", "eyes"):
            if key in sfx:
                g = sfx[key].apply(g, t)
        decode = fx["decode"]
        if scene.name is not None and t >= decode.start:
            composite(g, Layer(decode.apply(scene.name, t), *scene.name_at, opaque=scene.name_over))
        g = sfx["drip"].apply(g, t)
        if "finale" in sfx:
            g = sfx["finale"].apply(g, t)
        for key in ("hit", "holo"):
            e = fx[key]
            if e.start <= t < e.end:
                g = e.apply(g, t)
        return g

    def _countdown(self, scene: _Scene, t: float, width: int, height: int) -> Grid:
        for text, on, off in self._effects[True]["countdown"]:
            if on.start <= t < off.end:
                art = scene.texts[text]
                if t < on.end:
                    return on.apply(art, t)
                return off.apply(_crt_style(art, t, self._seed(50)), t)
        return Grid(width, height)


def _hologram(start: float, duration: float, seed: int, palette: Palette) -> Any:
    """A short hologram flicker (``effects.hologram``, imported lazily).

    FALLBACK: if that module is not there, a Glitch burst stands in for it."""
    try:
        from .effects.hologram import Hologram
    except ImportError:
        return Glitch(start=start, duration=duration, bursts=2, burst_min=0.12, burst_max=0.2,
                      intensity=0.5, colors=palette.glitch, seed=seed)
    return Hologram(start=start, duration=duration, intensity=0.6, seed=seed)


def _countdown_screen(text: str, width: int, height: int, box_w: int, box_h: int, top: Color, bottom: Color) -> Grid:
    """``text`` as big as fits (block font, scaled up to 3x; then small; then plain), centred."""
    from .fonts import render_text

    art = None
    for font in ("block", "small"):
        g = render_text(text, font)
        if g.width > box_w or g.height > box_h:
            continue
        k = next((k for k in (3, 2) if g.width * k <= box_w and g.height * k <= 0.45 * box_h), 1)
        art = _shade(scale_blocks(g, k), top, bottom)
        break
    if art is None:
        art = Grid.from_text(text[: max(0, box_w)], fg=top)
    return compose(width, height, [Layer(art, (width - art.width) // 2, (height - art.height) // 2)])


_SCAN = 0.55  # brightness of every other row
_FLICKER = 20.0  # brightness changes per second


def _crt_style(grid: Grid, t: float, seed: int) -> Grid:
    """CRT look for a still: dim scanlines, a brightness flicker, now and then a
    row band slipping sideways by a cell."""
    w, h = grid.width, grid.height
    frame = int(t * _FLICKER)
    level = 0.82 + 0.18 * noise(seed, frame)
    out = list(grid.cells)
    shades: dict[tuple[Color | None, float], Color] = {}
    for i, c in enumerate(out):
        if c.ch != " " and c.fg is not None:
            k = level * (_SCAN if (i // w) % 2 else 1.0)
            fg = shades.get((c.fg, k))
            if fg is None:
                fg = shades[(c.fg, k)] = lerp_color((0, 0, 0), c.fg, k)
            out[i] = Cell(c.ch, fg, c.bg)
    r = hash32(seed, 1, frame)
    if r % 4 == 0 and h > 0 and w > 1:  # a slipped band
        y0 = (r >> 4) % h
        for y in range(y0, min(h, y0 + 1 + (r >> 12) % 2)):
            row = out[y * w : (y + 1) * w]
            out[y * w : (y + 1) * w] = [BLANK] + row[:-1] if (r >> 14) & 1 else row[1:] + [BLANK]
    return Grid(w, h, out)


__all__ = [
    "ASSEMBLIES", "COUNTDOWN", "FINALES", "NO_LOGO_TIMELINE", "RECOMMENDED_FPS", "TIMELINE", "Phase",
    "Summon", "pick_variants", "scale_blocks",
]
