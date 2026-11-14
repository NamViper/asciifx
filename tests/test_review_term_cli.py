"""Regressions from the review of term/, cli.py, spinner.py and startup.py."""

import io
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_spinner import COLS, ROWS, FakeClock, lines, screen, wait_for
from vt import VT, Recorder

import asciifx.spinner as spinner_module
from asciifx.cli import TMUX_SNIPPET, ZSH_SCREENSAVER, _tmux_quote, main
from asciifx.core import Cell, Grid
from asciifx.spinner import spinner
from asciifx.startup import _os_name
from asciifx.term import ColorMode, KeyReader, Terminal
from asciifx.term.ansi import AUTOWRAP_OFF, encode_frame, leave_inline

TC = ColorMode.TRUECOLOR
SRC = Path(__file__).resolve().parent.parent / "src"
LOGO = Path(__file__).resolve().parent.parent / "skull_logo.png"


# -- encoder: glyphs that aren't one printable character ---------------------------


def evil_grid() -> Grid:
    g = Grid.from_text("abcdef")
    g.set(1, 0, Cell("\x1b"))  # Grid.set doesn't filter, unlike put_text
    g.set(2, 0, Cell("]0;pwned\x07"))  # OSC: would retitle the window
    g.set(3, 0, Cell("\x9b"))  # C1 CSI
    return g


@pytest.mark.parametrize("inline", [False, True])
def test_control_characters_in_cells_never_reach_the_terminal(inline):
    g = evil_grid()
    full = encode_frame(g, TC, inline=inline)
    diff = encode_frame(g, TC, Grid.from_text("abcdef"), inline=inline)
    for data in (full, diff):
        assert "\x07" not in data and "\x9b" not in data and "]0;" not in data.replace("]0;pwned", "")
        assert not re.search(r"\x1b(?![\[78])", data)  # every ESC starts one of our own sequences
    vt = VT(20, 3).feed(AUTOWRAP_OFF + "\x1b7" + full)  # the VT fails on any stray control
    assert vt.line(0).startswith("a ]0;pwned ")  # shown as text (multi-char glyphs still misalign)


def test_control_characters_in_cells_not_printed_when_piped():
    buf = io.StringIO()
    Terminal(out=buf).render(evil_grid())
    assert "\x1b" not in buf.getvalue() and "\x07" not in buf.getvalue() and "\x9b" not in buf.getvalue()


def test_empty_glyph_takes_its_column():
    """An empty glyph used to write nothing, shifting the rest of the row left."""
    g = Grid(4, 1, [Cell("a"), Cell(""), Cell("c"), Cell("d")])
    vt = VT(10, 2).feed(AUTOWRAP_OFF + encode_frame(g, TC))
    assert vt.line(0) == "a cd"
    g2 = Grid(4, 1, [Cell("a"), Cell(""), Cell("X"), Cell("Y")])
    vt.feed(encode_frame(g2, TC, g))
    assert vt.line(0) == "a XY"


# -- Terminal: frames larger than the screen ----------------------------------------


def test_inline_frame_wider_than_the_screen_is_cut_not_piled_into_the_last_column(fake_tty):
    text = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN"  # 40 columns on a 30-column screen
    with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC, fullscreen=False) as term:
        term.render(Grid.from_text(text))
        term.render(Grid.from_text(text.upper()))  # a diff, also cut
    vt = VT(30, 8).feed(fake_tty.output())
    assert vt.line(0) == text.upper()[:30]  # column 29 used to show "N", the row's last glyph
    assert vt.cursor == (0, 1)


def test_fullscreen_frame_larger_than_the_screen_is_cut(fake_tty):
    big = Grid(40, 12, [Cell(chr(ord("A") + y)) for y in range(12) for _ in range(40)])
    with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC) as term:
        term.render(big)
        vt = VT(30, 8).feed(fake_tty.output())
    assert [vt.line(y) for y in range(8)] == [chr(ord("A") + y) * 30 for y in range(8)]


