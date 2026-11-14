"""spinner(): pure line content at fixed t, plus end to end on a pseudo-terminal.

The spinner thread draws asynchronously, so TTY tests poll the emulated screen
until the expected content shows up (``wait_for``) instead of sleeping, and
drive the elapsed time with a fake clock so every line is exact.
"""

import io
import re
import signal
import threading
import time
import unicodedata

import pytest
from vt import VT, VTError

from asciifx.core import Cell
from asciifx.spinner import (
    BLOOD_RED,
    DEFAULT_RATE,
    DIM,
    FAIL_COLOR,
    INTERRUPT_COLOR,
    SPINNERS,
    SUCCESS_COLOR,
    THREAD_NAME,
    Spinner,
    demo,
    spinner,
)
from asciifx.term import ColorMode, Terminal

COLS, ROWS = 40, 10


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def inline_term(fake_tty, cls=Terminal):
    fake_tty.resize(COLS, ROWS)
    return cls(fake_tty.out, fake_tty.inp, color_mode=ColorMode.TRUECOLOR, fullscreen=False)


def plain_term(buf=None):
    return Terminal(out=buf if buf is not None else io.StringIO(), fullscreen=False)


def screen(fake_tty) -> VT:
    return VT(COLS, ROWS).feed(fake_tty.output(settle=0.01))


def lines(vt: VT) -> list[str]:
    return [vt.line(y) for y in range(vt.height)]


def wait_for(fake_tty, check, timeout: float = 5.0) -> VT:
    """Poll the screen until ``check(vt)`` holds."""
    deadline = time.monotonic() + timeout
    seen: list[str] = []
    while True:
        try:
            vt = screen(fake_tty)
            if check(vt):
                return vt
            seen = lines(vt)
        except VTError:
            pass  # read the output in the middle of an escape sequence; try again
        if time.monotonic() > deadline:
            raise AssertionError(f"screen never matched; last seen: {seen}")
        time.sleep(0.01)


def spinner_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == THREAD_NAME]


def assert_clean_exit(fake_tty, lflag_before: int, vt: VT) -> None:
    assert fake_tty.lflag() == lflag_before  # termios restored
    assert vt.modes.get(25) and vt.modes.get(7)  # cursor shown, autowrap back on
    assert not spinner_threads()  # the drawing thread is gone


# -- pure: the line at fixed t --------------------------------------------------


def test_line_at_fixed_times():
    sp = Spinner("work", terminal=plain_term())
    assert sp.line(0.0).to_text() == "⠋ work  0.0s"
    assert sp.line(0.1).to_text() == "⠙ work  0.1s"  # dots: 12 frames/s
    grid = sp.line(0.3)
    assert grid.to_text() == "⠸ work  0.3s"
    assert grid.get(0, 0) == Cell("⠸", BLOOD_RED)
    assert grid.get(2, 0) == Cell("w")  # text in the terminal's default color
    assert grid.get(8, 0) == Cell("0", DIM)


def test_line_shortens_text_to_fit_width():
    sp = Spinner("a long task name", terminal=plain_term())
    assert sp.line(0.0, width=14).to_text() == "⠋ a l...  0.0s"
    assert sp.line(0.0, width=80).to_text() == "⠋ a long task name  0.0s"


def test_pulse_color_follows_time():
    sp = Spinner(style="pulse", color=(200, 20, 30), terminal=plain_term())
    assert sp.line(0.0).get(0, 0) == Cell("⣀", (60, 6, 9))  # darkest: PULSE_FLOOR x color
    assert sp.line(0.5).get(0, 0) == Cell("⣿", (200, 20, 30))  # peak: full color, fullest glyph
    assert sp.line(1.0).get(0, 0) == Cell("⣀", (60, 6, 9))


@pytest.mark.parametrize("name", sorted(SPINNERS))
def test_styles_are_single_width_and_steady(name):
    sp = Spinner(style=name, terminal=plain_term())
    assert sp.frames and sp.rate > 0
    assert len({len(f) for f in sp.frames}) == 1  # equal widths: the text doesn't jitter
    for ch in "".join(sp.frames) + "✔✖":
        assert unicodedata.east_asian_width(ch) in ("N", "Na", "H"), f"{ch!r} may be drawn wide"
        assert unicodedata.category(ch) not in ("Mn", "Me", "Cf", "Cc"), f"{ch!r} is not a spacing character"


