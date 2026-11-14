"""Ctrl-Z (SIGTSTP) and resume while a Terminal session is active.

The real "stop the process" call (``Terminal._suspend_process``) is replaced
by a fake that records the terminal's state at that moment, so these tests run
on the fake_tty pty without stopping pytest. The last test stops and continues
a real ``asciifx play`` child process.
"""

import os
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest
from vt import VT

from asciifx.core import Grid
from asciifx.term import ColorMode, Terminal
from asciifx.term.ansi import ENTER, ENTER_INLINE, LEAVE, SYNC_BEGIN, encode_frame, leave_inline, reserve_region

TC = ColorMode.TRUECOLOR
RED, BLUE = (255, 0, 0), (0, 0, 255)
SRC = Path(__file__).resolve().parent.parent / "src"


def onlcr(s: str) -> str:
    """How ``s`` reads on the pty's master side: the tty driver turns LF into CR LF."""
    return s.replace("\n", "\r\n")


def is_cbreak(lflag: int) -> bool:
    return not lflag & termios.ICANON and not lflag & termios.ECHO and bool(lflag & termios.ISIG)


class FakeStop:
    """Stands in for the real stop: records what the terminal looks like meanwhile."""

    def __init__(self, fake_tty, during=None):
        self.fake_tty = fake_tty
        self.during = during  # extra things to do "while stopped" (e.g. the shell printing)
        self.calls: list[dict] = []

    def __call__(self) -> None:
        self.calls.append({
            "out": self.fake_tty.output(),
            "lflag": self.fake_tty.lflag(),
            "handler": signal.getsignal(signal.SIGTSTP),
        })
        if self.during is not None:
            self.during()


def ctrl_z() -> None:
    # Thread-directed, so the handler runs right here in the main thread (before
    # raise_signal returns), not whenever another thread's delivery is noticed.
    signal.raise_signal(signal.SIGTSTP)


@pytest.fixture(autouse=True)
def default_sigtstp():
    old = signal.signal(signal.SIGTSTP, signal.SIG_DFL)  # the handler is only installed over SIG_DFL
    yield
    signal.signal(signal.SIGTSTP, old)


def test_fullscreen_ctrl_z_restores_stops_and_reenters(fake_tty):
    before = fake_tty.lflag()
    grid = Grid.from_text("hello\nworld", fg=RED)
    with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC) as term:
        stop = term._suspend_process = FakeStop(fake_tty)
        assert signal.getsignal(signal.SIGTSTP) == term._on_stop
        term.render(grid)
        drawn = fake_tty.output()
        ctrl_z()

        [stopped] = stop.calls
        assert stopped["out"] == drawn + LEAVE  # exactly what exiting writes
        assert stopped["lflag"] == before  # termios restored while stopped
        assert stopped["handler"] == signal.SIG_DFL  # so the real kill really stops
        assert fake_tty.output() == drawn + LEAVE + ENTER  # continued: alt screen, cursor hidden
        assert is_cbreak(fake_tty.lflag())
        assert signal.getsignal(signal.SIGTSTP) == term._on_stop  # reinstalled for the next Ctrl-Z

        term.render(grid)  # same grid, but the screen was cleared: full redraw
        assert fake_tty.output() == drawn + LEAVE + ENTER + encode_frame(grid, TC)
        vt = VT(30, 8).feed(fake_tty.output())
        assert vt.grid(0, 0, 5, 2) == grid
        assert vt.modes[1049] and not vt.modes[25]

        os.kill(os.getpid(), signal.SIGWINCH)  # resize handling still works after a resume
        n = len(fake_tty.output())
        term.render(grid)
        assert fake_tty.output()[n:] == encode_frame(grid, TC)
    assert fake_tty.output().endswith(LEAVE) and fake_tty.output().count(LEAVE) == 2
    assert fake_tty.lflag() == before
    assert signal.getsignal(signal.SIGTSTP) == signal.SIG_DFL


def test_ctrl_z_twice(fake_tty):
    grid = Grid.from_text("x")
    with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC) as term:
        stop = term._suspend_process = FakeStop(fake_tty)
        term.render(grid)
        ctrl_z()
        term.render(grid)
        ctrl_z()
        term.render(grid)
    assert len(stop.calls) == 2
    frame = encode_frame(grid, TC)
    assert fake_tty.output() == (ENTER + frame + LEAVE) * 3