def test_print_above_without_a_session_leaves_autowrap_on(fake_tty):
    term = Terminal(fake_tty.out, fake_tty.inp, color_mode=TC, fullscreen=False)
    term.print_above("hello")  # never entered: nothing will restore the modes afterwards
    vt = VT(30, 8).feed(fake_tty.output())
    assert vt.line(0) == "hello" and vt.autowrap
    term.render(Grid.from_text("x"))
    term.print_above("again")
    vt = VT(30, 8).feed(fake_tty.output())
    assert vt.autowrap


# -- keys --------------------------------------------------------------------------


def test_escape_sequence_split_across_reads_is_one_key():
    r, w = os.pipe()
    try:
        reader = KeyReader(r)
        os.write(w, b"\x1b")
        threading.Timer(0.01, os.write, args=(w, b"[A")).start()
        assert reader.poll(1) == ["up"]  # was ["esc"], then ["[", "A"]
        os.write(w, b"q\x1b[1;5")
        threading.Timer(0.01, os.write, args=(w, b"C")).start()
        assert reader.poll(1) == ["q", "\x1b[1;5C"]
        os.write(w, b"\x1b")  # a lone Esc still comes through, just a little later
        start = time.monotonic()
        assert reader.poll(1) == ["esc"]
        assert time.monotonic() - start < 0.5
    finally:
        os.close(r)
        os.close(w)


# -- cli -----------------------------------------------------------------------------


@pytest.mark.parametrize("args", [
    ["img", "/nonexistent/logo.png"],
    ["img", __file__],  # not an image
    ["img", str(LOGO), "--threshold", "5"],
])
def test_img_errors_are_messages_not_tracebacks(capsys, args):
    if not LOGO.exists():
        pytest.skip("logo not present")
    assert main(args) == 2
    err = capsys.readouterr().err
    assert err.startswith("asciifx: ") and "Traceback" not in err


@pytest.mark.parametrize("args", [
    ["img", "x.png", "--width", "0"],
    ["img", "x.png", "--height", "-3"],
    ["shell-init", "tmux", "--screensaver-after", "-5"],
])
def test_nonsense_numbers_are_rejected(capsys, args):
    with pytest.raises(SystemExit) as info:
        main(args)
    assert info.value.code == 2


def test_img_piped_honors_an_explicit_width(capsys, monkeypatch):
    if not LOGO.exists():
        pytest.skip("logo not present")
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    assert main(["img", str(LOGO), "--mode", "ramp", "--width", "120"]) == 0
    out = capsys.readouterr().out
    assert max(len(line) for line in out.splitlines()) == 120  # was capped to the 80-column fallback


