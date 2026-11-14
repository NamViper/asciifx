"""Public library API."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .core.grid import Color, Grid
from .core.loop import DEFAULT_FPS, Tick, run
from .sources.base import Source
from .spinner import SPINNERS, Spinner, spinner  # noqa: F401  (public API re-export)
from .term import Terminal

if TYPE_CHECKING:
    from .effects.base import Effect


def play(
    source: Source,
    *,
    fps: float = DEFAULT_FPS,
    duration: float | None = None,
    terminal: Terminal | None = None,
) -> None:
    """Play ``source`` full-screen until any key is pressed (or ``duration`` elapses).

    When output is not a terminal, prints a single static frame (t=0) instead.
    """
    term = terminal if terminal is not None else Terminal()
    if not term.is_tty:
        term.render(source.frame(0.0, *term.size()))
        return

    pressed: list[str] = []

    def wait(timeout: float) -> None:
        pressed.extend(term.read_keys(timeout))

    def step(tick: Tick) -> bool:
        if pressed:
            return False
        term.render(source.frame(tick.t, *term.size()))
        return True

    with term:
        run(step, fps=fps, duration=duration, wait=wait)


def animate_inline(
    frame: Callable[[float], Grid],
    duration: float,
    *,
    fps: float = DEFAULT_FPS,
    terminal: Terminal | None = None,
) -> bool:
    """Animate ``frame(t)`` for t in [0, duration] in place, below the cursor.

    The final frame (``t == duration``) stays on screen and in scrollback.
    Any key skips straight to it. ``frame`` must return grids of a constant size.
    When output is not a terminal, only the final frame is printed, as plain text.
    Returns True if the animation was skipped by a key press.
    """
    term = terminal if terminal is not None else Terminal(fullscreen=False)
    if not term.is_tty:
        term.render(frame(duration))
        return False

    pressed: list[str] = []

    def wait(timeout: float) -> None:
        pressed.extend(term.read_keys(timeout))

    def step(tick: Tick) -> bool:
        if pressed:
            return False
        term.render(frame(tick.t))
        return True

    with term:
        run(step, fps=fps, duration=duration, wait=wait)
        if pressed:
            term.render(frame(duration))
    return bool(pressed)


def banner(
    art: str | Grid,
    *,
    font: str | None = None,
    fg: Color | None = None,
    gradient: tuple[Color, Color] | None = None,
    align: str = "left",
    effect: Effect | None = None,
    duration: float | None = None,
    fps: float = DEFAULT_FPS,
    terminal: Terminal | None = None,
) -> None:
    """Show ``art`` (text or a Grid) inline, animated by ``effect``; any key skips.

    With ``font`` (see ``asciifx.fonts.FONTS``) text is drawn in big letters,
    word-wrapped to the terminal width; without it, text is shown as-is.
    ``duration`` defaults to ``effect.end_for(grid)`` (0 without an effect).
    """
    term = terminal if terminal is not None else Terminal(fullscreen=False)
    if isinstance(art, Grid):
        grid = art
    elif font is not None:
        from .fonts import render_text

        cols, _ = term.size()
        grid = render_text(art, font, fg=fg, gradient=gradient, align=align, max_width=max(1, cols - 1))
    else:
        grid = Grid.from_text(art, fg=fg)
    if duration is None:
        # end_for: some effects' length depends on the grid (e.g. how much text to type)
        duration = effect.end_for(grid) if effect is not None else 0.0

    def frame(t: float) -> Grid:
        return effect.apply(grid, t) if effect is not None else grid

    animate_inline(frame, duration, fps=fps, terminal=term)