A = Grid.from_text(">_ boot\n[    ]", fg=RED)
B = Grid.from_text(">_ booted.\n[####] ok", fg=BLUE)


def test_inline_ctrl_z_moves_below_and_redraws_in_a_fresh_region(fake_tty):
    """The shell prints its job messages below the region; drawing resumes under them."""
    before = fake_tty.lflag()
    shell, job_messages = "$ line 0\n$ line 1\n", "[1]+ Stopped\n$ fg\n"
    fake_tty.out.write(shell)  # what the shell printed before we started
    fake_tty.out.flush()

    def shell_job_messages():
        fake_tty.out.write(job_messages)
        fake_tty.out.flush()

    with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC, fullscreen=False) as term:
        stop = term._suspend_process = FakeStop(fake_tty, during=shell_job_messages)
        term.render(A)
        drawn = fake_tty.output()
        ctrl_z()

        [stopped] = stop.calls
        assert stopped["out"] == drawn + leave_inline(2)  # exactly what exiting writes
        assert stopped["lflag"] == before
        vt = VT(30, 8).feed(stopped["out"])
        assert vt.cursor == (0, 4)  # just below the region (rows 2-3)
        assert vt.modes[25] and vt.modes[7]  # cursor shown, autowrap on
        assert is_cbreak(fake_tty.lflag())

        # Continued: a fresh region at the cursor, with the last frame redrawn at once.
        resumed = fake_tty.output()[len(stopped["out"] + onlcr(job_messages)):]
        assert resumed == ENTER_INLINE + onlcr(reserve_region(2)) + encode_frame(A, TC, inline=True)
        term.render(B)  # drawn in the new region (checked on the final screen below)

    out = fake_tty.output()
    assert out.startswith(onlcr(shell) + ENTER_INLINE) and out.endswith(leave_inline(2))
    vt = VT(30, 8).feed(out)
    # Rows were: 0-1 shell, 2-3 region A, 4-5 job messages, then reserving 2 rows
    # from row 6 scrolled the screen by one line.
    assert vt.scrolled == 1
    assert [vt.line(y) for y in (0, 3, 4)] == ["$ line 1", "[1]+ Stopped", "$ fg"]
    assert vt.grid(0, 1, A.width, 2) == A  # the old region keeps the frame shown when stopped
    assert vt.grid(0, 5, B.width, 2) == B  # the new region has the final frame
    assert vt.cursor == (0, 7)  # the prompt goes below the new region
    assert vt.modes[25] and vt.modes[7]
    assert fake_tty.lflag() == before


def test_inline_ctrl_z_before_the_first_frame(fake_tty):
    with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC, fullscreen=False) as term:
        term._suspend_process = FakeStop(fake_tty)
        ctrl_z()
        term.render(A)  # reserves its region as usual
    out = fake_tty.output()
    assert out == (
        ENTER_INLINE + leave_inline(None) + ENTER_INLINE
        + onlcr(reserve_region(2)) + encode_frame(A, TC, inline=True) + leave_inline(2)
    )


class StopMidFrame:
    """Output proxy: writes half of the next frame, then gets Ctrl-Z, then writes the rest."""

    def __init__(self, inner):
        self.inner = inner
        self.armed = False

    def write(self, s):
        if self.armed and s.startswith(SYNC_BEGIN):
            self.armed = False
            half = len(s) // 2
            self.inner.write(s[:half])
            self.inner.flush()
            ctrl_z()  # the handler runs now, in the middle of render()
            return self.inner.write(s[half:])
        return self.inner.write(s)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.mark.parametrize("fullscreen", [True, False])
def test_ctrl_z_mid_frame_waits_for_the_frame(fake_tty, fullscreen):
    out = StopMidFrame(fake_tty.out)
    a, b = Grid.from_text("ab", fg=RED), Grid.from_text("xy", fg=BLUE)
    with Terminal(out, fake_tty.inp, color_mode=TC, fullscreen=fullscreen) as term:
        stop = term._suspend_process = FakeStop(fake_tty)
        term.render(a)
        out.armed = True
        term.render(b)
        assert len(stop.calls) == 1  # handled as soon as render was done
    diff = encode_frame(b, TC, a, inline=not fullscreen)
    leave = LEAVE if fullscreen else leave_inline(1)
    assert stop.calls[0]["out"].endswith(diff + leave)  # the whole frame, then the restore
    VT(30, 8).feed(fake_tty.output())  # well-formed throughout


