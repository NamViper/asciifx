"""Terminal session behavior, tested against a real pseudo-terminal."""

import fcntl
import io
import os
import signal
import struct
import termios
import time

import pytest
from vt import VT, Recorder

from asciifx.core import Cell, Grid
from asciifx.sources import Bounce
from asciifx.term import ColorMode, Terminal
from asciifx.term.ansi import ENTER, ENTER_INLINE, ERASE_LINE, ERASE_SCREEN, LEAVE, encode_frame, leave_inline

TC = ColorMode.TRUECOLOR
RED, BLUE = (255, 0, 0), (0, 0, 255)


def assert_restored(fake_tty, lflag_before):
    out = fake_tty.output()
    assert out.startswith(ENTER)
    assert out.endswith(LEAVE)
    assert fake_tty.lflag() == lflag_before


def test_non_tty_prints_plain_text_once_and_never_escapes():
    buf = io.StringIO()
    with Terminal(out=buf) as term:
        assert not term.is_tty
        term.render(Grid.from_text("hi  \nyo", fg=(255, 0, 0)))
    assert buf.getvalue() == "hi\nyo\n"


def test_enter_and_exit_on_tty(fake_tty):
    before = fake_tty.lflag()
    with Terminal(fake_tty.out, fake_tty.inp) as term:
        assert term.is_tty
        assert term.size() == (30, 8)
        lflag = fake_tty.lflag()
        assert not lflag & termios.ICANON and not lflag & termios.ECHO  # cbreak
        assert lflag & termios.ISIG  # Ctrl-C still raises SIGINT
    assert_restored(fake_tty, before)


def test_render_writes_one_encoded_frame(fake_tty):
    grid = Grid.filled(3, 2, Cell("#", (1, 2, 3)))
    with Terminal(fake_tty.out, fake_tty.inp, color_mode=ColorMode.TRUECOLOR) as term:
        term.render(grid)
    assert ENTER + encode_frame(grid, ColorMode.TRUECOLOR) + LEAVE == fake_tty.output()


def test_restores_after_exception(fake_tty):
    before = fake_tty.lflag()
    with pytest.raises(RuntimeError):
        with Terminal(fake_tty.out, fake_tty.inp):
            raise RuntimeError("boom")
    assert_restored(fake_tty, before)


@pytest.mark.parametrize("sig,exc,code", [(signal.SIGTERM, SystemExit, 143), (signal.SIGINT, KeyboardInterrupt, None)])
def test_restores_after_signal(fake_tty, sig, exc, code):
    before = fake_tty.lflag()
    handler_before = signal.getsignal(sig)
    with pytest.raises(exc) as info:
        with Terminal(fake_tty.out, fake_tty.inp):
            os.kill(os.getpid(), sig)
            time.sleep(1)  # the handler raises before this finishes
    if code is not None:
        assert info.value.code == code
    assert signal.getsignal(sig) is handler_before
    assert_restored(fake_tty, before)


def test_signal_during_restore_is_deferred_until_restore_finishes(fake_tty):
    """A SIGTERM arriving mid-cleanup must not abort the cleanup."""

    class SignalOnLeave:
        def __init__(self, inner):
            self.inner = inner

        def write(self, s):
            self.inner.write(s)
            if s == LEAVE:
                os.kill(os.getpid(), signal.SIGTERM)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    before = fake_tty.lflag()
    with pytest.raises(SystemExit):
        with Terminal(SignalOnLeave(fake_tty.out), fake_tty.inp):
            pass
    assert_restored(fake_tty, before)


def test_read_keys_returns_early_on_input(fake_tty):
    with Terminal(fake_tty.out, fake_tty.inp) as term:
        assert term.read_keys(0) == []
        fake_tty.send(b"q\x1b[A")
        start = time.monotonic()
        assert term.read_keys(5) == ["q", "up"]
        assert time.monotonic() - start < 1


def test_restore_is_idempotent(fake_tty):
    term = Terminal(fake_tty.out, fake_tty.inp)
    with term:
        term.restore()
    assert fake_tty.output().count(LEAVE) == 1


