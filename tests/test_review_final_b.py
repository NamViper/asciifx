"""Regression tests from the final review of core/, effects/, sources/ and fonts.py:
Grid.from_dict validation, color ranges, a zero-width video size, zero-width
characters in big text, effect edge cases, and the numpy-free imports."""

import json
import subprocess
import sys

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.effects import EFFECTS, Lightning, noise
from asciifx.fonts import render_text, text_width
from asciifx.sources.video import fit_video, parse_probe
from asciifx.startup import load_logo
from asciifx.term.ansi import encode_frame
from asciifx.term.color import ColorMode

ESC = "\x1b"


def _dict(palette, cells, width=1, height=1):
    return {"width": width, "height": height, "palette": palette, "cells": cells}


# -- Grid.from_dict ------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        # A string color component was put verbatim into the SGR sequence: a raw
        # escape sequence (here OSC 2, set the window title) reached the terminal.
        _dict([["a", ["0m" + ESC + "]2;owned\x07", 0, 0], None]], [0]),
        _dict([["a", "red", None]], [0]),  # tuple("red") == ("r", "e", "d")
        # Out of 0..255: rgb_to_256 raised IndexError in the middle of a frame.
        _dict([["a", [999, 0, 0], None]], [0]),
        _dict([["a", None, [0, -5, 0]]], [0]),
        _dict([["a", [1, 2], None]], [0]),
        _dict([["a", [1, 2, 3, 4], None]], [0]),
        _dict([["a", [1.5, 2, 3], None]], [0]),
        _dict([["a", [True, 2, 3], None]], [0]),
        # Glyphs: not a string (crashed the renderer), or not one character.
        _dict([[1, None, None]], [0]),
        _dict([["", None, None]], [0]),
        _dict([["ab", None, None]], [0]),
        # Indexes: negative ones silently picked from the end of the palette.
        _dict([["a", None, None]], [-1]),
        _dict([["a", None, None]], [1]),
        _dict([["a", None, None]], [True]),
        _dict([["a", None, None]], ["0"]),
        # Sizes: a float size passed Grid's cell count check (1.5 * 2 == 3).
        _dict([["a", None, None]], [0, 0, 0], width=1.5, height=2),
        _dict([["a", None, None]], [0], width="1"),
        _dict([], [], width=-1, height=0),
        # An empty cell list became a blank grid of any size, allocated in full.
        _dict([], [], width=3, height=2),
        _dict([["a", None, None]], [0], width=3, height=2),
    ],
)
def test_from_dict_rejects_malformed_data(data):
    with pytest.raises(ValueError):
        Grid.from_dict(data)


def test_from_dict_still_round_trips():
    g = Grid(3, 2)
    g.put_text(0, 0, "a▀", fg=(255, 0, 0), bg=(0, 0, 0))
    g.set(2, 1, Cell("x", None, (0, 0, 0)))
    g.set(1, 1, Cell("y", (255, 255, 255), (1, 2, 3)))
    for grid in (g, Grid(0, 0), Grid(3, 0), Grid(0, 2), Grid.filled(2, 2, BLANK)):
        assert Grid.from_dict(json.loads(json.dumps(grid.to_dict()))) == grid


def test_poisoned_logo_cache_is_regenerated_not_drawn(tmp_path):
    img = tmp_path / "logo.png"
    img.write_bytes(b"fake")
    convert = lambda p, w, h, params: Grid.from_text("ok", fg=(1, 2, 3))  # noqa: E731
    load_logo(img, 2, 1, {}, cache=tmp_path, convert=convert)
    (cached,) = tmp_path.glob("logo-*.json")
    data = json.loads(cached.read_text())
    data["palette"][0][1] = ["0m" + ESC + "]2;owned\x07", 0, 0]
    cached.write_text(json.dumps(data))
    grid = load_logo(img, 2, 1, {}, cache=tmp_path, convert=convert)
    assert grid == Grid.from_text("ok", fg=(1, 2, 3))
    assert "owned" not in encode_frame(grid, ColorMode.TRUECOLOR)


