"""Terminal session: the only place that talks to the real terminal.

On a TTY, ``with Terminal() as term:`` switches to the alternate screen, hides
the cursor, disables autowrap and puts stdin in cbreak mode. Everything is
restored on exit — normal return, exception, SIGINT or SIGTERM.

``render`` only sends the cells that changed since the previous frame. A
terminal resize (SIGWINCH) or ``invalidate()`` makes the next frame a full
redraw.

With ``fullscreen=False`` (inline mode) the alternate screen is not used: the
first frame reserves ``grid.height`` lines starting at the cursor's line
(assumed to be at column 0) and every frame is drawn there. On exit the cursor
is left on the line just below the region, so the last frame stays on screen
and in the scrollback, and the shell prompt prints underneath it.
``print_above`` prints permanent lines where the region is and moves the
region down below them (a spinner's log output).

Ctrl-Z (SIGTSTP) suspends cleanly: the terminal is restored exactly as on exit,
the process stops, and once continued (``fg``) the session is re-entered. See
``Terminal`` for the details, and for what may be called from other threads.

On a non-TTY (pipe, file), entering is a no-op and ``render`` prints plain text
without any escape sequences.
"""

from __future__ import annotations

import os
import signal
import sys
import termios
import threading
import time
import tty
from collections.abc import Mapping
from types import FrameType, TracebackType
from typing import IO, Any

from ..core.grid import BLANK, Grid
from .ansi import (
    AUTOWRAP_ON,
    ENTER,
    ENTER_INLINE,
    LEAVE,
    encode_frame,
    leave_inline,
    printable_cells,
    reserve_region,
)
from .ansi import print_above as encode_print_above
from .color import ColorMode, detect_color_mode
from .keys import KeyReader

_SIGNALS = (signal.SIGINT, signal.SIGTERM)
_FALLBACK_SIZE = (80, 24)
# C0 and C1 controls (same set as Grid's), except "\n", which print_above splits on.
_CONTROLS_TO_SPACES = {c: " " for c in [*range(0x20), *range(0x7F, 0xA0)] if c != 0x0A}


def _isatty(stream: IO[Any]) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _env_int(name: str) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return 0


def _fallback_size(fallback: tuple[int, int]) -> tuple[int, int]:
    """Same as ``shutil.get_terminal_size(fallback)``, minus the cost of importing shutil.

    shutil pulls in bz2, lzma, zstd and fnmatch: a few ms on every shell
    startup. Each dimension: $COLUMNS / $LINES if a positive integer, else the
    size of the terminal on ``sys.__stdout__``, else ``fallback``.
    """
    cols, rows = _env_int("COLUMNS"), _env_int("LINES")
    if cols <= 0 or rows <= 0:
        try:
            size = os.get_terminal_size(sys.__stdout__.fileno())  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):  # no stdout, closed, not a terminal
            size = os.terminal_size(fallback)
        if cols <= 0:
            cols = size.columns or fallback[0]
        if rows <= 0:
            rows = size.lines or fallback[1]
    return cols, rows


def _fit_rows(grid: Grid, rows: int) -> Grid:
    """``grid`` cropped or padded with blank rows to exactly ``rows`` rows."""
    if grid.height == rows:
        return grid
    w = grid.width
    cells = grid.cells[: w * rows]
    cells.extend([BLANK] * (w * rows - len(cells)))
    return Grid(w, rows, cells)


def _crop(grid: Grid, width: int, height: int) -> Grid:
    """The top-left part of ``grid`` that fits in ``width`` x ``height`` (``grid`` itself if it all fits)."""
    if grid.width <= width and grid.height <= height:
        return grid
    w, h, gw = min(grid.width, width), min(grid.height, height), grid.width
    return Grid(w, h, [c for y in range(h) for c in grid.cells[y * gw : y * gw + w]])


