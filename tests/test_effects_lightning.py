"""Lightning: the bolt's path, the leader, the flash and the afterglow, at fixed times."""

import copy

import pytest

from asciifx.core import Cell, Grid
from asciifx.effects import Lightning

W, H = 60, 20
ART_FG = (200, 30, 30)


def art() -> Grid:
    """A block of braille glyphs in the lower half (something to strike and light up)."""
    g = Grid(W, H)
    for y in range(12, 18):
        for x in range(20, 40):
            g.set(x, y, Cell("⣿", ART_FG))
    return g


def fx(**kw) -> Lightning:
    return Lightning(**{"start": 1.0, "duration": 1.2, "target": (0.5, 0.6), "seed": 7, **kw})


def bolt_cells(g: Grid, base: Grid) -> dict[tuple[int, int], Cell]:
    """Cells whose glyph differs from ``base`` and is braille (the bolt; not the lit-up art)."""
    return {
        (i % g.width, i // g.width): c
        for i, (c, b) in enumerate(zip(g.cells, base.cells))
        if c.ch != b.ch and 0x2800 <= ord(c.ch) <= 0x28FF
    }


def test_identity_outside_the_window_and_pure():
    e, g = fx(), art()
    before = copy.deepcopy(g)
    for t in (0.0, 0.999, e.end, e.end + 5):
        out = e.apply(g, t)
        assert out == g and out is not g
    for t in (1.05, 1.17, 1.4):
        assert e.apply(g, t) == e.apply(g, t) == fx().apply(g, t)
    assert g == before


def test_bolt_runs_from_the_top_edge_to_the_target():
    e = fx()
    dots = e.bolt(W, H)
    assert dots and all(0 <= x < 2 * W and 0 <= y < 4 * H and 0.0 <= o <= 1.0 for x, y, o in dots)
    assert min(y for _, y, _ in dots) == 0  # starts at the very top
    tx, ty = 0.6 * 4 * H, 0.5 * 2 * W
    last = [(x, y) for x, y, o in dots if o == 1.0]
    assert last and all(abs(y - tx) <= 1 and abs(x - ty) <= 2 for x, y in last)  # ends on the target
    assert max(y for _, y, _ in dots) <= 4 * H * 0.6 + 1  # branches never reach below the strike


def test_bolt_is_seeded_and_jagged():
    a, b = fx(seed=1).bolt(W, H), fx(seed=2).bolt(W, H)
    assert a == fx(seed=1).bolt(W, H) and a != b
    xs = {x for x, _, o in a if o < 1}
    assert max(xs) - min(xs) >= 4  # not a straight vertical line
    assert len(fx(branches=0).bolt(W, H)) < len(fx(branches=4).bolt(W, H))


def test_leader_cracks_down_from_the_top():
    e, g = fx(), art()
    early, late = bolt_cells(e.apply(g, 1.03), g), bolt_cells(e.apply(g, 1.12), g)
    assert early and len(early) < len(late)
    assert min(y for _, y in early) == 0
    assert max(y for _, y in early) < max(y for _, y in late)


def test_flash_is_the_whole_screen_with_the_art_as_a_silhouette():
    e, g = fx(), art()
    assert e.strike == pytest.approx(1.14)
    for t in (1.145, 1.2):
        out = e.apply(g, t)
        assert all(c.bg == (255, 255, 255) for c in out.cells)
        assert all(c.fg == (40, 0, 0) for c, src in zip(out.cells, g.cells) if src.ch != " ")
        assert all(c.ch == src.ch for c, src in zip(out.cells, g.cells))
    assert all(c.bg is None for c in e.apply(g, 1.215).cells)  # 0.07 s: 2 frames at 30 FPS


def test_afterglow_lights_the_art_then_fades_with_the_bolt():
    e, g = fx(), art()
    art_cells = [i for i, c in enumerate(g.cells) if c.ch != " "]

    def art_brightness(out: Grid) -> float:
        return sum(sum(out.cells[i].fg) for i in art_cells if out.cells[i].ch == "⣿") / len(art_cells)

    just_after, later = e.apply(g, 1.23), e.apply(g, 1.8)
    assert art_brightness(just_after) > art_brightness(later) > sum(ART_FG) * 0.99
    bolt_now = bolt_cells(just_after, g)
    assert bolt_now and all(min(c.fg) >= 230 for c in bolt_now.values())  # white-hot
    fading = bolt_cells(e.apply(g, 1.9), g)
    assert fading and all(c.fg[1] < 255 for c in fading.values())
    assert e.bolt_level(1.29) < 0.5 < e.bolt_level(1.25)  # the channel dims (from 0.06 s after the flash)...
    assert e.bolt_level(1.33) > 0.8  # ...and strikes again
    assert e.apply(g, 2.0) != g and e.apply(g, 2.2) == g  # faded out smoothly by the end


def test_tiny_grids():
    e = fx()
    for size in [(0, 0), (1, 1), (2, 1), (1, 5)]:
        g = Grid(*size)
        for t in (1.05, 1.15, 1.5):
            out = e.apply(g, t)
            assert (out.width, out.height) == size
