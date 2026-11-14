import os

import pytest

from asciifx.core import Cell, Grid
from asciifx.term import ColorMode, KeyReader, detect_color_mode, parse_keys, rgb_to_256
from asciifx.term.ansi import SYNC_BEGIN, SYNC_END, encode_frame, sgr

RED, GRAY = (255, 0, 0), (40, 40, 40)


@pytest.mark.parametrize(
    "rgb,index",
    [
        ((0, 0, 0), 16),
        ((255, 255, 255), 231),
        ((255, 0, 0), 196),
        ((95, 135, 175), 67),
        ((128, 128, 128), 244),  # gray ramp beats the cube
        ((8, 8, 8), 232),
    ],
)
def test_rgb_to_256(rgb, index):
    assert rgb_to_256(*rgb) == index


@pytest.mark.parametrize(
    "env,mode",
    [
        ({"COLORTERM": "truecolor"}, ColorMode.TRUECOLOR),
        ({"COLORTERM": "24bit"}, ColorMode.TRUECOLOR),
        ({"COLORTERM": "yes"}, ColorMode.ANSI256),
        ({}, ColorMode.ANSI256),
    ],
)
def test_detect_color_mode(env, mode):
    assert detect_color_mode(env) is mode


def test_sgr_default_colors_and_both_modes():
    assert sgr(None, None, ColorMode.TRUECOLOR) == "\x1b[39;49m"
    assert sgr(RED, GRAY, ColorMode.TRUECOLOR) == "\x1b[38;2;255;0;0;48;2;40;40;40m"
    assert sgr(RED, None, ColorMode.ANSI256) == "\x1b[38;5;196;49m"


def test_encode_frame_exact():
    g = Grid(2, 2)
    g.put_text(0, 0, "ab", fg=RED)
    g.put_text(0, 1, "c")
    assert encode_frame(g, ColorMode.TRUECOLOR) == (
        SYNC_BEGIN
        + "\x1b[0m\x1b[2J"  # full redraw: reset colors, erase the screen
        + "\x1b[1;1H\x1b[38;2;255;0;0;49mab"
        + "\x1b[2;1H\x1b[39;49mc "
        + "\x1b[0m"
        + SYNC_END
    )


def test_encode_frame_only_emits_sgr_on_color_change():
    g = Grid.filled(10, 3, Cell("x", RED))
    out = encode_frame(g, ColorMode.ANSI256)
    assert out.count("38;5;196") == 1
    assert "x" * 10 in out


def test_parse_keys():
    assert parse_keys("a\x1b[A\x1bOB\r \x01é\x1b[15~\x1bx\x1b\r\x1b") == [
        "a", "up", "down", "enter", "space", "ctrl+a", "é", "\x1b[15~", "alt+x", "alt+enter", "esc",
    ]


def test_key_reader_reads_available_bytes_and_handles_split_utf8():
    r, w = os.pipe()
    try:
        reader = KeyReader(r)
        assert reader.poll(0) == []
        os.write(w, b"hi\xc3")
        assert reader.poll(1) == ["h", "i"]
        os.write(w, b"\xa9")
        assert reader.poll(1) == ["é"]
    finally:
        os.close(r)
        os.close(w)


def test_key_reader_eof_does_not_spin():
    r, w = os.pipe()
    os.close(w)
    try:
        reader = KeyReader(r)
        assert reader.poll(1) == []
        assert reader._eof
        assert reader.poll(0.01) == []
    finally:
        os.close(r)
