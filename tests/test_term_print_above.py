"""Terminal.print_above: permanent lines above an inline region (e.g. a spinner's log)."""

import io
import signal
import threading

import pytest
from test_spinner import COLS, ROWS, FakeClock, lines, screen, wait_for
from test_term_threads import Renderer, SlowOut
from vt import VT, Recorder

from asciifx.core import Grid
from asciifx.spinner import spinner
from asciifx.term import ColorMode, Terminal
from asciifx.term.ansi import (
    AUTOWRAP_OFF,
    AUTOWRAP_ON,
    ENTER_INLINE,
    ERASE_DOWN,
    RESET,
    RESTORE_CURSOR,
    SYNC_BEGIN,
    SYNC_END,
    encode_frame,
    leave_inline,
    print_above,
    reserve_region,
)

TC = ColorMode.TRUECOLOR
RED, BLUE = (255, 0, 0), (0, 0, 255)
FRAME = Grid.from_text("⠋ work\n[##  ]", fg=RED)  # a 2-row region
FRAME2 = Grid.from_text("⠙ work\n[### ]", fg=BLUE)


def inline(fake_tty, out=None):
    return Terminal(out or fake_tty.out, fake_tty.inp, color_mode=TC, fullscreen=False)


def shell(fake_tty, text):
    fake_tty.out.write(text)  # what the shell printed before we started
    fake_tty.out.flush()


def test_sequence():
    assert print_above(2, ["a", "bc"]) == (
        SYNC_BEGIN + RESTORE_CURSOR + ERASE_DOWN + AUTOWRAP_ON + "a\r\nbc\r\n" + AUTOWRAP_OFF
        + reserve_region(2) + SYNC_END
    )
    # no region yet: at the cursor, and the first render reserves it below
    assert print_above(None, ["a"]) == SYNC_BEGIN + RESET + ERASE_DOWN + AUTOWRAP_ON + "a\r\n" + AUTOWRAP_OFF + SYNC_END


def test_lines_go_above_and_the_region_is_redrawn_below_in_one_write(fake_tty):
    shell(fake_tty, "$ run\n")
    out = Recorder(fake_tty.out)
    with inline(fake_tty, out) as term:
        term.render(FRAME)
        n = len(out.writes)
        term.print_above("first\nsecond")
        assert out.writes[n:] == [print_above(2, ["first", "second"]) + encode_frame(FRAME, TC, inline=True)]
        term.render(FRAME2)  # a diff, drawn in the region's new place
        assert len(out.writes[-1]) < len(encode_frame(FRAME2, TC, inline=True))
    vt = VT(30, 8).feed(fake_tty.output())
    assert [vt.line(y) for y in range(3)] == ["$ run", "first", "second"]
    assert vt.grid(0, 3, FRAME2.width, 2) == FRAME2
    assert vt.cursor == (0, 5)  # the prompt goes below the region
    assert vt.modes[25] and vt.modes[7]


def test_long_lines_wrap_without_leftovers(fake_tty):
    wide = Grid.from_text("x" * 30 + "\n" + "y" * 30, fg=RED)  # a full-width 2-row region
    with inline(fake_tty) as term:
        term.render(wide)
        term.print_above("a" * 45 + "\n" + "b" * 30 + "\nc")
    vt = VT(30, 8).feed(fake_tty.output())
    # 45 chars wrap to a second row; no "y"s of the old frame are left next to them.
    # Exactly 30 chars: the CR LF after it doesn't add an empty row (xterm's pending wrap).
    assert [vt.line(y) for y in range(4)] == ["a" * 30, "a" * 15, "b" * 30, "c"]
    assert vt.grid(0, 4, 30, 2) == wide
    assert vt.cursor == (0, 6)


def test_scrolls_at_the_bottom(fake_tty):
    with inline(fake_tty) as term:
        term.render(FRAME)
        for i in range(12):  # more than the 8-row screen
            term.print_above(f"line {i}")
        term.render(FRAME2)
    vt = VT(30, 8).feed(fake_tty.output())
    assert [vt.line(y) for y in range(5)] == [f"line {i}" for i in range(7, 12)]
    assert vt.grid(0, 5, FRAME2.width, 2) == FRAME2
    assert vt.cursor == (0, 7)


def test_before_the_first_frame(fake_tty):
    shell(fake_tty, "$ run\n")
    with inline(fake_tty) as term:
        term.print_above("early")
        term.render(FRAME)
    out = fake_tty.output()
    assert out.endswith(leave_inline(2))
    vt = VT(30, 8).feed(out)
    assert [vt.line(y) for y in range(2)] == ["$ run", "early"]
    assert vt.grid(0, 2, FRAME.width, 2) == FRAME
    assert vt.cursor == (0, 4)


def test_control_characters_become_spaces(fake_tty):
    with inline(fake_tty) as term:
        term.render(FRAME)
        term.print_above("a\tb\x1b[31mc\rd\r\ne\x07")
    vt = VT(30, 8).feed(fake_tty.output())  # would choke on a stray ESC or BEL
    assert [vt.line(0), vt.line(1)] == ["a b [31mc d", "e"]
    assert vt.grid(0, 0, 11, 1).cells[0].fg is None  # no color leaked into the line


