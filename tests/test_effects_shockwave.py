"""Shockwave: an aspect-correct ring that brightens and pushes what it passes, at fixed times."""

import copy
import math

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.effects import Shockwave

W, H = 80, 30
FG = (100, 0, 0)


def fx(**kw) -> Shockwave:
    return Shockwave(**{"start": 1.0, "duration": 1.0, "center": (0.5, 0.5), **kw})


def ring_cells(g: Grid) -> list[tuple[int, int]]:
    return [(i % g.width, i // g.width) for i, c in enumerate(g.cells) if c != BLANK]


def test_identity_outside_the_window_pure_and_unmutated():
    e, g = fx(), Grid.filled(W, H, Cell("⣿", FG))
    before = copy.deepcopy(g)
    for t in (0.0, 0.99, 2.0, 7.0):
        out = e.apply(g, t)
        assert out == g and out is not g
    assert e.apply(g, 1.3) == e.apply(g, 1.3) != g
    assert g == before


def test_crest_expands_fast_then_slows_and_fades():
    e = fx()
    r_max = e.max_radius(W, H)
    assert r_max == pytest.approx(math.hypot(40, 30) + 3.0)  # the farthest corner, plus the band
    assert e.crest(0.9, W, H) is None and e.crest(2.0, W, H) is None
    (r1, a1), (r2, a2), (r3, a3) = (e.crest(t, W, H) for t in (1.1, 1.3, 1.5))
    assert 0 < r1 < r2 < r3 < r_max and 1.0 > a1 > a2 > a3 > 0
    assert r2 - r1 > r3 - r2  # ease out
    assert fx(radius=10.0).crest(1.5, W, H)[0] == pytest.approx(7.5)  # out_quad(0.5) = 0.75


def test_ring_is_round_on_screen():
    e, t = fx(), 1.3
    r, _ = e.crest(t, W, H)
    cells = ring_cells(e.apply(Grid(W, H), t))
    assert len(cells) > 40
    cx, cy = e.origin(W, H)
    for x, y in cells:  # every ring cell sits on the circle, measured in cell widths
        assert abs(math.hypot(x + 0.5 - cx, 2 * (y + 0.5 - cy)) - r) < 1.2
    width = max(x for x, _ in cells) - min(x for x, _ in cells)
    height = max(y for _, y in cells) - min(y for _, y in cells)
    assert width == pytest.approx(2 * r, abs=2) and height == pytest.approx(r, abs=2)  # 2:1 cells
    out = e.apply(Grid(W, H), t)
    assert all(0x2801 <= ord(out.get(x, y).ch) <= 0x28FF for x, y in cells)  # thin braille line


def test_glyphs_on_the_crest_brighten_and_far_ones_do_not():
    g = Grid.filled(W, H, Cell("#", FG))
    e, t = fx(push=0.0), 1.3
    r, _ = e.crest(t, W, H)
    out = e.apply(g, t)
    cx, cy = e.origin(W, H)
    for y in range(H):
        for x in range(W):
            d = math.hypot(x + 0.5 - cx, 2 * (y + 0.5 - cy))
            c = out.get(x, y)
            assert c.ch == "#"
            if abs(d - r) > e.width + 0.5:
                assert c.fg == FG
            elif abs(d - r) < 0.5:
                assert c.fg[1] > 60 and c.fg[0] > FG[0]  # pulled toward near-white
    no_ring = fx(ring=None).apply(Grid(W, H), t)
    assert no_ring == Grid(W, H)


def test_cells_on_the_crest_are_pushed_outward():
    e = fx(push=2.0, strength=0.0, ring=None)
    g = Grid(W, H)
    g.set(55, 15, Cell("@", FG))  # 15 cells right of the centre, on the centre row
    cx, _ = e.origin(W, H)
    t = next(t for t in (1.0 + k / 200 for k in range(200)) if e.crest(t, W, H)[0] >= 55.5 - cx)
    out = e.apply(g, t)
    found = [(x, y) for x, y in ring_cells(out) if out.get(x, y).ch == "@"]
    assert found and all(x > 55 and y == 15 for x, y in found)  # copied outward along the radius


def test_tiny_grids():
    e = fx()
    for size in [(0, 0), (1, 1), (3, 1), (1, 4)]:
        g = Grid.filled(*size, Cell("x", FG)) if size[0] * size[1] else Grid(*size)
        for t in (1.0, 1.2, 1.8):
            assert (e.apply(g, t).width, e.apply(g, t).height) == size