def test_custom_styles_with_or_without_rate(monkeypatch):
    monkeypatch.setitem(SPINNERS, "plain", ("a", "b"))
    monkeypatch.setitem(SPINNERS, "timed", (("a", "b"), 4))
    assert Spinner(style="plain", terminal=plain_term()).rate == DEFAULT_RATE
    sp = Spinner(style="timed", terminal=plain_term())
    assert (sp.frames, sp.rate) == (("a", "b"), 4.0)
    assert sp.line(0.3).to_text() == "b  0.3s"  # floor(0.3 * 4) = 1; no text: no gap for it


def test_rejects_unknown_style_and_bad_fps():
    with pytest.raises(ValueError, match="unknown spinner style 'nope'"):
        Spinner(style="nope", terminal=plain_term())
    with pytest.raises(ValueError, match="fps"):
        Spinner(fps=0, terminal=plain_term())


# -- not a terminal --------------------------------------------------------------


def test_non_tty_prints_start_writes_and_final_line_only():
    buf, clock = io.StringIO(), FakeClock()
    with spinner("loading", terminal=plain_term(buf), clock=clock) as sp:
        assert not spinner_threads()  # nothing animates
        sp.text = "almost"
        sp.write("step 1\nstep 2")
        clock.t = 1.5
    assert buf.getvalue() == "loading...\nstep 1\nstep 2\n✔ almost (1.5s)\n"


def test_non_tty_failure_and_stop():
    buf = io.StringIO()
    with pytest.raises(RuntimeError):
        with spinner("deploy", terminal=plain_term(buf), clock=lambda: 0.0):
            raise RuntimeError("nope")
    with spinner("quiet", terminal=plain_term(buf), clock=lambda: 0.0) as sp:
        sp.stop()
    assert buf.getvalue() == "deploy...\n✖ deploy (0.0s)\nquiet...\n"


def test_works_as_a_decorator():
    buf = io.StringIO()

    @spinner("crunch", terminal=plain_term(buf), clock=lambda: 0.0)
    def double(x):
        return 2 * x

    assert double(21) == 42 and double(2) == 4
    assert buf.getvalue() == "crunch...\n✔ crunch (0.0s)\n" * 2


# -- on a terminal ---------------------------------------------------------------


def test_success_leaves_exact_final_line(fake_tty):
    before = fake_tty.lflag()
    clock = FakeClock()
    with spinner("loading", terminal=inline_term(fake_tty), clock=clock):
        vt = wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ loading  0.0s")
        assert vt.lines[0][0].fg == BLOOD_RED
        assert len(spinner_threads()) == 1
        clock.t = 1.5
    vt = screen(fake_tty)
    assert lines(vt)[:2] == ["✔ loading (1.5s)", ""]
    assert vt.lines[0][0].fg == SUCCESS_COLOR
    assert vt.cursor == (0, 1)  # the prompt goes below the final line
    assert "\x1b[?1049h" not in fake_tty.output()  # inline, never the alternate screen
    assert_clean_exit(fake_tty, before, vt)


def test_frames_follow_the_clock(fake_tty):
    clock = FakeClock()
    with spinner("x", style="line", terminal=inline_term(fake_tty), clock=clock):
        for t, glyph in [(0.0, "|"), (0.2, "/"), (0.4, "-"), (0.6, "\\"), (0.7, "|")]:  # 6 frames/s
            clock.t = t
            wait_for(fake_tty, lambda vt, t=t, glyph=glyph: vt.line(0) == f"{glyph} x  {t:.1f}s")


def test_text_updates_show_up(fake_tty):
    clock = FakeClock()
    with spinner("phase one", terminal=inline_term(fake_tty), clock=clock) as sp:
        wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ phase one  0.0s")
        sp.text = "phase two"
        wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ phase two  0.0s")
        clock.t = 3.0
    assert screen(fake_tty).line(0) == "✔ phase two (3.0s)"


def test_write_puts_lines_above_the_spinner(fake_tty):
    before = fake_tty.lflag()
    clock = FakeClock()
    with spinner("work", terminal=inline_term(fake_tty), clock=clock) as sp:
        wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ work  0.0s")
        sp.write("first")
        sp.write("second\nthird")
        wait_for(fake_tty, lambda vt: lines(vt)[:4] == ["first", "second", "third", "⠋ work  0.0s"])
        clock.t = 2.0
    vt = screen(fake_tty)
    assert lines(vt)[:5] == ["first", "second", "third", "✔ work (2.0s)", ""]
    assert vt.cursor == (0, 4)
    assert_clean_exit(fake_tty, before, vt)


def test_write_from_another_thread_lands_above_the_final_line(fake_tty):
    clock = FakeClock()
    with spinner("work", terminal=inline_term(fake_tty), clock=clock) as sp:
        wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ work  0.0s")
        worker = threading.Thread(target=sp.write, args=("from a worker",))
        worker.start()
        worker.join()
        clock.t = 1.0
    assert lines(screen(fake_tty))[:3] == ["from a worker", "✔ work (1.0s)", ""]