def test_screen_shrunk_since_the_region_was_reserved(fake_tty):
    tall = Grid.from_text("\n".join("abcdefg"))  # 7 rows: the most an 8-row screen allows
    out = Recorder(fake_tty.out)
    with inline(fake_tty, out) as term:
        term.render(tall)
        fake_tty.resize(30, 4)
        term.print_above("hi")
        # the region is clamped to screen rows - 1, and the frame cropped to it
        assert out.writes[-1] == print_above(3, ["hi"]) + encode_frame(Grid(1, 3, tall.cells[:3]), TC, inline=True)
    assert fake_tty.output().endswith(leave_inline(3))


def test_fullscreen_refuses_and_non_tty_prints_plain_lines(fake_tty):
    with Terminal(fake_tty.out, fake_tty.inp) as term:
        with pytest.raises(RuntimeError):
            term.print_above("x")
    for fullscreen in (False, True):
        buf = io.StringIO()
        with Terminal(out=buf, fullscreen=fullscreen) as term:
            term.print_above("one\ntwo\x1b")
        assert buf.getvalue() == "one\ntwo \n"


def test_noop_after_restore(fake_tty):
    out = Recorder(fake_tty.out)
    term = inline(fake_tty, out)
    with term:
        term.render(FRAME)
    n = len(out.writes)
    term.print_above("late")  # e.g. a worker thread logging after the main thread left
    assert len(out.writes) == n


def test_from_threads_while_another_thread_renders(fake_tty, fast_switching):
    out = SlowOut(fake_tty.out)
    with inline(fake_tty, out) as term:
        renderer = Renderer(term, (FRAME, FRAME2))
        renderer.start()
        renderer.wait_frames(3)
        writers = [threading.Thread(target=term.print_above, args=(f"line {i}",)) for i in range(10)]
        for w in writers:
            w.start()
        for w in writers:
            w.join()
        renderer.wait_frames(renderer.count + 3)
        renderer.finish()
    assert out.max_in_flight == 1
    text = fake_tty.output()
    assert all(text.count(f"line {i}\r") == 1 for i in range(10))  # each printed once
    vt = VT(30, 8).feed(text)
    printed = [vt.line(y) for y in range(5)]  # 10 lines + 2-row region + line below: scrolled
    assert sorted(printed) == sorted(set(printed)) and all(p.startswith("line ") for p in printed)
    assert vt.grid(0, 5, FRAME.width, 2) in (FRAME, FRAME2)
    assert vt.cursor == (0, 7)


# -- with the spinner (moved from the spinner agent's proposal) ----------------------


def spinner_term(fake_tty):
    fake_tty.resize(COLS, ROWS)
    return Terminal(fake_tty.out, fake_tty.inp, color_mode=TC, fullscreen=False)


def test_spinner_write_prints_above_from_any_thread(fake_tty):
    before = fake_tty.lflag()
    clock = FakeClock()
    with spinner("work", terminal=spinner_term(fake_tty), clock=clock) as sp:
        sp.write("before first frame")
        wait_for(fake_tty, lambda vt: lines(vt)[:2] == ["before first frame", "⠋ work  0.0s"])
        sp.write("first\nsecond")
        # redrawn in the same write: no wait needed beyond output settling
        assert lines(screen(fake_tty))[:4] == ["before first frame", "first", "second", "⠋ work  0.0s"]
        w = threading.Thread(target=sp.write, args=("worker",))
        w.start()
        w.join()
        assert lines(screen(fake_tty))[:5] == ["before first frame", "first", "second", "worker", "⠋ work  0.0s"]
        clock.t = 2.0
    vt = screen(fake_tty)
    assert lines(vt)[:6] == ["before first frame", "first", "second", "worker", "✔ work (2.0s)", ""]
    assert vt.cursor == (0, 5) and fake_tty.lflag() == before


def test_spinner_write_scrolls_at_the_bottom(fake_tty):
    clock = FakeClock()
    with spinner("work", terminal=spinner_term(fake_tty), clock=clock) as sp:
        for i in range(15):  # more than the screen height
            sp.write(f"line {i}")
        clock.t = 1.0
    vt = screen(fake_tty)
    assert lines(vt)[-3:] == ["line 14", "✔ work (1.0s)", ""]
    assert vt.cursor == (0, ROWS - 1)


def test_inline_region_moved_by_print_above_survives_ctrl_z(fake_tty):
    """After print_above, Ctrl-Z leaves from (and resumes below) the *moved* region."""
    old = signal.signal(signal.SIGTSTP, signal.SIG_DFL)
    try:
        with inline(fake_tty) as term:
            term._suspend_process = lambda: None
            term.render(FRAME)
            term.print_above("log")
            signal.raise_signal(signal.SIGTSTP)
            term.render(FRAME2)
    finally:
        signal.signal(signal.SIGTSTP, old)
    out = fake_tty.output()
    assert out.startswith(ENTER_INLINE)
    vt = VT(30, 8).feed(out)
    # rows: 0 log, 1-2 region when stopped, 3-4 region after the resume
    assert vt.line(0) == "log"
    assert vt.grid(0, 1, FRAME.width, 2) == FRAME
    assert vt.grid(0, 3, FRAME2.width, 2) == FRAME2
    assert vt.cursor == (0, 5)
