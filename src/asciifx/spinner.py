"""Inline spinner for long-running work: ``with spinner("loading") as sp: ...``.

    with spinner("Fetching") as sp:
        for i, url in enumerate(urls, 1):
            sp.text = f"Fetching {i}/{len(urls)}"
            fetch(url)
            sp.write(f"got {url}")        # a permanent line above the spinner
    # the spinner line is left as:  ✔ Fetching 3/3 (2.4s)

    @spinner("Crunching")                 # also works as a decorator
    def crunch() -> None: ...

While it spins, a background thread redraws one line below the cursor in
place: ``<frame> <text>  <elapsed>s``. Frames are a function of time,
``frames[floor(t * rate) % len(frames)]``, never of how many were drawn.
When the block ends the line becomes ``✔ text (1.2s)``. If the block raises,
it becomes ``✖ text (1.2s)`` and the exception propagates; on Ctrl-C it
becomes ``✖ text (interrupted, 1.2s)`` and the KeyboardInterrupt propagates.

Do not print to stdout/stderr directly while it spins: the spinner line is
redrawn in place, so anything printed directly gets overwritten or tears the
line. Use ``sp.write()`` instead.

When output is not a terminal (pipe, file, CI log) nothing animates:
``text...`` is printed once at the start, ``write()`` lines as they come and
the final status line at the end, as plain text without escape sequences.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import TracebackType
from typing import Literal

from .core.grid import Color, Grid
from .term import Terminal

type SpinnerSpec = tuple[str, ...] | tuple[tuple[str, ...], float]
"""A style: its frames, or ``(frames, rate)`` with ``rate`` in frames per second."""

BLOOD_RED: Color = (225, 25, 35)
SUCCESS_COLOR: Color = (90, 200, 110)
FAIL_COLOR: Color = (235, 45, 45)
INTERRUPT_COLOR: Color = (235, 160, 40)
DIM: Color = (140, 140, 140)

DEFAULT_RATE = 10.0
"""Frames per second of styles given as a bare tuple of frames."""

SPINNERS: dict[str, SpinnerSpec] = {
    "dots": (("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"), 12.0),
    "line": (("|", "/", "-", "\\"), 6.0),
    "arc": (("◜", "◠", "◝", "◞", "◡", "◟"), 12.0),
    "bounce": (("⠁", "⠁", "⠂", "⠄", "⡀", "⠄", "⠂"), 12.0),  # a ball dropping, lingering at the top
    "pulse": (("⣀", "⣤", "⣶", "⣿", "⣶", "⣤"), 6.0),  # swells and brightens once a second
    "skull": (("☠",), 0.75),  # glows in and out
}
"""Built-in styles. All frames of a style are equally wide, in single-width characters.
Rates are chosen to divide the default 12 fps evenly, so no frame is shown longer than another."""

PULSING: frozenset[str] = frozenset({"pulse", "skull"})
"""Styles whose color pulses from dark to full ``color`` and back, once per cycle of their frames."""

PULSE_FLOOR = 0.3
"""Darkest point of a pulse, as a fraction of ``color``."""

THREAD_NAME = "asciifx-spinner"

type Status = Literal["success", "fail", "interrupted", "stopped"]

_FINALS: dict[str, tuple[str, Color, str]] = {  # status -> (symbol, its color, note before the time)
    "success": ("✔", SUCCESS_COLOR, ""),
    "fail": ("✖", FAIL_COLOR, ""),
    "interrupted": ("✖", INTERRUPT_COLOR, "interrupted, "),
}


def _resolve(spec: SpinnerSpec) -> tuple[tuple[str, ...], float]:
    """``(frames, rate)`` of a style given either as frames or as ``(frames, rate)``."""
    match spec:
        case (tuple() as frames, int() | float() as rate):
            pass
        case _:
            frames, rate = spec, DEFAULT_RATE
    if not frames or rate <= 0:
        raise ValueError(f"a spinner style needs frames and a positive rate, got {spec!r}")
    return tuple(frames), float(rate)


def _pulse(color: Color, phase: float) -> Color:
    """``color`` dimmed along a smooth cycle: darkest at whole ``phase`` values, full halfway."""
    k = PULSE_FLOOR + (1.0 - PULSE_FLOOR) * (0.5 - 0.5 * math.cos(2.0 * math.pi * phase))
    return (round(color[0] * k), round(color[1] * k), round(color[2] * k))


def _row(glyph: str, color: Color, text: str, suffix: str, width: int | None) -> Grid:
    """``glyph text<suffix>`` as one row. ``text`` is shortened with "..." to fit ``width``."""
    text = text.replace("\n", " ")
    head = f"{glyph} " if text else glyph
    if width is not None and len(head) + len(text) + len(suffix) > width:
        room = max(0, width - len(head) - len(suffix))
        text = text[: room - 3] + "..." if room > 3 else text[:room]
    total = len(head) + len(text) + len(suffix)
    grid = Grid(total if width is None else min(total, width), 1)
    grid.put_text(0, 0, glyph, color)
    grid.put_text(len(head), 0, text)
    grid.put_text(len(head) + len(text), 0, suffix, DIM)
    return grid


def _join(thread: threading.Thread | None) -> None:
    """Wait until ``thread`` is gone, even if a signal handler raises meanwhile
    (e.g. a second Ctrl-C). Such an exception is re-raised once the thread is gone."""
    pending: BaseException | None = None
    while thread is not None and thread.is_alive():
        try:
            thread.join()
        except BaseException as exc:
            pending = exc
    if pending is not None:
        raise pending


class Spinner:
    """One spinner line, usually made by ``spinner()``.

    ``text`` and ``write()`` may be used from any thread. ``start()``, the
    finishing methods (``succeed``, ``fail``, ``stop``) and leaving the
    ``with`` block belong to one thread, normally the main one, because
    entering and restoring the terminal installs and removes signal handlers.
    """

    def __init__(
        self,
        text: str = "",
        *,
        style: str = "dots",
        color: Color = BLOOD_RED,
        fps: float = 12.0,
        terminal: Terminal | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if style not in SPINNERS:
            raise ValueError(f"unknown spinner style {style!r}; choose from {', '.join(SPINNERS)}")
        if fps <= 0:
            raise ValueError(f"fps must be positive, got {fps}")
        self.frames, self.rate = _resolve(SPINNERS[style])
        self.style = style
        self.color = color
        self.fps = fps
        self.terminal = terminal if terminal is not None else Terminal(fullscreen=False)
        self._text = str(text)
        self._clock = clock
        self._t0 = 0.0
        self._state: Literal["new", "running", "finishing", "done"] = "new"
        self._lock = threading.Lock()  # held for every terminal call once started
        self._wake = threading.Event()  # redraw now: text changed, a line was written, or stopping
        self._stopping = False  # read by the thread under _lock before each frame
        self._thread: threading.Thread | None = None
        self._drawn = False  # the thread has drawn at least one frame
        self._error: BaseException | None = None  # what killed the thread, if anything

    # -- content (pure) ----------------------------------------------------

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._text = str(value)  # one reference assignment: atomic; the thread reads it once per frame
        self._wake.set()

    def line(self, t: float, width: int | None = None) -> Grid:
        """The spinner line ``t`` seconds after the start, cropped to ``width`` columns. No I/O."""
        n = len(self.frames)
        glyph = self.frames[math.floor(t * self.rate) % n]
        color = _pulse(self.color, t * self.rate / n) if self.style in PULSING else self.color
        return _row(glyph, color, self._text, f"  {t:.1f}s", width)

    def _final_line(self, status: str, label: str, elapsed: float, width: int | None) -> Grid:
        symbol, color, note = _FINALS[status]
        return _row(symbol, color, label, f" ({note}{elapsed:.1f}s)", width)

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> Spinner:
        """Show the spinner; on a terminal, start the thread that draws it. Returns ``self``."""
        if self._state != "new":
            raise RuntimeError("a Spinner can only be started once")
        term = self.terminal
        self._t0 = self._clock()
        self._state = "running"
        if not term.is_tty:
            if self._text:
                term.render(Grid.from_text(f"{self._text}..."))
            return self
        term.__enter__()
        try:
            self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
            self._thread.start()
        except BaseException:
            self._finish("stopped", None)
            raise
        return self

    def succeed(self, msg: str | None = None) -> None:
        """Stop and leave ``✔ msg (1.2s)`` (``msg`` defaults to the current text)."""
        self._finish("success", msg)

    def fail(self, msg: str | None = None) -> None:
        """Stop and leave ``✖ msg (1.2s)`` (``msg`` defaults to the current text)."""
        self._finish("fail", msg)

    def stop(self) -> None:
        """Stop and erase the spinner line, leaving no status."""
        self._finish("stopped", None)

    def __enter__(self) -> Spinner:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._finish("success", None)
        elif issubclass(exc_type, KeyboardInterrupt):
            self._finish("interrupted", None, raise_thread_error=False)
        else:
            self._finish("fail", None, raise_thread_error=False)

    # -- output --------------------------------------------------------------

    def write(self, text: object = "") -> None:
        """Print ``text`` as permanent line(s) above the spinner, like ``print(text)``.

        Thread-safe. While the spinner is shown on a terminal, long lines wrap
        (``Terminal.print_above``); before ``start()`` and once finished, they
        are cut at the terminal width.
        """
        lines = str(text).split("\n")
        term = self.terminal
        with self._lock:
            if not term.is_tty:
                term.render(Grid.from_text("\n".join(lines)))
                return
            if self._state in ("new", "done"):
                self._print_lines(lines)
                return
            # running or finishing: the session is active until _finish restores it
            # (under _lock), and the thread draws no frame while we hold _lock
            term.print_above("\n".join(lines))
        self._wake.set()  # redraw the spinner below the new lines right away

    def _print_lines(self, lines: list[str]) -> None:
        """Print ``lines`` while no session is active: each one is drawn as a
        one-row inline session, which leaves it on screen with the cursor below."""
        term = self.terminal
        width = term.size()[0]
        for line in lines:
            term.__enter__()
            term.render(Grid.from_text(line[:width]))
            term.restore()

    # -- drawing thread ------------------------------------------------------

    def _run(self) -> None:
        period = 1.0 / self.fps
        term = self.terminal
        try:
            while True:
                self._wake.clear()
                with self._lock:
                    if self._stopping:
                        return
                    term.render(self.line(self._clock() - self._t0, term.size()[0]))
                    self._drawn = True
                # Sleep until the next frame boundary (or a wake-up): what is drawn depends on the time.
                self._wake.wait(period - (self._clock() - self._t0) % period)
        except BaseException as exc:  # no traceback through the spinner line; _finish re-raises it
            self._error = exc

    def _finish(self, status: Status, msg: str | None, *, raise_thread_error: bool = True) -> None:
        with self._lock:
            if self._state != "running":
                return
            self._state = "finishing"
            self._stopping = True  # the thread checks this under _lock: it draws no frame after this
        elapsed = self._clock() - self._t0
        label = self._text if msg is None else str(msg)
        self._wake.set()
        try:
            _join(self._thread)  # the final frame and restore() only ever run once the thread is gone
        finally:
            with self._lock:
                try:
                    self._draw_final(status, label, elapsed)
                finally:
                    self._state = "done"  # first: restore() may raise a SIGINT deferred meanwhile
                    self.terminal.restore()
        if raise_thread_error and self._error is not None:
            raise RuntimeError("the spinner thread failed") from self._error

    def _draw_final(self, status: Status, label: str, elapsed: float) -> None:
        term = self.terminal
        width = term.size()[0] if term.is_tty else None
        if status != "stopped":
            term.render(self._final_line(status, label, elapsed, width))
        elif self._drawn:
            term.render(Grid(0, 1))  # an empty row: erases the spinner line


@contextmanager
def spinner(
    text: str = "",
    *,
    style: str = "dots",
    color: Color = BLOOD_RED,
    fps: float = 12.0,
    terminal: Terminal | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[Spinner]:
    """Show an inline spinner while the block (or decorated function) runs.

    ``style``: a name from ``SPINNERS``. ``color``: the frame color (blood red
    by default). ``fps``: redraws per second. ``terminal``: an inline
    ``Terminal`` that is not entered yet (default: ``Terminal(fullscreen=False)``).
    ``clock``: time source, for tests. See the module docs for what is shown.
    """
    with Spinner(text, style=style, color=color, fps=fps, terminal=terminal, clock=clock) as sp:
        yield sp


def demo(
    style: str = "dots",
    seconds: float = 3.0,
    text: str = "summoning",
    *,
    terminal: Terminal | None = None,
) -> None:
    """``asciifx spin``: spin for ``seconds`` (any key ends it early), then leave ``✔ text``."""
    term = terminal if terminal is not None else Terminal(fullscreen=False)
    with spinner(text, style=style, terminal=term):
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            if term.read_keys(left):
                break


__all__ = ["BLOOD_RED", "PULSING", "SPINNERS", "Spinner", "SpinnerSpec", "demo", "spinner"]
