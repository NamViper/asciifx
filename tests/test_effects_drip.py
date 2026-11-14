"""Drip: drops swell, run down and pool, as a pure function of t (fixed times)."""

import copy

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.effects import Drip
from asciifx.effects.drip import DRIP_HEAD, DRIP_POOL, DRIP_TRAIL

W, H = 40, 20
ART = (200, 200, 200)


def art() -> Grid:
    """A slab of braille in rows 2..6."""
    g = Grid(W, H)
    for y in range(2, 7):
        for x in range(8, 32):
            g.set(x, y, Cell("⣿", ART))
    return g


def one(**kw) -> Drip:
    """A single drop from (20, 7), swelling 1.0 .. 1.4, then running."""
    return Drip(**{"start": 1.0, "duration": 5.0, "origins": ((20, 7),), "stagger": 0.0, "form": 0.4,
                   "speed": 4.0, "accel": 8.0, "fade": 0.5, "seed": 3, **kw})  # fmt: skip


def red_rows(g: Grid, base: Grid) -> list[int]:
    return sorted({i // g.width for i, (c, b) in enumerate(zip(g.cells, base.cells)) if c != b})


def test_identity_outside_the_window_pure_and_unmutated():
    e, g = one(), art()
    before = copy.deepcopy(g)
    for t in (0.0, 0.999, 6.0, 9.0):
        out = e.apply(g, t)
        assert out == g and out is not g
    for t in (1.2, 2.0, 4.0):
        assert e.apply(g, t) == e.apply(g, t) == one().apply(g, t)
    assert g == before


def test_schedule():
    e, g = one(), art()
    (d,) = e.drops(g)
    assert (d.x, d.y, d.launch, d.run) == (20, 7, 1.0, pytest.approx(1.4))
    assert e.fall(0.5) == pytest.approx(4.0 * 0.5 + 0.5 * 8.0 * 0.25)
    rows = H - 1 - 7
    assert e.fall(d.bottom - d.run) == pytest.approx(rows)
    assert len(d.sway) == rows + 1 and all(abs(c - 2 * 20) <= 2 for c in d.sway)
    assert 2 <= d.pool <= e.pool
    many = Drip(start=1.0, count=4, stagger=0.9, seed=5).drops(g)
    assert len(many) == 4 and all(1.0 <= m.launch <= 1.9 for m in many)


def test_swelling_then_running_down():
    e, g = one(), art()
    def drop_dots(out: Grid) -> int:  # braille dots of the swelling drop (row 7)
        (c,) = [c for c in out.cells[7 * W : 8 * W] if c.fg == DRIP_HEAD]
        return bin(ord(c.ch) - 0x2800).count("1")

    swell = e.apply(g, 1.05)
    assert red_rows(swell, g) == [7]
    assert drop_dots(swell) == 1 and drop_dots(e.apply(g, 1.35)) == 4  # it grows down the cell
    t = 1.9  # 0.5 s of running: 4 rows
    out = e.apply(g, t)
    rows = red_rows(out, g)
    assert rows[0] == 7 and rows[-1] == 7 + int((4 * e.fall(0.5) + 3) // 4)
    head_row = rows[-1]
    heads = [c for c in out.cells[head_row * W : (head_row + 1) * W] if c.fg == DRIP_HEAD]
    trail = [c for y in rows[:-2] for c in out.cells[y * W : (y + 1) * W] if c.fg == DRIP_TRAIL]
    assert heads and trail  # bright drop, darker streak behind it
    assert all(0x2801 <= ord(c.ch) <= 0x28FF for c in heads + trail)


def test_pool_spreads_at_the_bottom_then_fades():
    e, g = one(), art()
    (d,) = e.drops(g)
    pools = []
    for t in (d.bottom + 0.05, d.bottom + 0.5, d.bottom + 2.0):
        out = e.apply(g, t)
        pools.append([x for x in range(W) if out.get(x, H - 1).fg == DRIP_POOL])
    assert 0 < len(pools[0]) < len(pools[1]) <= len(pools[2]) == 2 * d.pool + 1
    assert all(out.get(x, H - 1).ch in "⣀⣤⣶" for x in pools[2])
    mid = d.sway[-1] // 2
    assert out.get(mid, H - 1).ch == "⣶" and out.get(pools[2][0], H - 1).ch == "⣀"  # thick middle
    fading = e.apply(g, 5.75)
    assert all(c.fg[0] < DRIP_POOL[0] for c in fading.cells[(H - 1) * W :] if c != BLANK)


def test_stains_the_art_it_runs_over():
    g = art()
    e = one(origins=((20, 3),), stain=True)
    out = e.apply(g, 1.7)
    assert out.get(20, 4).ch == "⣿" and out.get(20, 4).fg in (DRIP_HEAD, DRIP_TRAIL)  # dots kept, red
    clean = one(origins=((20, 3),), stain=False).apply(g, 1.7)
    assert clean.get(20, 4).ch != "⣿"  # without stain the streak replaces the art
    letters = Grid.from_text("AAA\nAAA\nAAA\n   \n   ", fg=ART)
    out = Drip(origins=((1, 0),), stagger=0.0, form=0.1, seed=1).apply(letters, 0.5)
    assert out.get(1, 1).ch == "A" and out.get(1, 1).fg != ART  # other glyphs keep their shape


def test_auto_origins_hang_under_the_art():
    g = art()
    e = Drip(count=3, seed=4)
    origins = e.auto_origins(g)
    assert len(origins) == 3 and all(y == 7 and 8 <= x < 32 for x, y in origins)
    xs = sorted(x for x, _ in origins)
    assert all(b - a >= 3 for a, b in zip(xs, xs[1:]))
    assert Drip().auto_origins(Grid(W, H)) == ()
    full = Grid.filled(5, 3, Cell("x"))
    assert all(y == 2 for _, y in Drip(count=1).auto_origins(full))  # no room below: the bottom row


def test_tiny_grids():
    e = Drip(start=0.0, duration=2.0, stagger=0.0, form=0.1)
    for g in (Grid(0, 0), Grid(1, 1), Grid.filled(1, 1, Cell("@")), Grid.filled(3, 1, Cell("@"))):
        for t in (0.05, 0.5, 1.5):
            out = e.apply(g, t)
            assert (out.width, out.height) == (g.width, g.height)