def test_summon_with_a_logo_that_is_not_an_image(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert main(["summon", "--logo", __file__]) == 2
    err = capsys.readouterr().err
    assert err.startswith("asciifx: ") and "Traceback" not in err


def test_closed_pipe_is_not_a_traceback():
    if not LOGO.exists():
        pytest.skip("logo not present")
    r, w = os.pipe()
    os.close(r)  # like `| head` having exited already
    env = dict(os.environ, PYTHONPATH=str(SRC), COLUMNS="60", LINES="20")
    try:
        proc = subprocess.run([sys.executable, "-m", "asciifx", "img", str(LOGO), "--style", "all"],
                              stdout=w, stderr=subprocess.PIPE, env=env, timeout=60)
    finally:
        os.close(w)
    assert proc.returncode == 141
    assert proc.stderr == b""


@pytest.mark.parametrize("logo", ['/l/a"b.png', "/l/$HOME/x.png", "/l/it's.png", "/l/back\\slash #x.png"])
def test_tmux_lock_command_survives_special_characters(capsys, logo):
    assert main(["shell-init", "tmux", "--logo", logo]) == 0
    out = capsys.readouterr().out
    assert f"set -g lock-command {_tmux_quote('asciifx screensaver --logo ' + shlex.quote(logo) + ' --idle-for 120')}" in out
    if shutil.which("tmux"):
        conf = Path(os.environ.get("TMPDIR", "/tmp")) / f"asciifx-test-{os.getpid()}.conf"
        sock = conf.with_suffix(".sock")
        conf.write_text(out)
        try:
            r = subprocess.run(["tmux", "-S", str(sock), "-f", str(conf), "start-server", ";",
                                "show", "-gv", "lock-command"], capture_output=True, text=True, timeout=20)
        finally:
            subprocess.run(["tmux", "-S", str(sock), "kill-server"], capture_output=True)
            conf.unlink(missing_ok=True)
        assert shlex.split(r.stdout) == ["asciifx", "screensaver", "--logo", logo, "--idle-for", "120"], r.stderr


def test_tmux_quote():
    assert _tmux_quote('a "b" $c \\d') == '"a \\"b\\" \\$c \\\\d"'
    assert "{command}" in TMUX_SNIPPET


def _trapalrm() -> str:
    body = re.search(r"^  TRAPALRM\(\) \{\{\n.*?^  \}\}\n", ZSH_SCREENSAVER, re.M | re.S)
    assert body is not None
    return body.group().replace("{{", "{").replace("}}", "}").replace("{args}", "")


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
@pytest.mark.parametrize("ago,fires", [(1, False), (2, False), (3, True)])
def test_zsh_screensaver_waits_the_full_idle_time(ago, fires):
    """EPOCHSECONDS is whole seconds: a last key "2 s ago" may be 1.01 s ago, too early for 2."""
    script = (
        "zmodload zsh/datetime\n"
        "asciifx() { print FIRED }\n"
        f"{_trapalrm()}"
        "for try in 1 2 3; do\n"
        "  t0=$EPOCHSECONDS\n"
        f"  typeset -gi _asciifx_idle_after=2 _asciifx_last_key=$(( EPOCHSECONDS - {ago} ))\n"
        "  out=$(TRAPALRM; print TMOUT=$TMOUT)\n"
        "  (( EPOCHSECONDS == t0 )) && break  # no second boundary crossed meanwhile\n"
        "done\n"
        "print -r -- $out\n"
    )
    r = subprocess.run(["zsh", "-f", "-c", script], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    assert ("FIRED" in r.stdout) == fires, r.stdout
    tmout = int(r.stdout.rsplit("TMOUT=", 1)[1])
    assert tmout == (2 if fires else 2 - ago + 1)


# -- startup ----------------------------------------------------------------------------


def test_os_name_survives_an_undecodable_os_release(tmp_path):
    f = tmp_path / "os-release"
    f.write_bytes(b'PRETTY_NAME="Caf\xe9 OS"\n')
    assert _os_name(f)  # falls back to platform.system() + release instead of raising


# -- spinner -------------------------------------------------------------------------------


def spinner_term(fake_tty, out=None):
    fake_tty.resize(COLS, ROWS)
    return Terminal(out or fake_tty.out, fake_tty.inp, color_mode=TC, fullscreen=False)


def test_write_while_finishing_lands_above_the_final_line(fake_tty, monkeypatch):
    clock = FakeClock()
    real_join = spinner_module._join

    def join_after_a_late_write(thread):
        w = threading.Thread(target=sp.write, args=("late",))
        w.start()
        w.join()
        real_join(thread)

    monkeypatch.setattr(spinner_module, "_join", join_after_a_late_write)
    with spinner("work", terminal=spinner_term(fake_tty), clock=clock) as sp:
        wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ work  0.0s")
        clock.t = 1.0
    assert lines(screen(fake_tty))[:3] == ["late", "✔ work (1.0s)", ""]


def test_sigint_during_the_final_restore_still_finishes_the_spinner(fake_tty):
    """A SIGINT arriving while the terminal is restored is raised afterwards; the
    spinner must count as done, so a later write() still prints."""

    class SigintOnLeave(Recorder):
        armed = True

        def write(self, s):
            n = super().write(s)
            if s == leave_inline(1) and self.armed:
                self.armed = False
                os.kill(os.getpid(), signal.SIGINT)
            return n

    before = fake_tty.lflag()
    clock = FakeClock()
    with pytest.raises(KeyboardInterrupt):
        with spinner("work", terminal=spinner_term(fake_tty, SigintOnLeave(fake_tty.out)), clock=clock) as sp:
            wait_for(fake_tty, lambda vt: vt.line(0) == "⠋ work  0.0s")
            clock.t = 1.0
    sp.write("after")  # used to be held back forever
    vt = screen(fake_tty)
    assert lines(vt)[:3] == ["✔ work (1.0s)", "after", ""]
    assert vt.cursor == (0, 2) and fake_tty.lflag() == before
