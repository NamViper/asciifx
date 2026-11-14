"""Decode at fixed t: which cells are locked, what the scrambled ones look like."""

import pytest

from asciifx.core import Cell, Grid
from asciifx.effects import Decode, is_blank
from asciifx.effects.decode import DECODE_CHARSET, DECODE_HIGHLIGHT

RED = (255, 0, 0)
BG = (0, 0, 80)


def grid() -> Grid:
    g = Grid.from_text("#### \n#  ##\n ### ", fg=RED)  # 5x3, art spans columns 0..4
    g.set(4, 0, Cell(" ", None, BG))  # background-only cell: content, not blank
    return g


def dec(**kw) -> Decode:
    return Decode(**{"start": 1.0, "duration": 1.0, "rate": 10.0, **kw})


def check_frame(d: Decode, g: Grid, t: float) -> set[tuple[int, int]]:
    """Assert the per-cell rules at t; return the scrambled (unlocked) cells."""
    out = d.apply(g, t)
    fracs = d.lock_fractions(g)
    p = (t - d.start) / d.duration
    scrambled = set()
    for y in range(g.height):
        for x in range(g.width):
            src, got = g.get(x, y), out.get(x, y)
            if is_blank(src):
                assert is_blank(got)
            elif fracs[y * g.width + x] <= p:
                assert got == src
            else:
                assert got.ch in d.charset
                assert got.fg == (d.highlight if d.highlight is not None else src.fg)
                assert got.bg == src.bg
                scrambled.add((x, y))
    return scrambled


def test_pure_sweep_locks_column_by_column():
    d = dec(sweep=1.0)
    g = grid()
    fracs = d.lock_fractions(g)
    assert fracs == tuple(x / 4 for _ in range(3) for x in range(5))
    content = {(x, y) for y in range(3) for x in range(5) if not is_blank(g.get(x, y))}
    assert check_frame(d, g, 1.5) == {(x, y) for x, y in content if x >= 3}
    assert check_frame(d, g, 1.76) == {(x, y) for x, y in content if x == 4}


def test_random_order_lock_fractions():
    fracs = dec(sweep=0.0).lock_fractions(grid())
    assert len(fracs) == 15 and all(0.0 <= f < 1.0 for f in fracs)
    assert len(set(fracs)) == 15
    assert list(fracs[:5]) != sorted(fracs[:5])  # not a column sweep


def test_mixed_sweep_is_biased_left_to_right():
    fracs = dec(sweep=0.6).lock_fractions(grid())
    for i, f in enumerate(fracs):
        x = i % 5
        assert 0.6 * x / 4 <= f <= 0.6 * x / 4 + 0.4


def test_snapshot_mid_decode():
    d, g = dec(), grid()
    assert check_frame(d, g, 1.5) == {(3, 0), (4, 0), (3, 1), (4, 1), (3, 2)}
    out = d.apply(g, 1.5)
    assert out.to_text() == "###$|\n#  \\9\n ##M"
    assert out.get(4, 0).bg == BG and out.get(4, 0).ch != " "  # glyph drawn over the bg


def test_before_start_everything_is_scrambled():
    d, g = dec(), grid()
    content = {(x, y) for y in range(3) for x in range(5) if not is_blank(g.get(x, y))}
    assert check_frame(d, g, 0.5) == content
    assert d.apply(g, 0.5).to_text() == "#M2H=\nP  6V\n 4<%"


def test_glyphs_change_with_rate_buckets_only():
    d, g = dec(), grid()
    assert d.apply(g, 1.32) == d.apply(g, 1.38)  # same 1/10 s bucket, nothing locks in between
    assert d.apply(g, 1.38) != d.apply(g, 1.41)  # next bucket, still nothing locks


def test_highlight_none_keeps_cell_color():
    d = dec(highlight=None)
    check_frame(d, grid(), 1.5)
    assert d.apply(grid(), 1.5).get(3, 0).fg == RED
    assert dec().apply(grid(), 1.5).get(3, 0).fg == DECODE_HIGHLIGHT


def test_appear_materializes_from_nothing():
    g = grid()
    # appear=1: a cell appears exactly when it locks, so only locked cells show.
    assert dec(sweep=1.0, appear=1.0).apply(g, 1.5).to_text() == "###\n#\n ##"
    # appear=0.5 at p=0.3: x<=1 locked (lock x/4), x=2 (lock 0.5, appears at 0.25)
    # scrambled, x=3 (appears at 0.375) and x=4 (at 0.5) still hidden.
    out = dec(sweep=1.0, appear=0.5).apply(g, 1.3)
    assert out.to_text()[:2] == "##" and out.get(2, 0).ch in DECODE_CHARSET
    assert out.get(2, 0).fg == DECODE_HIGHLIGHT
    assert all(is_blank(out.get(x, y)) for y in range(3) for x in (3, 4))
    assert all(map(is_blank, dec(appear=0.5).apply(g, 0.9).cells))  # before start: nothing yet


def test_charset_and_single_column():
    g = Grid.from_text("x\ny")
    out = dec(charset="?").apply(g, 0.0)
    assert out.to_text() == "?\n?"
    assert dec(sweep=1.0).lock_fractions(g) == (0.0, 0.0)


def test_all_blank_grid():
    assert dec().apply(Grid(3, 2), 1.5) == Grid(3, 2)
    assert dec().lock_fractions(Grid(3, 2)) is None


@pytest.mark.parametrize("t", [2.0, 2.5])
def test_identity_from_end(t):
    assert dec().end == 2.0
    assert dec().apply(grid(), t) == grid()


def test_rejects_empty_charset():
    with pytest.raises(ValueError):
        Decode(charset="")