def test_size_falls_back_when_pty_reports_zero(fake_tty, monkeypatch):
    fcntl.ioctl(fake_tty.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    w, h = Terminal(fake_tty.out, fake_tty.inp).size()
    assert w > 0 and h > 0


class SignalsOnLeave:
    """Output proxy that sends signals to us while restore() writes LEAVE."""

    def __init__(self, inner, *sigs):
        self.inner = inner
        self.sigs = sigs

    def write(self, s):
        self.inner.write(s)
        if s == LEAVE:
            for sig in self.sigs:
                os.kill(os.getpid(), sig)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_resize_during_restore_keeps_deferred_signal_logic(fake_tty):
    before = fake_tty.lflag()
    winch_before = signal.getsignal(signal.SIGWINCH)
    with pytest.raises(SystemExit):
        with Terminal(SignalsOnLeave(fake_tty.out, signal.SIGWINCH, signal.SIGTERM, signal.SIGWINCH), fake_tty.inp):
            pass
    assert_restored(fake_tty, before)
    assert signal.getsignal(signal.SIGWINCH) is winch_before


# -- diff rendering ------------------------------------------------------------


def test_identical_render_writes_nothing(fake_tty):
    out = Recorder(fake_tty.out)
    grid = Grid.from_text("hello", fg=RED)
    with Terminal(out, fake_tty.inp, color_mode=TC) as term:
        term.render(grid)
        n = len(out.writes)
        term.render(grid)
        term.render(grid.copy())
        assert len(out.writes) == n
        grid.set(0, 0, Cell("J", BLUE))  # mutated in place: the snapshot must be a copy
        term.render(grid)
        assert out.writes[n:] == [encode_frame(grid, TC, Grid.from_text("hello", fg=RED))]
    vt = VT(30, 8).feed(fake_tty.output())
    assert vt.grid(0, 0, 5, 1) == grid


def test_sigwinch_updates_size_and_forces_full_redraw(fake_tty):
    out = Recorder(fake_tty.out)
    handler_before = signal.getsignal(signal.SIGWINCH)
    grid = Grid.from_text("hi")
    with Terminal(out, fake_tty.inp, color_mode=TC) as term:
        assert signal.getsignal(signal.SIGWINCH) is not handler_before
        term.render(grid)
        fake_tty.resize(40, 10)
        os.kill(os.getpid(), signal.SIGWINCH)
        assert term.size() == (40, 10)
        n = len(out.writes)
        term.render(grid)  # unchanged grid, but the terminal may have garbled the screen
        assert out.writes[n:] == [encode_frame(grid, TC)]
        term.render(grid)
        assert len(out.writes) == n + 1  # flag consumed: back to diffs
    assert signal.getsignal(signal.SIGWINCH) is handler_before


def test_invalidate_forces_full_redraw(fake_tty):
    out = Recorder(fake_tty.out)
    grid = Grid.from_text("hi")
    with Terminal(out, fake_tty.inp, color_mode=TC) as term:
        term.render(grid)
        term.invalidate()
        n = len(out.writes)
        term.render(grid)
        assert out.writes[n:] == [encode_frame(grid, TC)]


def test_failed_write_forces_full_redraw_next_time(fake_tty):
    class FailOnce(Recorder):
        fail = False

        def write(self, s):
            if self.fail:
                self.fail = False
                raise KeyboardInterrupt
            return super().write(s)

    out = FailOnce(fake_tty.out)
    a, b = Grid.from_text("ab"), Grid.from_text("xy")
    with Terminal(out, fake_tty.inp, color_mode=TC) as term:
        term.render(a)
        out.fail = True
        with pytest.raises(KeyboardInterrupt):
            term.render(b)
        n = len(out.writes)
        term.render(b)
        assert out.writes[n:] == [encode_frame(b, TC)]


def test_diff_rendering_sends_a_fraction_of_a_full_frame(fake_tty):
    fake_tty.resize(80, 24)
    out = Recorder(fake_tty.out)
    source = Bounce()
    with Terminal(out, fake_tty.inp, color_mode=TC) as term:
        n = len(out.writes)
        for i in range(61):  # two seconds at 30 FPS
            term.render(source.frame(i / 30, *term.size()))
        frames = [len(w.encode()) for w in out.writes[n:]]
    assert len(frames) > 30  # some frames don't change at all and write nothing
    assert sum(frames[1:]) / 60 < 0.3 * frames[0]
    assert VT(80, 24).feed(fake_tty.output()).grid() == source.frame(2.0, 80, 24)


# -- inline mode -----------------------------------------------------------------

BANNER = [
    Grid.from_text(">_ boot\n[    ]", fg=RED),
    Grid.from_text(">_ booting\n[##  ]", fg=RED, bg=BLUE),
    Grid.from_text(">_ booted.\n[####] ok", fg=BLUE),
]


def run_inline(fake_tty, frames, **kw):
    with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC, fullscreen=False, **kw) as term:
        for grid in frames:
            term.render(grid)
    return fake_tty.output()


