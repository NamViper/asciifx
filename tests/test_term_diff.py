"""Diff-based frame encoding (fullscreen and inline), checked with a VT emulator."""

import random
import re

import pytest
from vt import VT, VTError

from asciifx.core import Cell, Grid
from asciifx.core.grid import BLANK
from asciifx.term import ColorMode, rgb_to_256
from asciifx.term.ansi import (
    AUTOWRAP_OFF,
    AUTOWRAP_ON,
    CURSOR_SHOW,
    ERASE_SCREEN,
    REWRITE_GAP,
    RESET,
    SYNC_BEGIN,
    SYNC_END,
    encode_frame,
    leave_inline,
    reserve_region,
    sgr,
)

TC = ColorMode.TRUECOLOR
RED, GREEN, DARK = (255, 0, 0), (0, 255, 0), (10, 20, 30)
_MOVE = re.compile(r"\x1b\[\d+(?:;\d+)?[HG]")


def moves(encoded: str) -> list[str]:
    return _MOVE.findall(encoded)


# -- unit tests --------------------------------------------------------------


def test_unchanged_frame_encodes_to_nothing():
    g = Grid.from_text("hello\nworld", fg=RED)
    assert encode_frame(g, TC, g.copy()) == ""
    assert encode_frame(g, TC, Grid.from_text("hello\nworld", fg=RED)) == ""  # equal, not identical
    assert encode_frame(g, TC, g.copy(), inline=True) == ""


def test_one_changed_cell_is_one_move_plus_that_cell():
    prev = Grid(10, 3)
    new = prev.copy()
    new.set(5, 1, Cell("x", RED))
    cell = sgr(RED, None, TC) + "x"
    assert encode_frame(new, TC, prev) == SYNC_BEGIN + "\x1b[2;6H" + cell + RESET + SYNC_END
    # inline: back to the region's saved top-left, down 1 line, to column 6
    assert encode_frame(new, TC, prev, inline=True) == SYNC_BEGIN + "\x1b8\x1b[1B\x1b[6G" + cell + RESET + SYNC_END


def test_inline_move_on_first_row_has_no_cursor_down():
    prev = Grid(10, 3)
    new = prev.copy()
    new.set(5, 0, Cell("x"))
    # after DECRC the colors are the default ones saved with the region: no SGR needed
    assert encode_frame(new, TC, prev, inline=True) == SYNC_BEGIN + "\x1b8\x1b[6G" + "x" + RESET + SYNC_END


def test_changes_within_the_gap_share_one_move():
    prev = Grid(20, 2)
    new = prev.copy()
    new.set(2, 0, Cell("x"))
    new.set(3 + REWRITE_GAP, 0, Cell("y"))  # exactly REWRITE_GAP unchanged cells in between
    out = encode_frame(new, TC, prev)
    assert moves(out) == ["\x1b[1;3H"]
    assert "x" + " " * REWRITE_GAP + "y" in out


def test_changes_far_apart_get_two_moves():
    prev = Grid(20, 2)
    new = prev.copy()
    new.set(2, 0, Cell("x"))
    new.set(4 + REWRITE_GAP, 0, Cell("y"))  # one cell more than the gap
    out = encode_frame(new, TC, prev)
    assert moves(out) == ["\x1b[1;3H", f"\x1b[{5 + REWRITE_GAP}G"]  # 2nd move: same row, CHA
    assert "x" + " " not in out


def test_unknown_cursor_at_row_start_is_not_a_small_gap():
    prev = Grid(10, 3)
    new = prev.copy()
    new.set(0, 0, Cell("a"))
    new.set(1, 1, Cell("b"))
    new.set(2, 2, Cell("c"))
    out = encode_frame(new, TC, prev)
    assert moves(out) == ["\x1b[1;1H", "\x1b[2;2H", "\x1b[3;3H"]
    out = encode_frame(new, TC, prev, inline=True)
    assert out.count("\x1b8") == 3 and moves(out) == ["\x1b[1G", "\x1b[2G", "\x1b[3G"]


def test_sgr_only_on_color_change_in_diff():
    prev = Grid(10, 1)
    new = prev.copy()
    new.put_text(0, 0, "ab cd", fg=RED)
    out = encode_frame(new, TC, prev)
    assert out.count("\x1b[38;2;255;0;0") == 1
    assert out == SYNC_BEGIN + "\x1b[1;1H" + sgr(RED, None, TC) + "ab cd" + RESET + SYNC_END


def test_size_change_or_no_prev_is_full_redraw_with_clear():
    new = Grid.from_text("abcd\nefgh")
    for prev in (None, Grid(3, 2), Grid(4, 3)):
        out = encode_frame(new, TC, prev)
        assert out.startswith(SYNC_BEGIN + RESET + ERASE_SCREEN)
        assert "abcd" in out and "efgh" in out


def test_inline_full_redraw_erases_its_rows_not_the_screen():
    out = encode_frame(Grid.from_text("ab\ncd"), TC, inline=True)
    assert out == (
        SYNC_BEGIN
        + "\x1b8\x1b[1G" + RESET + "\x1b[2K" + "ab"
        + "\x1b8\x1b[1B\x1b[1G" + RESET + "\x1b[2K" + "cd"
        + RESET + SYNC_END
    )  # fmt: skip


