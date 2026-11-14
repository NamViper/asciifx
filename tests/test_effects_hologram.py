"""Hologram at fixed t: ghosts, scanlines, interlace, dropouts, envelope, registry."""

import copy
import math

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.effects import BLOOD, EFFECTS, HOLO_GHOST, Hologram, make

RED = (230, 30, 30)


def dot(w: int = 11, h: int = 5, x: int = 5, y: int = 2) -> Grid:
    g = Grid(w, h)
    g.set(x, y, Cell("X", RED))
    return g


def solid(w: int = 12, h: int = 20) -> Grid:
    return Grid.filled(w, h, Cell("#", (100, 100, 100)))


def quiet(h: Hologram, height: int, t: float = 0.0) -> float:
    """A time at or after t with no dropout and the ghost at its normal 1-cell offset."""
    from asciifx.effects.base import noise, time_bucket
    from asciifx.effects.hologram import _SALT

    while h.dropouts(height, t) or noise(h.seed, _SALT + 2, time_bucket(t - h.start, h.rate)) < 0.12:
        t += 1 / h.rate
    return t


def test_snapshot():
    g = Grid.from_text(" ab \n cd ", fg=RED)
    h = Hologram(seed=3)
    assert h.scanlines(2, 0.17) == pytest.approx((0.06, 4.06)) and h.dropouts(2, 0.17) == {}
    out = h.apply(g, 0.17)
    assert out.to_text() == "aabb\nccdd"
    assert out.get(0, 0) == Cell("a", (133, 16, 36)) and out.get(3, 1) == Cell("d", (0, 117, 133))  # ghosts
    assert out.get(1, 0) == Cell("a", (240, 134, 134))  # on the scanline: brightened
    assert out.get(2, 1) == Cell("d", (198, 70, 70))  # odd row, a row below: less so


def test_ghost_one_cell_left_red_right_cyan():
    h = Hologram(duration=None, seed=5)
    t = quiet(h, 5)
    out = h.apply(dot(), t)
    left, mid, right = out.get(4, 2), out.get(5, 2), out.get(6, 2)
    assert left.ch == mid.ch == right.ch == "X"
    assert left.fg[0] > 2 * left.fg[2] and right.fg[2] > 2 * right.fg[0]  # red / cyan
    assert sum(c.ch != " " for c in out.cells) == 3
    assert out.get(3, 2) == BLANK and out.get(7, 2) == BLANK


def test_ghost_never_covers_art_or_backgrounds():
    g = Grid.from_text("ab", fg=RED)
    g = Grid(4, 1, [Cell(" ", None, (9, 9, 9)), *g.cells, BLANK])
    h = Hologram(duration=None, seed=2)
    out = h.apply(g, quiet(h, 1))
    assert out.get(0, 0) == Cell(" ", None, (9, 9, 9))
    assert [out.get(x, 0).ch for x in (1, 2, 3)] == ["a", "b", "b"]


def test_scanlines_roll_down_and_brighten():
    h = Hologram(duration=None, intensity=1.0, seed=1)
    g = solid()
    t0 = quiet(h, 20, 0.2)

    def brightest(t: float) -> int:
        out = h.apply(g, t)
        return max(range(0, 20, 2), key=lambda y: out.get(0, y).fg[0])  # even rows: no interlace

    rows = [brightest(t0 + dt) for dt in (0.0, 0.1, 0.2)]
    assert rows[0] < rows[1] < rows[2] or rows[2] < rows[0]  # moving down (or wrapped)
    assert h.scanlines(20, 1.0)[0] > h.scanlines(20, 0.95)[0]
    hot = h.apply(g, t0).get(0, brightest(t0)).fg
    assert hot[0] > 150 and hot[0] == hot[1] == hot[2]  # toward white


def test_interlace_dims_odd_rows():
    h = Hologram(duration=None, seed=4)
    g = solid()
    t = quiet(h, 20)
    out = h.apply(g, t)
    lines = h.scanlines(20, t)
    calm = [y for y in range(20) if min(abs(y - p) for p in lines) > 2]
    even = [out.get(0, y).fg[0] for y in calm if y % 2 == 0]
    odd = [out.get(0, y).fg[0] for y in calm if y % 2 == 1]
    assert max(odd) < min(even) <= 100


def test_dropouts_are_short_and_rows_vanish_or_flicker():
    h = Hologram(duration=None, intensity=1.0, seed=7)
    g = solid()
    frames = [h.apply(g, k / 30) for k in range(90)]
    vanished = [{y for y in range(20) if all(c == BLANK for c in f.cells[y * 12 : (y + 1) * 12])}
                for f in frames]
    assert sum(bool(v) for v in vanished) >= 5
    for y in range(20):  # no row stays gone for more than a few frames
        run = longest = 0
        for v in vanished:
            run = run + 1 if y in v else 0
            longest = max(longest, run)
        assert longest <= 6
    kinds = {kind for k in range(90) for kind in h.dropouts(20, k / 30).values()}
    assert kinds == {"vanish", "flicker"}


def test_envelope_and_identity():
    g = dot()
    h = Hologram(start=1.0, duration=2.0)
    assert h.end == 3.0 and h.end_for(g) == 3.0
    for t in (0.0, 0.99, 3.0, 10.0):
        assert h.apply(g, t) == g and h.apply(g, t) is not g
    assert h.apply(g, 1.5) != g
    assert Hologram(intensity=0).apply(g, 0.5) == g
    endless = Hologram(duration=None)
    assert endless.end == math.inf
    assert endless.apply(g, 1e6) != g


def test_pure_and_seeded():
    g = solid()
    before = copy.deepcopy(g)
    a = Hologram(duration=None, seed=1)
    assert a.apply(g, 2.5) == Hologram(duration=None, seed=1).apply(g, 2.5)
    assert any(a.apply(g, k / 15) != Hologram(duration=None, seed=2).apply(g, k / 15) for k in range(30))
    assert g == before
    assert a.apply(Grid(0, 0), 1.0) == Grid(0, 0)
    assert a.apply(Grid(3, 2), 1.0) == Grid(3, 2)


def test_registered_and_validated():
    assert EFFECTS["hologram"] is Hologram and "hologram" in EFFECTS
    assert make("hologram", BLOOD, intensity=0.9) == Hologram(intensity=0.9)
    assert Hologram().ghost == HOLO_GHOST
    with pytest.raises(ValueError):
        Hologram(ghost=((1, 2, 3),))