def _set_cbreak(fd: int) -> None:
    """cbreak (no echo, no line buffering; Ctrl-C/Ctrl-Z still signal), and no XON/XOFF.

    With IXON on (the default), Ctrl-S stops all output: the next frame's write
    blocks, so no key is read any more and the animation looks hung until Ctrl-Q.
    Off, Ctrl-S is a key like any other (and Linux resumes stopped output).
    """
    tty.setcbreak(fd)
    mode = termios.tcgetattr(fd)
    mode[0] &= ~termios.IXON
    termios.tcsetattr(fd, termios.TCSANOW, mode)


def _raise_for(signum: int) -> None:
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + signum)


def _in_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


class Terminal:
    """A terminal session; use it as a context manager (see the module docstring).

    **Ctrl-Z.** While a session entered from the main thread is active,
    SIGTSTP first restores the terminal exactly as on exit (fullscreen: leave
    the alternate screen, show the cursor; inline: move below the region, show
    the cursor, autowrap on; both: termios back to the saved modes), then stops
    the process for real (SIGTSTP with its default action). Once continued
    (``fg``, SIGCONT), cbreak is set again, and:

    * fullscreen: the alternate screen is re-entered (cleared, cursor hidden)
      and the next ``render`` is a full redraw.
    * inline, best effort: the shell has printed its "Stopped"/"continued"
      lines below the old region meanwhile, so that region is abandoned (it
      keeps the frame shown when stopped, in the scrollback) and a fresh one of
      the same height (at most screen rows - 1) is reserved at the cursor,
      assumed at column 0 as after any shell output. The last frame is redrawn
      there at once and later frames go there.

    The handler is installed only if SIGTSTP had its default disposition, as
    curses does: a parent that ignores it, or the host program's own handler,
    stays in charge. SIGSTOP can't be caught; after ``kill -STOP`` the screen
    and modes stay as they were.

    **Threads.** A background thread (e.g. a spinner) may ``render`` while the
    main thread runs other code:

    * ``__enter__``, ``render``, ``print_above``, ``restore``/``__exit__``
      and the Ctrl-Z suspend/resume hold one lock, so a frame is always
      written whole, never interleaved with another frame or with the
      leave/enter sequences, and the diff state (what is on screen) stays
      consistent.
    * After ``restore``, ``render`` and ``print_above`` on a TTY are no-ops
      until the session is entered again: a frame rendered by a thread after
      the main thread has left the ``with`` block (e.g. on KeyboardInterrupt)
      writes nothing.
    * ``invalidate`` only sets a flag: safe from any thread, it takes effect
      at the next ``render``.
    * ``size`` and ``read_keys`` take no lock; read keys from one thread only.
    * Enter and exit the session in the main thread: only it can install
      signal handlers (entered elsewhere, signals keep their usual behavior).
      Handlers run in the main thread. One that must wait for a frame another
      thread is writing waits for that single write. If the main thread is
      itself in ``render``/``restore`` when Ctrl-Z arrives, the stop happens
      right after; SIGINT/SIGTERM arriving during ``restore`` or during a
      suspend/resume are raised once that is done. SIGINT/SIGTERM during a
      main-thread ``render`` abort that frame, and the leave sequence starts
      with ESC, which cancels a half-written escape sequence.
    """

    def __init__(
        self,
        out: IO[str] | None = None,
        inp: IO[Any] | None = None,
        *,
        color_mode: ColorMode | None = None,
        env: Mapping[str, str] | None = None,
        fullscreen: bool = True,
    ) -> None:
        self.out = out if out is not None else sys.stdout
        self.inp = inp if inp is not None else sys.stdin
        self.is_tty = _isatty(self.out)
        self.color_mode = color_mode or detect_color_mode(os.environ if env is None else env)
        self.fullscreen = fullscreen
        self._prev: Grid | None = None  # what is on screen now; None = unknown
        self._redraw = False  # set by SIGWINCH / invalidate(), consumed by render
        self._region: int | None = None  # inline mode: rows reserved by the first render
        self._keys: KeyReader | None = None
        self._saved_tty: list[Any] | None = None
        self._saved_handlers: dict[int, Any] = {}
        self._active = False
        self._closed = False  # restored: render is a no-op until the next __enter__
        self._lock = threading.RLock()  # see "Threads" above
        self._restoring = False  # restore or suspend/resume in progress: signals are deferred
        self._deferred_signal: int | None = None
        self._pending_stop = False  # Ctrl-Z waiting for the main thread to finish a frame/restore

    # -- session ---------------------------------------------------------

    def __enter__(self) -> Terminal:
        if not self.is_tty:
            return self
        try:
            with self._lock:
                if self._active:
                    return self
                self._active = True
                self._closed = False
                self._prev = None
                self._redraw = False
                self._region = None
                self._pending_stop = False
                self._install_signal_handlers()
                if _isatty(self.inp):
                    fd = self.inp.fileno()
                    self._saved_tty = termios.tcgetattr(fd)
                    _set_cbreak(fd)  # no echo, no line buffering, no Ctrl-S; Ctrl-C/Ctrl-Z still signal
                    self._keys = KeyReader(fd)
                self.out.write(ENTER if self.fullscreen else ENTER_INLINE)
                self.out.flush()
        except BaseException:
            self.restore()
            raise
        if self._pending_stop:
            self._run_pending_stop()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.restore()

    def restore(self) -> None:
        """Undo everything ``__enter__`` did. Idempotent; safe while another thread renders."""
        if not self._active:
            return
        outer = not self._restoring
        self._restoring = True  # before waiting for the lock: a signal from now on is deferred
        try:
            with self._lock:  # a frame being written by another thread is finished first
                if self._active:
                    try:
                        try:
                            self._leave()
                        finally:
                            self._restore_signal_handlers()
                    finally:
                        self._saved_tty = None
                        self._keys = None
                        self._prev = None
                        self._region = None
                        self._active = False
                        self._closed = True
        finally:
            if outer:
                self._restoring = False
        if not outer:
            return
        if self._pending_stop:  # Ctrl-Z during the restore: the session is over, so just stop
            self._pending_stop = False
            self._suspend_process()  # SIGTSTP is back to its default action here
        if (signum := self._deferred_signal) is not None:
            self._deferred_signal = None
            _raise_for(signum)

    def _leave(self) -> None:
        """Screen modes back as before ``__enter__``, then termios (lock held)."""
        try:
            self.out.write(LEAVE if self.fullscreen else leave_inline(self._region))
            self.out.flush()
        finally:
            if self._saved_tty is not None:
                termios.tcsetattr(self.inp.fileno(), termios.TCSADRAIN, self._saved_tty)

    # -- Ctrl-Z ----------------------------------------------------------

    def _suspend_process(self) -> None:
        """Stop this process the way an unhandled Ctrl-Z would; returns once continued.

        Called with SIGTSTP set to its default action. Tests replace it (on the
        instance) to simulate a stop without stopping the test runner.
        """
        os.kill(os.getpid(), signal.SIGTSTP)

    def _suspend(self) -> None:
        """Ctrl-Z: leave the terminal as on exit, stop, and re-enter once continued.

        Main thread only, not while it holds the lock (see ``_run_pending_stop``).
        """
        self._pending_stop = False
        self._restoring = True  # SIGINT/SIGTERM/SIGTSTP meanwhile are deferred
        try:
            with self._lock:  # waits for a frame another thread is writing; blocks renders until re-entered
                if not self._active:  # restored meanwhile (by another thread): just stop
                    self._stop_process()
                else:
                    try:
                        self._leave()
                    finally:
                        try:
                            self._stop_process()
                        finally:
                            self._reenter()
        finally:
            self._restoring = False
        if (signum := self._deferred_signal) is not None:  # e.g. `kill -INT` while stopped
            self._deferred_signal = None
            _raise_for(signum)

    def _stop_process(self) -> None:
        handler = signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        try:
            self._suspend_process()
        finally:
            signal.signal(signal.SIGTSTP, handler)

    def _reenter(self) -> None:
        """After a stop: cbreak again, then the session's screen modes (lock held)."""
        if self._saved_tty is not None:
            # Before any output: if we were continued in the background, this stops
            # us again (SIGTTOU) until `fg`, instead of drawing over the shell.
            _set_cbreak(self.inp.fileno())
        last, self._prev = self._prev, None  # unknown until the write below completes
        if self.fullscreen:
            data, last = ENTER, None  # next render: full redraw (the screen size may have changed)
        else:
            data = ENTER_INLINE
            if self._region is not None:
                rows = min(self._region, max(0, self.size()[1] - 1))
                self._region = rows
                data += reserve_region(rows)
                if last is not None:
                    last = _fit_rows(last, rows)
                    data += encode_frame(last, self.color_mode, None, inline=True)
        self.out.write(data)
        self.out.flush()
        self._prev = last

    def _run_pending_stop(self) -> None:
        """Handle a deferred Ctrl-Z, unless the main thread is still busy with the terminal."""
        while (
            self._pending_stop
            and not self._restoring
            and _in_main_thread()
            and not self._lock._is_owned()  # RLock: held by this thread (mid-frame)?
        ):
            self._suspend()

    def _on_stop(self, signum: int, frame: FrameType | None) -> None:
        # Runs in the main thread, between two bytecodes of whatever it was doing:
        # if that was writing a frame or restoring, the stop waits until it's done.
        self._pending_stop = True
        self._run_pending_stop()

    # -- signals ---------------------------------------------------------

    def _on_signal(self, signum: int, frame: FrameType | None) -> None:
        if self._restoring:
            self._deferred_signal = signum
            return
        _raise_for(signum)  # unwinds the stack, so the caller's finally/with restores

    def _on_resize(self, signum: int, frame: FrameType | None) -> None:
        # Only a flag: render() may be running right now (the handler runs between
        # its bytecodes), and resetting _prev here could be undone by it.
        self._redraw = True

    def _install_signal_handlers(self) -> None:
        if not _in_main_thread():
            return  # signal handlers can only be installed from the main thread
        for sig in _SIGNALS:
            self._saved_handlers[sig] = signal.signal(sig, self._on_signal)
        self._saved_handlers[signal.SIGWINCH] = signal.signal(signal.SIGWINCH, self._on_resize)
        if signal.getsignal(signal.SIGTSTP) == signal.SIG_DFL:  # leave SIG_IGN / custom handlers alone
            self._saved_handlers[signal.SIGTSTP] = signal.signal(signal.SIGTSTP, self._on_stop)

    def _restore_signal_handlers(self) -> None:
        while self._saved_handlers:
            sig, handler = self._saved_handlers.popitem()
            signal.signal(sig, handler if handler is not None else signal.SIG_DFL)

    # -- output / input --------------------------------------------------

    def size(self) -> tuple[int, int]:
        """(columns, rows) of the terminal; 80x24 (or $COLUMNS/$LINES) when unknown."""
        if self.is_tty:
            try:
                cols, rows = os.get_terminal_size(self.out.fileno())
                if cols > 0 and rows > 0:  # some ptys report 0x0 when no size was ever set
                    return cols, rows
            except (OSError, ValueError):
                pass
        return _fallback_size(_FALLBACK_SIZE)

    def render(self, grid: Grid) -> None:
        """Draw a frame with at most one write. Non-TTY: plain text, no escapes.

        On a TTY only the cells that changed since the last frame are sent
        (nothing at all if none did). Fullscreen draws at the top-left; inline
        draws into the region reserved by the first render, whose height is
        fixed then (at most screen rows - 1): later frames are cropped or
        padded with blank rows to that height. Anything beyond the screen's
        right (fullscreen: or bottom) edge is cut off. After ``restore`` this
        is a no-op on a TTY. Thread-safe, see the class docstring.
        """
        if not self.is_tty:
            # no escape sequences when piped, not even ones smuggled in through a cell's glyph
            text = Grid(grid.width, grid.height, printable_cells(grid.cells)).to_text() + "\n"
            with self._lock:
                self.out.write(text)
                self.out.flush()
            return
        with self._lock:
            if not self._closed:  # once restored, drawing would scribble over the shell
                self._draw(grid)
        if self._pending_stop:  # Ctrl-Z arrived while this (main) thread was drawing
            self._run_pending_stop()

    def _draw(self, grid: Grid) -> None:
        if self._redraw:
            self._redraw = False  # consumed before drawing: a resize during this render isn't lost
            self._prev = None
        prefix = ""
        cols, rows = self.size()
        if not self.fullscreen:
            if self._region is None:
                self._region = max(0, min(grid.height, rows - 1))  # keep the line below on screen
                prefix = reserve_region(self._region)
            grid = _fit_rows(grid, self._region)
        # Autowrap is off: whatever lies beyond the screen's edge would pile up in its
        # last column/row and show the wrong cells there. Cut it off instead.
        grid = _crop(grid, cols, grid.height if not self.fullscreen else rows)
        frame = encode_frame(grid, self.color_mode, self._prev, inline=not self.fullscreen)
        if not frame:
            return
        self._prev = None  # if the write fails halfway, the screen content is unknown
        self.out.write(prefix + frame)
        self.out.flush()
        self._prev = grid.copy()  # cells are immutable: a shallow copy is a snapshot

    def print_above(self, text: str) -> None:
        """Inline mode: print ``text`` for good above the region, which moves down below it.

        Like ``print`` for programs that keep an inline animation (a spinner)
        at the bottom: the lines go where the region was, scroll into the
        scrollback like normal output (long ones wrap), and the region is
        reserved again just below them with its current frame redrawn, all in
        one write. Each ``\\n`` (or ``\\r\\n``) starts a new line; other control
        characters (tabs, ESC, ...) become spaces. Safe from any thread, like
        ``render``; a no-op after ``restore``. Non-TTY: the plain lines.
        Fullscreen on a TTY raises ``RuntimeError``: there is no "above" there.
        """
        lines = text.replace("\r\n", "\n").translate(_CONTROLS_TO_SPACES).split("\n")
        if not self.is_tty:
            with self._lock:
                self.out.write("\n".join(lines) + "\n")
                self.out.flush()
            return
        if self.fullscreen:
            raise RuntimeError("print_above() needs an inline Terminal (fullscreen=False)")
        with self._lock:
            if self._closed:
                return
            rows = self._region
            if rows is not None:  # the screen may have shrunk: keep the line below on screen
                rows = self._region = min(rows, max(0, self.size()[1] - 1))
            data = encode_print_above(rows, lines)
            last, self._prev = self._prev, None  # unknown until the write below completes
            if last is not None and rows is not None:
                last = _fit_rows(last, rows)
                data += encode_frame(last, self.color_mode, None, inline=True)
            if not self._active:  # never entered: no restore() will turn autowrap back on
                data += AUTOWRAP_ON
            self.out.write(data)
            self.out.flush()
            self._prev = last
        if self._pending_stop:
            self._run_pending_stop()

    def invalidate(self) -> None:
        """Forget what is on screen: the next ``render`` is a full redraw. Safe from any thread."""
        self._redraw = True

    def read_keys(self, timeout: float) -> list[str]:
        """Wait up to ``timeout`` seconds for key presses (returns early on input)."""
        if self._keys is None:
            time.sleep(max(0.0, timeout))
            return []
        return self._keys.poll(timeout)
