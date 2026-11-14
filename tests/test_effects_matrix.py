"""Matrix rain at fixed t, checked cell by cell against the drop schedule."""

import math
import unicodedata

import pytest

from asciifx.core import BLANK, Grid
from asciifx.effects import BLOOD, Matrix
from asciifx.effects.base import gradient
from asciifx.effects.matrix import MATRIX_CHARSET, MATRIX_HEAD, MATRIX_TAIL

RED = (255, 0, 0)


def grid() -> Grid:
    return Grid.from_text("ABCDEF\nGHIJKL\nMNOPQR\nSTUVWX\nYZ0123", fg=RED)  # 6x5, no blanks


def rain(**kw) -> Matrix:
    return Matrix(**{"start": 1.0, "duration": 1.0, "tail_len": 3, **kw})


def expected_kinds(m: Matrix, g: Grid, t: float) -> list[list[str | int]]:
    """Per cell: "art" (input shows), "blank", or the trail index (0 = head)."""
    kinds: list[list[str | int]] = [
        ["blank" if m.reveal else "art"] * g.width for _ in range(g.height)
    ]
    for x, col in enumerate(m.columns(g.width, g.height)):
        for k, spawn in enumerate(col.spawns):
            head = math.floor((t - m.start - spawn) * col.speed) - 1
            if head < 0:
                break
            if k == 0 and m.reveal:
                for y in range(min(head, g.height)):
                    kinds[y][x] = "art"
            for y in range(max(0, head - col.tail + 1), min(head, g.height - 1) + 1):
                kinds[y][x] = head - y
    return kinds


def check_frame(m: Matrix, g: Grid, t: float) -> list[list[str | int]]:
    out = m.apply(g, t)
    cols = m.columns(g.width, g.height)
    kinds = expected_kinds(m, g, t)
    for y in range(g.height):
        for x in range(g.width):
            kind, got = kinds[y][x], out.get(x, y)
            if kind == "art":
                assert got == g.get(x, y), (x, y)
            elif kind == "blank":
                assert got == BLANK, (x, y)
            else:
                trail = (m.head, *gradient(m.tail, cols[x].tail - 1))
                assert got.ch in m.charset and got.fg == trail[kind], (x, y)
    return kinds


@pytest.mark.parametrize("t", [1.05, 1.15, 1.2, 1.3, 1.45, 1.6, 1.8, 1.95])
@pytest.mark.parametrize("drops", [1, 3])
def test_cells_follow_the_schedule(t, drops):
    check_frame(rain(drops=drops, duration=2.0), grid(), t)
    check_frame(rain(drops=drops, duration=2.0, reveal=False), grid(), t)


def test_snapshot():
    m, g = rain(drops=1), grid()
    kinds = check_frame(m, g, 1.3)
    assert kinds == [
        ["blank", "art", 2, 2, 2, 1],
        ["blank", "art", 1, 1, 1, 0],
        ["blank", 2, 0, 0, 0, "blank"],
        ["blank", 1, "blank", "blank", "blank", "blank"],
        ["blank", 0, "blank", "blank", "blank", "blank"],
    ]
    assert m.apply(g, 1.3).to_text() == ' Bｷﾜ9ｿ\n Hﾒ__=\n ﾙﾑｳ|\n ｴ\n ﾌ'
    heads = [(x, y) for y, row in enumerate(kinds) for x, k in enumerate(row) if k == 0]
    assert heads == [(5, 1), (2, 2), (3, 2), (4, 2), (1, 4)]
    assert m.apply(g, 1.3).get(1, 4).fg == MATRIX_HEAD
    assert m.apply(g, 1.3).get(2, 0).fg == MATRIX_TAIL[-1]  # tail of 3: head + 2 tail colors


def test_before_start():
    assert rain().apply(grid(), 0.99) == Grid(6, 5)  # reveal: nothing shown yet
    assert rain(reveal=False).apply(grid(), 0.5) == grid()
    assert rain().apply(grid(), 1.0) == Grid(6, 5)  # heads sit just above row 0 at spawn


@pytest.mark.parametrize("size", [(1, 1), (6, 5), (100, 25), (30, 60)])
@pytest.mark.parametrize("duration", [0.3, 0.8, 1.5, 4.0])
def test_every_trail_leaves_the_grid_by_the_end(size, duration):
    m = Matrix(duration=duration, spread=0.5, drops=3)
    w, h = size
    cols = m.columns(w, h)
    assert len(cols) == w
    for col in cols:
        assert 1 <= len(col.spawns) <= 3
        assert col.spawns[0] <= 0.5 * duration
        assert list(col.spawns) == sorted(col.spawns)
        for spawn in col.spawns:
            assert spawn + (h + col.tail) / col.speed <= duration + 1e-9


def test_rain_varies_per_column():
    cols = Matrix().columns(100, 25)
    assert len({round(c.speed, 6) for c in cols}) > 50
    assert len({round(c.spawns[0], 6) for c in cols}) > 50
    assert all(c.speed >= 24.0 * 0.7 - 1e-9 for c in cols)


def test_fully_revealed_after_the_rain_passed():
    m, g = rain(drops=1), grid()
    last_clear = max(s[0] + (5 + c.tail) / c.speed for c in m.columns(6, 5) for s in [c.spawns])
    assert m.apply(g, 1.0 + last_clear + 1e-6) == g


@pytest.mark.parametrize("t", [2.0, 3.0])
def test_identity_from_end(t):
    assert rain().end == 2.0
    assert rain().apply(grid(), t) == grid()


def test_palette_colors():
    m = rain(drops=1, **BLOOD.kwargs("matrix"))
    out = m.apply(grid(), 1.3)
    assert out.get(1, 4).fg == BLOOD.head
    assert out.get(2, 0).fg == BLOOD.tail[-1]
    assert m.tail == BLOOD.tail
    assert Matrix(tail=[(1, 2, 3)]).tail == ((1, 2, 3),)  # lists are accepted


def test_glyphs_are_single_cell_width():
    assert MATRIX_CHARSET
    for ch in MATRIX_CHARSET:
        assert unicodedata.east_asian_width(ch) not in ("W", "F"), ch
    assert all(0xFF66 <= ord(ch) <= 0xFF9D for ch in MATRIX_CHARSET if ord(ch) > 0x7F)


def test_rejects_empty_charset_or_tail():
    with pytest.raises(ValueError):
        Matrix(charset="")
    with pytest.raises(ValueError):
        Matrix(tail=())


def test_rain_glyphs_are_not_biased_toward_one_character():
    """Regression: the per-frame glyph hash once used the same salt as the per-cell
    hashes, so whenever a column's time bucket equalled a row index the two hashes
    cancelled and that cell always showed charset[0] (a visible line of 'ｦ')."""
    from collections import Counter

    from asciifx.core import Grid
    from asciifx.effects import Matrix

    m = Matrix(duration=2.0, reveal=False)
    counts: Counter[str] = Counter()
    for i in range(200):
        out = m.apply(Grid(40, 30), i / 100)
        counts.update(c.ch for c in out.cells if c.ch != " ")
    total = sum(counts.values())
    expected = total / len(m.charset)
    assert counts.most_common(1)[0][1] < 4 * expected
