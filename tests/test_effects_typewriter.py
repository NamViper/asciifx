"""Typewriter: fully hand-computed frames at fixed t."""

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.effects import Typewriter

RED = (255, 0, 0)
CYAN = (0, 255, 255)

# Row 0 types 4 cells ("ab c", the inner space takes time too), row 1 is empty
# (no time), row 2 types 2 cells (its leading blanks are skipped): 6 in total.
TEXT = "ab c\n\n  xy"


def grid() -> Grid:
    return Grid.from_text(TEXT, fg=RED)


def tw(**kw) -> Typewriter:
    return Typewriter(**{"start": 1.0, "cps": 10.0, **kw})


@pytest.mark.parametrize(
    "t,expected",
    [
        (0.0, "\n\n"),  # before start: nothing, not even the cursor
        (0.99, "\n\n"),
        (1.0, "█\n\n"),  # typing starts: cursor on the first cell
        (1.1, "a█\n\n"),
        (1.25, "ab█\n\n"),  # int(0.25 * 10) = 2 cells
        (1.3, "ab █\n\n"),  # the inner space has been typed
        (1.4, "ab c\n\n  █"),  # row 0 done; the cursor can't go past the edge -> next row
        (1.5, "ab c\n\n  x█"),
        (1.59, "ab c\n\n  x█"),
        (1.6, TEXT),  # end = 1.0 + 6 / 10
        (5.0, TEXT),
    ],
)
def test_frames(t, expected):
    assert tw().apply(grid(), t).to_text() == expected


def test_cursor_after_last_typed_cell_waits_at_end_of_row():
    g = Grid.from_text("ab \ncd ", fg=RED)  # trailing blank column leaves room for the cursor
    assert tw().apply(g, 1.2).to_text() == "ab█\n"  # row 0 typed, row 1 not started
    assert tw().apply(g, 1.3).to_text() == "ab\nc█"


def test_colors_and_cursor_color():
    out = tw().apply(grid(), 1.25)
    assert out.get(0, 0) == Cell("a", RED) and out.get(1, 0) == Cell("b", RED)
    assert out.get(2, 0) == Cell("█", RED)  # cursor takes the last typed cell's fg
    assert out.get(3, 0) == BLANK
    assert all(c == BLANK for c in out.cells[4:])
    assert tw(cursor_fg=CYAN).apply(grid(), 1.25).get(2, 0) == Cell("█", CYAN)


def test_cursor_without_fg_and_disabled_cursor():
    plain = Grid.from_text(TEXT)
    assert tw().apply(plain, 1.25).get(2, 0) == Cell("█")
    assert tw(cursor=None).apply(grid(), 1.25).to_text() == "ab\n\n"
    assert tw(cursor="_").apply(grid(), 1.25).to_text() == "ab_\n\n"


def test_background_cells_count_as_content():
    g = Grid(3, 1, [Cell(" ", None, RED), BLANK, Cell("z", RED)])
    out = tw().apply(g, 1.1)
    assert out.cells == [Cell(" ", None, RED), Cell("█"), BLANK]
    assert tw().end_for(g) == pytest.approx(1.3)


def test_end_in_cps_mode_depends_on_grid():
    assert tw().end_for(grid()) == pytest.approx(1.6)
    with pytest.raises(ValueError, match="end_for"):
        tw().end


def test_duration_overrides_cps():
    fixed = tw(duration=0.6, cps=999.0)  # 6 cells in 0.6 s = 10 cps, like tw()
    assert fixed.end == fixed.end_for(grid()) == pytest.approx(1.6)
    for t in (0.5, 1.0, 1.25, 1.4, 1.5, 1.6):
        assert fixed.apply(grid(), t) == tw().apply(grid(), t)
    assert tw(duration=0.0).apply(grid(), 0.5).to_text() == "\n\n"
    assert tw(duration=0.0).apply(grid(), 1.0) == grid()


def test_empty_and_blank_grids():
    assert tw().apply(Grid(0, 0), 1.2) == Grid(0, 0)
    assert tw().apply(Grid(4, 2), 1.2) == Grid(4, 2)
    assert tw().end_for(Grid(4, 2)) == 1.0


def test_rejects_non_positive_cps():
    with pytest.raises(ValueError):
        Typewriter(cps=0)
