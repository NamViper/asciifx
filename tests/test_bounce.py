"""The bouncing-sprite demo (`asciifx play bounce`), checked at fixed t."""

import pytest

from asciifx.core import CELL_ASPECT, Cell
from asciifx.sources import Bounce
from asciifx.sources.procedural import bounce_color

# 11x3 sprite on a 41x13 screen -> travel span is 30 columns x 10 rows.
W, H = 41, 13


@pytest.mark.parametrize(
    "t,expected",
    [
        (0.0, (0, 0, 0)),
        (1.0, (16, 8, 0)),  # 16 cols/s horizontally, 8 rows/s vertically
        (1.25, (20, 10, 1)),  # hits the bottom edge
        (2.0, (28, 4, 2)),  # bounced off the right and bottom edges
    ],
)
def test_position_at_fixed_t(t, expected):
    assert Bounce().position(t, W, H) == expected


def test_vertical_speed_corrects_for_cell_aspect():
    x, y, _ = Bounce(speed=10).position(1.0, W, H)
    assert y == x / CELL_ASPECT


def test_sprite_larger_than_screen_stays_put():
    assert Bounce().position(3.0, 5, 2) == (0, 0, 0)


def test_frame_at_fixed_t():
    src = Bounce(speed=4)
    grid = src.frame(1.0, 15, 5)  # span 4x2: at t=1 it sits in the bottom-right corner
    assert grid.to_text() == (
        "\n"
        "   ·     ·\n"
        "    ╭─────────╮\n"
        "    │ asciifx │\n"
        "   ·╰─────────╯"
    )
    assert grid.get(6, 3) == Cell("a", bounce_color(2), src.sprite_bg)  # recolored after 2 bounces
    assert grid.get(3, 1) == src.dot


def test_each_bounce_gets_a_new_color():
    colors = {bounce_color(n) for n in range(10)}
    assert len(colors) == 10