@pytest.mark.parametrize("lines_before", [2, 7, 12])  # 7 and 12: reserving the region scrolls the screen
def test_inline_mode_end_to_end(fake_tty, lines_before):
    before = fake_tty.lflag()
    vt = VT(30, 8)
    vt.feed("".join(f"$ line {i}\r\n" for i in range(lines_before)))  # what the shell printed so far
    start_row = vt.y
    above = [list(line) for line in vt.lines]

    out = run_inline(fake_tty, BANNER)
    assert out.startswith(ENTER_INLINE) and out.endswith(leave_inline(2))
    assert "\x1b[?1049" not in out and ERASE_SCREEN not in out  # no alternate screen, no clear
    vt.feed(out)

    top = min(start_row, 8 - 1 - 2)
    scroll = start_row - top
    assert vt.scrolled - (lines_before - start_row) == scroll
    assert vt.lines[:top] == above[scroll : scroll + top]  # text above the region untouched
    last = BANNER[-1]
    assert vt.grid(0, top, last.width, 2) == last  # the final frame stays on screen
    assert vt.grid(last.width, top, 30 - last.width, 2) == Grid(30 - last.width, 2)
    assert vt.cursor == (0, top + 2)  # the prompt prints on the line below the region
    assert vt.modes[25] and vt.modes[7]  # cursor shown, autowrap back on
    assert fake_tty.lflag() == before


def test_inline_region_is_fixed_by_first_frame_and_capped_to_screen(fake_tty):
    tall = Grid(10, 12, [Cell(str(i % 10)) for i in range(120)])  # taller than the 8-row screen
    short = Grid.from_text("x")
    out = Recorder(fake_tty.out)
    with Terminal(out, fake_tty.inp, color_mode=TC, fullscreen=False) as term:
        term.render(tall)
        first = "".join(out.writes)
        term.render(short)

    vt = VT(30, 8).feed(first)
    assert vt.grid(0, 0, 10, 7) == Grid(10, 7, tall.cells[:70])  # 7 rows reserved, the rest clipped
    vt = VT(30, 8).feed(fake_tty.output())
    expected = Grid(10, 7)
    expected.set(0, 0, Cell("x"))
    assert vt.grid(0, 0, 10, 7) == expected  # `short` padded with blank rows to the region height
    assert vt.cursor == (0, 7)


def test_inline_without_render_only_restores_modes(fake_tty):
    out = run_inline(fake_tty, [])
    assert out == ENTER_INLINE + leave_inline(None)


def test_inline_resize_redraws_region_without_clearing_screen(fake_tty):
    out = Recorder(fake_tty.out)
    grid = Grid.from_text("abc\ndef")
    with Terminal(out, fake_tty.inp, color_mode=TC, fullscreen=False) as term:
        term.render(grid)
        os.kill(os.getpid(), signal.SIGWINCH)
        n = len(out.writes)
        term.render(grid)
        assert out.writes[n:] == [encode_frame(grid, TC, inline=True)]
        assert out.writes[n].count(ERASE_LINE) == 2 and ERASE_SCREEN not in out.writes[n]


def test_inline_on_non_tty_prints_plain_text():
    buf = io.StringIO()
    with Terminal(out=buf, fullscreen=False) as term:
        term.render(Grid.from_text("hi  \nyo", fg=(255, 0, 0)))
    assert buf.getvalue() == "hi\nyo\n"
