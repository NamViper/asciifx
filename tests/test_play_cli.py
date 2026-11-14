"""End-to-end: play() and the CLI, on a pipe and on a pseudo-terminal."""

import io
import threading
import time

import pytest
from vt import VT

from asciifx import play
from asciifx.cli import main
from asciifx.sources import Bounce
from asciifx.term import ColorMode, Terminal
from asciifx.term.ansi import ENTER, LEAVE


def test_play_on_non_tty_prints_single_static_frame(monkeypatch):
    monkeypatch.setenv("COLUMNS", "40")
    monkeypatch.setenv("LINES", "10")
    buf = io.StringIO()
    play(Bounce(), terminal=Terminal(out=buf))
    assert buf.getvalue() == Bounce().frame(0.0, 40, 10).to_text() + "\n"
    assert "\x1b" not in buf.getvalue()


def test_play_runs_for_duration_and_restores(fake_tty):
    before = fake_tty.lflag()
    start = time.monotonic()
    play(Bounce(), fps=30, duration=0.3, terminal=Terminal(fake_tty.out, fake_tty.inp, color_mode=ColorMode.TRUECOLOR))
    elapsed = time.monotonic() - start
    assert 0.29 <= elapsed < 1.0
    out = fake_tty.output()
    assert out.startswith(ENTER) and out.endswith(LEAVE)
    # Diff rendering writes nothing for unchanged frames, so check the result instead
    # of counting frames: the loop guarantees a last tick at exactly t == duration.
    assert VT(30, 8).feed(out).grid() == Bounce().frame(0.3, 30, 8)
    assert fake_tty.lflag() == before


def test_any_key_quits(fake_tty):
    threading.Timer(0.2, fake_tty.send, args=(b"x",)).start()
    start = time.monotonic()
    play(Bounce(), duration=10, terminal=Terminal(fake_tty.out, fake_tty.inp))
    assert time.monotonic() - start < 1.0
    assert fake_tty.output().endswith(LEAVE)


def test_cli_play_piped_prints_frame(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "30")
    monkeypatch.setenv("LINES", "6")
    assert main(["play", "bounce", "--duration", "1"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("╭─────────╮")
    assert "\x1b" not in out


@pytest.mark.parametrize("args", [["play", "--fps", "0"], ["play", "--duration", "-1"], []])
def test_cli_rejects_bad_args(args, capsys):
    with pytest.raises(SystemExit) as info:
        main(args)
    assert info.value.code == 2