def test_reserve_and_leave_inline_sequences():
    assert reserve_region(3) == RESET + "\n\n\n" + "\x1b[3A" + "\x1b7"
    assert reserve_region(0) == RESET + "\x1b7"  # CUU 0 would mean 1: omitted
    assert leave_inline(3) == SYNC_END + "\x1b8\x1b[3B\r" + RESET + AUTOWRAP_ON + CURSOR_SHOW
    assert leave_inline(0) == SYNC_END + "\x1b8\r" + RESET + AUTOWRAP_ON + CURSOR_SHOW
    assert leave_inline(None) == SYNC_END + RESET + AUTOWRAP_ON + CURSOR_SHOW


@pytest.mark.parametrize("data", ["\x1b[5n", "\x07", "\x1b[3J", "\x1b[1m", "\x1bc", "\x1b[38;2;1;2m", "\x1b8", "\x1b"])
def test_vt_rejects_unknown_sequences(data):
    with pytest.raises(VTError):
        VT(5, 2).feed(data)


# -- randomized property tests ---------------------------------------------

CHARS = "ab █·"
COLORS = [None, RED, GREEN, DARK]
W, H = 14, 10


def random_cell(rng: random.Random) -> Cell:
    return Cell(rng.choice(CHARS), rng.choice(COLORS), rng.choice(COLORS))


def random_grid(rng: random.Random, width: int, height: int) -> Grid:
    return Grid(width, height, [random_cell(rng) for _ in range(width * height)])


def mutate(rng: random.Random, grid: Grid) -> Grid:
    g = grid.copy()
    n = len(g.cells)
    if not n:
        return g
    for _ in range(rng.choice([0, 1, 2, 3, rng.randrange(n + 1)])):
        i = rng.randrange(n)
        g.cells[i] = random_cell(rng)
    if rng.random() < 0.3:  # a contiguous run, possibly crossing rows
        i = rng.randrange(n)
        for k in range(i, min(n, i + rng.randint(1, 8))):
            g.cells[k] = random_cell(rng)
    if rng.random() < 0.3:  # equal but not identical cells must count as unchanged
        g.cells = [Cell(c.ch, c.fg, c.bg) for c in g.cells]
    return g


def expected(grid: Grid, mode: ColorMode) -> Grid:
    if mode is ColorMode.TRUECOLOR:
        return grid

    def to256(c):
        return None if c is None else rgb_to_256(*c)

    return Grid(grid.width, grid.height, [Cell(c.ch, to256(c.fg), to256(c.bg)) for c in grid.cells])


def next_grid(rng: random.Random, prev: Grid | None, height: int | None = None) -> Grid:
    if prev is None or rng.random() < 0.25:
        return random_grid(rng, rng.randint(0, W), rng.randint(0, H) if height is None else height)
    return mutate(rng, prev)


@pytest.mark.parametrize("mode", list(ColorMode))
def test_fullscreen_diffs_reproduce_every_frame(mode):
    rng = random.Random(f"fullscreen-{mode}")
    for _ in range(200):
        vt = VT(W, H).feed(AUTOWRAP_OFF)
        # leftovers on screen and non-default colors: a missing reset would leak via BCE
        vt.feed("junk junk\r\nmore\x1b[38;2;1;2;3;48;2;4;5;6m")
        prev = None
        for _ in range(5):
            grid = next_grid(rng, prev)
            data = encode_frame(grid, mode, prev)
            if prev is not None and grid == prev:
                assert data == ""
            vt.feed(data)
            assert vt.grid(0, 0, grid.width, grid.height) == expected(grid, mode)
            for y in range(H):  # outside the grid: blank since the last size change
                start = grid.width if y < grid.height else 0
                assert vt.lines[y][start:] == [BLANK] * (W - start)
            prev = grid


@pytest.mark.parametrize("mode", list(ColorMode))
def test_inline_diffs_reproduce_every_frame_below_existing_text(mode):
    rng = random.Random(f"inline-{mode}")
    for _ in range(200):
        vt = VT(W, H).feed(AUTOWRAP_OFF)
        for i in range(rng.randrange(2 * H)):  # sometimes more lines than fit: already scrolled
            vt.feed(f"\x1b[38;2;9;9;9mline {i}\x1b[0m\r\n")
        vt.feed(rng.choice(["", "\x1b[38;2;1;2;3;48;2;4;5;6m"]))  # colors the shell left set
        before = [list(line) for line in vt.lines]
        start_row = vt.y
        rows = rng.randint(0, H - 1)  # Terminal keeps the region below the screen height
        vt.feed(reserve_region(rows))
        top = min(start_row, H - 1 - rows)
        scroll = start_row - top
        prev = None
        for _ in range(5):
            grid = next_grid(rng, prev, rows)
            vt.feed(encode_frame(grid, mode, prev, inline=True))
            assert vt.grid(0, top, grid.width, rows) == expected(grid, mode)
            for y in range(top):
                assert vt.lines[y] == before[y + scroll]  # text above the region untouched
            for y in range(top, H):
                start = grid.width if y < top + rows else 0
                assert vt.lines[y][start:] == [BLANK] * (W - start), (y, top, rows)
            prev = grid
        vt.feed(leave_inline(rows))
        assert vt.cursor == (0, top + rows)  # on the line just below the region
        assert vt.grid(0, top, prev.width, rows) == expected(prev, mode)  # last frame stays