def test_signal_while_stopped_is_raised_after_reentering(fake_tty):
    """E.g. `kill -INT` on the stopped job: delivered on SIGCONT, mid-resume."""
    before = fake_tty.lflag()
    int_before = signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC) as term:
            term._suspend_process = FakeStop(fake_tty, during=lambda: signal.raise_signal(signal.SIGINT))
            ctrl_z()
            pytest.fail("the deferred SIGINT should have been raised by now")
    assert fake_tty.output() == ENTER + LEAVE + ENTER + LEAVE
    assert fake_tty.lflag() == before
    assert signal.getsignal(signal.SIGINT) is int_before
    assert signal.getsignal(signal.SIGTSTP) == signal.SIG_DFL


class StopOnLeave:
    def __init__(self, inner):
        self.inner = inner

    def write(self, s):
        n = self.inner.write(s)
        if s == LEAVE:
            ctrl_z()
        return n

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_ctrl_z_during_restore_stops_after_restoring(fake_tty):
    before = fake_tty.lflag()
    with Terminal(StopOnLeave(fake_tty.out), fake_tty.inp, color_mode=TC) as term:
        stop = term._suspend_process = FakeStop(fake_tty)
    [stopped] = stop.calls  # stopped once the restore was complete, not in the middle
    assert stopped["out"] == ENTER + LEAVE
    assert stopped["lflag"] == before
    assert stopped["handler"] == signal.SIG_DFL
    assert fake_tty.output() == ENTER + LEAVE  # and nothing re-entered afterwards


def test_ignored_sigtstp_is_left_alone(fake_tty):
    signal.signal(signal.SIGTSTP, signal.SIG_IGN)  # e.g. started from a shell without job control
    with Terminal(fake_tty.out, fake_tty.inp):
        assert signal.getsignal(signal.SIGTSTP) == signal.SIG_IGN
    assert signal.getsignal(signal.SIGTSTP) == signal.SIG_IGN


# -- a real stop and continue ------------------------------------------------------


def wait_for(condition, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not (result := condition()):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)
    return result


def test_ctrl_z_end_to_end(fake_tty):
    """`asciifx play` in a child process: SIGTSTP stops it cleanly, SIGCONT resumes drawing."""
    before = fake_tty.lflag()
    env = {k: v for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")}
    env.update(PYTHONPATH=str(SRC), COLORTERM="truecolor")
    proc = subprocess.Popen(
        [sys.executable, "-m", "asciifx", "play", "bounce"],
        stdin=fake_tty.slave, stdout=fake_tty.slave, stderr=subprocess.PIPE,
        env=env,
        # Its own process group, in our session: not orphaned, so the kernel
        # really stops it on SIGTSTP (it discards stop signals for orphans).
        process_group=0,
    )
    try:
        def output():
            return fake_tty.output(settle=0.01)

        wait_for(lambda: output().startswith(ENTER) and len(output()) > 300, "the first frames")
        assert is_cbreak(fake_tty.lflag())

        os.kill(proc.pid, signal.SIGTSTP)  # what the tty driver does on Ctrl-Z
        status = wait_for(lambda: os.waitpid(proc.pid, os.WUNTRACED | os.WNOHANG)[1], "the stop")
        assert os.WIFSTOPPED(status) and os.WSTOPSIG(status) == signal.SIGTSTP
        stopped = output()
        assert stopped.endswith(LEAVE)  # left the alternate screen, cursor shown
        assert fake_tty.lflag() == before  # back to the shell's modes
        time.sleep(0.2)
        assert output() == stopped  # really stopped: no more frames

        os.kill(proc.pid, signal.SIGCONT)  # what `fg` does
        wait_for(lambda: len(output()) > len(stopped) + 300, "frames after the resume")
        assert output()[len(stopped):].startswith(ENTER)  # alternate screen again, full redraw
        assert is_cbreak(fake_tty.lflag())

        fake_tty.send(b"q")
        assert proc.wait(timeout=10) == 0, proc.stderr.read().decode()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stderr.close()
    out = fake_tty.output()
    assert out.endswith(LEAVE) and out.count(ENTER) == 2 and out.count(LEAVE) == 2
    VT(30, 8).feed(out)  # well-formed throughout
    assert fake_tty.lflag() == before
