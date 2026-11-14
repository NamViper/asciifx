"""Rendering from a background thread (as spinner() does) while the main thread
restores the terminal, invalidates or gets Ctrl-Z."""

import signal
import threading
import time

import pytest
from vt import VT, Recorder

from asciifx.core import Cell, Grid
from asciifx.term import ColorMode, Terminal
from asciifx.term.ansi import ENTER, ENTER_INLINE, LEAVE, encode_frame, leave_inline

TC = ColorMode.TRUECOLOR


class SlowOut:
    """Output proxy that writes in small chunks and lets other threads run in
    between, so unserialized writes would interleave. Counts overlapping writes."""

    def __init__(self, inner, chunk=32):
        self.inner = inner
        self.chunk = chunk
        self.writes: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def write(self, s):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            for i in range(0, len(s), self.chunk):
                self.inner.write(s[i : i + self.chunk])
                self.inner.flush()
                time.sleep(0)  # releases the GIL
        finally:
            self.in_flight -= 1
        self.writes.append(s)
        return len(s)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def frames(width, height):
    """Two full-screen frames that differ in every cell, with colors: long writes."""
    def frame(k):
        return Grid(width, height, [
            Cell("#*"[(i + k) % 2], ((i * 7 + k * 50) % 256, 40, 200), (k * 90, (i * 3) % 256, 10))
            for i in range(width * height)
        ])
    return frame(0), frame(1)


class Renderer(threading.Thread):
    """Renders alternating frames as fast as it can until stopped."""

    def __init__(self, term, grids):
        super().__init__(daemon=True)
        self.term, self.grids = term, grids
        self.count = 0
        self.stop = threading.Event()
        self.errors: list[BaseException] = []

    def run(self):
        try:
            while not self.stop.is_set():
                self.term.render(self.grids[self.count % 2])
                self.count += 1
        except BaseException as e:  # pragma: no cover - reported by the test
            self.errors.append(e)

    def wait_frames(self, n, timeout=10):
        deadline = time.monotonic() + timeout
        while self.count < n:
            assert time.monotonic() < deadline, "renderer stalled"
            time.sleep(0.005)

    def finish(self):
        self.stop.set()
        self.join(10)
        assert not self.is_alive() and not self.errors


@pytest.mark.parametrize("fullscreen", [True, False])
def test_restore_on_keyboard_interrupt_while_a_thread_renders(fake_tty, fast_switching, fullscreen):
    before = fake_tty.lflag()
    out = SlowOut(fake_tty.out)
    a, b = frames(30, 7)  # inline: 7 rows = what fits above the line below
    term = Terminal(out, fake_tty.inp, color_mode=TC, fullscreen=fullscreen)
    renderer = Renderer(term, (a, b))
    with pytest.raises(KeyboardInterrupt):
        with term:
            renderer.start()
            renderer.wait_frames(8)
            raise KeyboardInterrupt  # restore() now runs while frames are being written
    count = renderer.count
    renderer.wait_frames(count + 8)  # it keeps "rendering" after the restore...
    renderer.finish()

    assert out.max_in_flight == 1  # ...but writes never overlapped
    text = fake_tty.output()
    leave = LEAVE if fullscreen else leave_inline(7)
    assert text.startswith(ENTER if fullscreen else ENTER_INLINE)
    assert text.endswith(leave) and text.count(leave) == 1  # nothing after the restore
    vt = VT(30, 8).feed(text)  # every frame whole: any interleaving breaks the parse
    assert vt.grid(0, 0, 30, 7) in (a, b)
    assert vt.modes[25] and vt.modes[7]
    if fullscreen:
        assert not vt.modes[1049]
    else:
        assert vt.cursor == (0, 7)
    assert fake_tty.lflag() == before


def test_render_after_restore_is_a_noop(fake_tty):
    out = Recorder(fake_tty.out)
    grid = Grid.from_text("hi")
    term = Terminal(out, fake_tty.inp, color_mode=TC)
    term.render(grid)  # never entered: draws, as before
    assert out.writes == [encode_frame(grid, TC)]
    with term:
        term.render(grid)
    n = len(out.writes)
    term.render(Grid.from_text("yo"))
    term.restore()
    assert len(out.writes) == n  # restored: nothing more
    with term:  # entering again draws again
        term.render(grid)
    assert out.writes[n:] == [ENTER, encode_frame(grid, TC), LEAVE]


def test_invalidate_during_a_render_is_not_lost(fake_tty):
    """invalidate() from another thread while a frame is being written still
    makes the *next* render a full redraw."""
    class InvalidateMidWrite(Recorder):
        armed = False

        def write(self, s):
            n = super().write(s)
            if self.armed:
                self.armed = False
                term.invalidate()
            return n

    out = InvalidateMidWrite(fake_tty.out)
    a, b = Grid.from_text("ab"), Grid.from_text("xy")
    with Terminal(out, fake_tty.inp, color_mode=TC) as term:
        term.render(a)
        out.armed = True
        term.render(b)
        n = len(out.writes)
        term.render(b)
        assert out.writes[n:] == [encode_frame(b, TC)]


def test_ctrl_z_while_a_thread_renders(fake_tty, fast_switching):
    """Ctrl-Z in the main thread waits for the frame being written, and no frame
    is drawn while stopped; drawing resumes on the alternate screen afterwards."""
    before = fake_tty.lflag()
    old = signal.signal(signal.SIGTSTP, signal.SIG_DFL)
    out = SlowOut(fake_tty.out)
    a, b = frames(30, 8)
    stopped = {}

    def fake_stop():
        stopped["out"] = fake_tty.output()
        stopped["lflag"] = fake_tty.lflag()
        time.sleep(0.2)  # the renderer is blocked meanwhile, as if stopped too
        stopped["after"] = fake_tty.output()

    try:
        with Terminal(out, fake_tty.inp, color_mode=TC) as term:
            term._suspend_process = fake_stop
            renderer = Renderer(term, (a, b))
            renderer.start()
            renderer.wait_frames(5)
            signal.raise_signal(signal.SIGTSTP)
            count = renderer.count
            renderer.wait_frames(count + 5)
            renderer.finish()
    finally:
        signal.signal(signal.SIGTSTP, old)

    assert stopped["out"].endswith(LEAVE) and stopped["out"].count(LEAVE) == 1
    assert stopped["lflag"] == before
    assert stopped["after"] == stopped["out"]  # nothing drawn while stopped
    assert out.max_in_flight == 1
    text = fake_tty.output()
    resumed = text[len(stopped["out"]):]
    assert resumed.startswith(ENTER)
    assert resumed[len(ENTER):].startswith(encode_frame(a, TC)) or resumed[len(ENTER):].startswith(encode_frame(b, TC))
    vt = VT(30, 8).feed(text)
    assert vt.grid() in (a, b)
    assert fake_tty.lflag() == before
