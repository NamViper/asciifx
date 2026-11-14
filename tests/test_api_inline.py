"""Inline animation (banner / startup) end to end on a pseudo-terminal."""

import io
import threading
import time

import pytest

from vt import VT

from asciifx.api import animate_inline, banner
from asciifx.core import Grid
from asciifx.startup import build, run
from asciifx.term import ColorMode, Terminal

INFO = [("user", "neo@zion"), ("os", "Ubuntu"), ("kernel", "6.18"), ("uptime", "1m"), ("date", "today")]


def inline_term(fake_tty):
    return Terminal(fake_tty.out, fake_tty.inp, color_mode=ColorMode.TRUECOLOR, fullscreen=False)


def test_animate_inline_leaves_final_frame_and_cursor_below(fake_tty):
    fake_tty.resize(40, 12)
    frames = lambda t: Grid.from_text(f"t={t:.2f}")  # noqa: E731
    skipped = animate_inline(frames, 0.2, terminal=inline_term(fake_tty))
    vt = VT(40, 12).feed(fake_tty.output())
    assert not skipped
    assert vt.line(0).rstrip() == "t=0.20"
    assert vt.cursor == (0, 1)
    assert "\x1b[?1049h" not in fake_tty.output()  # never the alternate screen


def test_any_key_skips_to_final_frame(fake_tty):
    fake_tty.resize(40, 12)
    threading.Timer(0.15, fake_tty.send, args=(b"\r",)).start()
    start = time.monotonic()
    skipped = animate_inline(lambda t: Grid.from_text(f"t={t:.1f}"), 10.0, terminal=inline_term(fake_tty))
    assert skipped and time.monotonic() - start < 1.0
    assert VT(40, 12).feed(fake_tty.output()).line(0).rstrip() == "t=10.0"


def test_animate_inline_non_tty_prints_final_frame_only():
    buf = io.StringIO()
    animate_inline(lambda t: Grid.from_text(f"t={t:.1f}"), 2.0, terminal=Terminal(out=buf, fullscreen=False))
    assert buf.getvalue() == "t=2.0\n"


def test_banner_without_effect(fake_tty):
    fake_tty.resize(40, 12)
    banner("hello\nworld", terminal=inline_term(fake_tty))
    vt = VT(40, 12).feed(fake_tty.output())
    assert [vt.line(0).rstrip(), vt.line(1).rstrip()] == ["hello", "world"]


def test_startup_banner_end_to_end(fake_tty, monkeypatch):
    monkeypatch.setattr("asciifx.startup.sysinfo", lambda now=None: INFO)
    fake_tty.resize(100, 30)
    run(None, duration=0.2, terminal=inline_term(fake_tty))
    vt = VT(100, 30).feed(fake_tty.output())
    expected = build(100, 30, None, info=INFO).final()
    assert vt.grid(0, 0, expected.width, expected.height) == expected
    assert vt.cursor == (0, expected.height)


@pytest.mark.parametrize("data", [b"not an image at all", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00"])
def test_startup_with_a_corrupt_logo_shows_the_text_banner(fake_tty, monkeypatch, tmp_path, data):
    """Garbage or a truncated PNG: every new shell printed "startup banner failed"."""
    monkeypatch.setattr("asciifx.startup.sysinfo", lambda now=None: INFO)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("ASCIIFX_DEBUG", raising=False)
    bad = tmp_path / "logo.png"
    bad.write_bytes(data)
    fake_tty.resize(100, 30)
    run(bad, duration=0.2, terminal=inline_term(fake_tty))
    vt = VT(100, 30).feed(fake_tty.output())
    expected = build(100, 30, None, info=INFO).final()
    assert vt.grid(0, 0, expected.width, expected.height) == expected
    with pytest.raises(OSError):  # build itself still reports it (summon --logo shows the error)
        build(100, 30, bad, info=INFO, cache=tmp_path)
    monkeypatch.setenv("ASCIIFX_DEBUG", "1")
    with pytest.raises(OSError):
        run(bad, duration=0.2, terminal=inline_term(fake_tty))


def test_startup_skips_when_terminal_too_small(fake_tty, monkeypatch):
    monkeypatch.setattr("asciifx.startup.sysinfo", lambda now=None: INFO)
    fake_tty.resize(20, 6)
    run(None, duration=0.2, terminal=inline_term(fake_tty))
    assert fake_tty.output() == ""


def test_banner_duration_comes_from_effect_end_for_grid():
    """Typewriter's length depends on the text, so banner() must use end_for(grid)."""
    from asciifx.effects import Typewriter

    buf = io.StringIO()
    banner("ACCESS GRANTED", effect=Typewriter(cps=10), terminal=Terminal(out=buf, fullscreen=False))
    assert buf.getvalue() == "ACCESS GRANTED\n"


def test_fx_demo_works_for_every_effect():
    from asciifx.cli import _EffectDemo
    from asciifx.effects import EFFECTS

    for name, cls in EFFECTS.items():
        demo = _EffectDemo(cls())
        for t in (0.0, 0.5, 2.0, 10.0):
            assert demo.frame(t, 60, 12).width == 60, name


def test_banner_with_font_wraps_to_terminal_width(monkeypatch):
    from asciifx.fonts import render_text

    monkeypatch.setenv("COLUMNS", "30")
    buf = io.StringIO()
    banner("HELLO WORLD", font="small", terminal=Terminal(out=buf, fullscreen=False))
    expected = render_text("HELLO WORLD", "small", max_width=29).to_text()
    assert buf.getvalue() == expected + "\n"
    assert max(len(line) for line in buf.getvalue().splitlines()) <= 29