# -- effects -------------------------------------------------------------------------


def _art(w: int, h: int) -> Grid:
    g = Grid(w, h)
    for y in range(h):
        for x in range(w):
            if noise(7, x, y) < 0.6:
                g.set(x, y, Cell("#", (200, 40, 40)))
    return g


def test_lightning_afterglow_above_one_keeps_colors_in_range():
    """afterglow=2 lit the art past white: RGB up to 488, which crashed 256-color output."""
    grid = _art(12, 6)
    effect = Lightning(afterglow=2.0, flash=0.05, leader=0.1, duration=1.0)
    for k in range(40):
        out = effect.apply(grid, k * 0.025)
        for c in out.cells:
            for color in (c.fg, c.bg):
                assert color is None or all(0 <= v <= 255 for v in color), (k, c)
        encode_frame(out, ColorMode.ANSI256)
    # Within range, afterglow still scales the glow.
    t = 0.2
    weak = Lightning(afterglow=0.3, flash=0.05, leader=0.1).apply(grid, t)
    strong = Lightning(afterglow=1.0, flash=0.05, leader=0.1).apply(grid, t)
    assert weak != strong


EDGE_GRIDS = {"column": _art(1, 7), "row": _art(7, 1), "full_column": Grid.filled(1, 5, Cell("@"))}


@pytest.mark.parametrize("grid_name", EDGE_GRIDS)
@pytest.mark.parametrize("name", list(EFFECTS))
def test_effects_on_one_cell_wide_grids_and_far_times(name, grid_name):
    """1xN / Nx1 grids and t = +-1e7 (the contract tests use 1x1 and t near the window)."""
    effect, grid = EFFECTS[name](), EDGE_GRIDS[grid_name]
    before = grid.copy()
    end = effect.end_for(grid)
    ts = [-1e7, -1.0] + [end * k / 15 for k in range(16)] + [end + 1.0, 1e7]
    for t in ts:
        out = effect.apply(grid, t)
        assert (out.width, out.height) == (grid.width, grid.height)
        assert all(len(c.ch) == 1 for c in out.cells)
        assert effect.apply(grid, t) == out
        if t >= end:
            assert out == grid
    assert grid == before


# -- sources -------------------------------------------------------------------------


@pytest.mark.parametrize("size", ["0x480", "0x0", "640x0"])
def test_zero_sized_video_stream_does_not_divide_by_zero(size):
    for rotation in ("", "      displaymatrix: rotation of -90.00 degrees\n"):
        text = f"Input #0, mov, from 'x':\n  Stream #0:0: Video: h264, yuv420p, {size}, 25 fps\n{rotation}"
        info = parse_probe(text)
        assert info.aspect == 1.0
        cols, (pw, ph) = fit_video(info.aspect, 80, 24, "half")
        assert cols >= 1 and pw >= 1 and ph >= 1


# -- fonts ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,plain",
    [
        ("❤️", "❤"),  # emoji presentation selector: a second tofu box
        ("I︎", "I"),  # text presentation selector
        ("A‍B", "AB"),  # zero-width joiner
        ("A​B", "AB"),  # zero-width space
        ("﻿HI", "HI"),  # byte order mark from a file
        ("A­B", "AB"),  # soft hyphen
        ("é", "é"),  # combining accents still compose
    ],
)
def test_zero_width_characters_draw_nothing(text, plain):
    for font in ("block", "small"):
        assert text_width(text, font) == text_width(plain, font)
        assert render_text(text, font) == render_text(plain, font)


# -- imports -------------------------------------------------------------------------


def test_cli_and_every_effect_import_without_numpy_or_pillow():
    code = (
        "import sys, asciifx, asciifx.cli, asciifx.fonts, asciifx.sources.textframes;"
        "from asciifx.effects import EFFECTS, Morph, Reverse, PALETTES;"
        "[EFFECTS[n] for n in EFFECTS];"
        "assert 'numpy' not in sys.modules and 'PIL' not in sys.modules, 'eager import'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