def test_write_uses_terminal_print_above_when_it_exists(fake_tty):
    class PrintAboveTerminal(Terminal):
        printed: list[str]

        def print_above(self, text: str) -> None:
            self.printed.append(text)

    term = inline_term(fake_tty, PrintAboveTerminal)
    term.printed = []
    with spinner("work", terminal=term, clock=FakeClock()) as sp:
        sp.write("a\nb")
        worker = threading.Thread(target=sp.write, args=("c",))
        worker.start()
        worker.join()
        assert term.printed == ["a\nb", "c"]  # right away, from any thread


def test_succeed_with_message_then_write_prints_below(fake_tty):
    clock = FakeClock()
    with spinner("work", terminal=inline_term(fake_tty), clock=clock) as sp:
        clock.t = 0.5
        sp.succeed("all done")
        assert not spinner_threads()
        sp.write("after")
        clock.t = 9.0  # leaving the block changes nothing any more
    vt = screen(fake_tty)
    assert lines(vt)[:3] == ["✔ all done (0.5s)", "after", ""]
    assert vt.cursor == (0, 2)


def test_stop_erases_the_line(fake_tty):
    before = fake_tty.lflag()
    with spinner("work", terminal=inline_term(fake_tty), clock=FakeClock()) as sp:
        wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ work  0.0s")
        sp.stop()
    vt = screen(fake_tty)
    assert vt.line(0) == ""
    assert_clean_exit(fake_tty, before, vt)


def test_exception_shows_cross_and_propagates(fake_tty):
    before = fake_tty.lflag()
    clock = FakeClock()
    with pytest.raises(ValueError, match="boom"):
        with spinner("work", terminal=inline_term(fake_tty), clock=clock):
            clock.t = 0.7
            raise ValueError("boom")
    vt = screen(fake_tty)
    assert lines(vt)[:2] == ["✖ work (0.7s)", ""]
    assert vt.lines[0][0].fg == FAIL_COLOR
    assert_clean_exit(fake_tty, before, vt)


def test_ctrl_c_shows_interrupted_and_propagates(fake_tty):
    before = fake_tty.lflag()
    handler = signal.getsignal(signal.SIGINT)
    clock = FakeClock()
    with pytest.raises(KeyboardInterrupt):
        with spinner("work", terminal=inline_term(fake_tty), clock=clock):
            wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ work  0.0s")
            clock.t = 1.2
            signal.raise_signal(signal.SIGINT)  # the Terminal's handler raises KeyboardInterrupt
            pytest.fail("SIGINT did not interrupt the block")
    vt = screen(fake_tty)
    assert lines(vt)[:2] == ["✖ work (interrupted, 1.2s)", ""]
    assert vt.lines[0][0].fg == INTERRUPT_COLOR
    assert signal.getsignal(signal.SIGINT) == handler
    assert_clean_exit(fake_tty, before, vt)


def test_drawing_thread_error_is_raised_after_cleanup(fake_tty):
    class BrokenTerminal(Terminal):
        def size(self):
            if threading.current_thread().name == THREAD_NAME:
                raise OSError("tty gone")
            return super().size()

    before = fake_tty.lflag()
    with pytest.raises(RuntimeError, match="spinner thread failed") as info:
        with spinner("work", terminal=inline_term(fake_tty, BrokenTerminal), clock=FakeClock()):
            deadline = time.monotonic() + 5
            while spinner_threads() and time.monotonic() < deadline:
                time.sleep(0.01)
    assert isinstance(info.value.__cause__, OSError)
    vt = screen(fake_tty)
    assert vt.line(0) == "✔ work (0.0s)"
    assert_clean_exit(fake_tty, before, vt)


def test_demo_any_key_ends_it_early(fake_tty):
    before = fake_tty.lflag()
    threading.Timer(0.3, fake_tty.send, args=(b"q",)).start()
    start = time.monotonic()
    demo("arc", 30.0, "summoning", terminal=inline_term(fake_tty))
    assert time.monotonic() - start < 5
    vt = screen(fake_tty)
    assert re.fullmatch(r"✔ summoning \(\d+\.\ds\)", vt.line(0))
    assert vt.cursor == (0, 1)
    assert_clean_exit(fake_tty, before, vt)


def test_demo_non_tty():
    buf = io.StringIO()
    demo("skull", 0.0, "x", terminal=plain_term(buf))
    assert buf.getvalue() == "x...\n✔ x (0.0s)\n"
