"""Starfield at fixed t: snapshot, outward motion, projection, streaks, layering, envelope."""

import copy
import math
import unicodedata

import pytest

from asciifx.core import CELL_ASPECT, Cell, Grid
from asciifx.effects import EFFECTS, Starfield
from asciifx.effects.base import gradient
from asciifx.effects.starfield import (
    STAR_COLORS, STAR_RAMP, STAR_SPEED, STREAK_GLYPHS, _stars, project,
)

GREEN = (0, 255, 0)


def art() -> Grid:
    g = Grid(16, 8)
    g.put_text(6, 3, "HI", fg=GREEN)
    return g


def busy(w: int = 40, h: int = 16) -> Grid:
    g = Grid(w, h)
    for y in range(0, h, 2):
        g.put_text(y % 3, y, "ab cd" * w, fg=GREEN)
    g.set(w // 2, h // 2 + 1, Cell(" ", None, (9, 9, 9)))  # a space with a background isn't blank
    return g


def drawn(g: Grid) -> int:
    return sum(c.ch != " " for c in g.cells)


def offset(w: int, h: int, x: float, y: float) -> tuple[float, float]:
    """Position relative to the grid centre, in columns, with rows scaled to look the same."""
    return x - (w - 1) / 2, (y - (h - 1) / 2) * CELL_ASPECT


# --- exact cells -------------------------------------------------------------


def test_snapshot():
    calm = Starfield(seed=2, density=0.6, duration=None)
    out = calm.apply(art(), 1.25)
    assert out.to_text() == (
        " .   .. ·      *\n"
        ".    +         .\n"
        "            .\n"
        "     .HI··+ .\n"
        "·           +  .\n"
        "  *    .+      .\n"
        "\n"
        "  .        . ."
    )
    assert out.get(15, 0) == Cell("*", (180, 188, 223)) and out.get(1, 0).fg == (75, 80, 116)
    assert out.get(6, 3) == Cell("H", GREEN)
    nearest = calm.stars(16, 8, 1.25)[-1]
    assert (round(nearest.x, 3), round(nearest.y, 3), round(nearest.z, 4)) == (1.628, 5.029, 0.3135)
    assert nearest.index == 7 and out.get(2, 5) == Cell("*", (187, 194, 226))  # nearest: brightest

    fast = Starfield(seed=2, density=0.6, duration=None, warp=4.0, streak=0.1)
    out = fast.apply(art(), 1.25)
    assert out.to_text() == (
        "  .          ·\n"
        "      .    .   +\n"
        "          . //*+\n"
        "    · HI+ .* . .\n"
        " ·           ..\n"
        "       +\n"
        "   .  .. .     .\n"
        "          ."
    )
    assert out.get(14, 2) == Cell("*", (214, 218, 238))  # head, its streak fading behind it
    assert out.get(13, 2) == Cell("/", (127, 135, 181)) and out.get(12, 2) == Cell("/", (96, 102, 142))


# --- motion and projection ----------------------------------------------------


@pytest.mark.parametrize(("t1", "t2"), [(1.0, 1.2), (5.0, 5.05), (100.0, 100.5)])
def test_stars_move_outward_from_the_centre(t1, t2):
    s = Starfield(duration=None, density=0.2)
    w, h = 80, 30
    before = {st.index: st for st in s.stars(w, h, t1)}
    after = {st.index: st for st in s.stars(w, h, t2)}
    moved = [(before[j], after[j]) for j in before.keys() & after.keys() if after[j].z < before[j].z]
    assert len(moved) >= 20
    for a, b in moved:
        assert b.z == pytest.approx(a.z - s.speed * (t2 - t1))
        ax, ay = offset(w, h, a.x, a.y)
        bx, by = offset(w, h, b.x, b.y)
        assert math.hypot(bx, by) > math.hypot(ax, ay)  # farther out...
        assert ax * by - ay * bx == pytest.approx(0, abs=1e-6)  # ...along the same ray
        assert ax * bx + ay * by >= 0


def test_depth_and_position_follow_the_formula():
    s = Starfield(duration=None, density=0.1, seed=4, warp=1.5)
    w, h, t = 50, 20, 2.0
    world = _stars(4, round(0.1 * w * h))
    stars = s.stars(w, h, t)
    assert stars and [st.z for st in stars] == sorted((st.z for st in stars), reverse=True)
    for st in stars:
        wx, wy, z0, _ = world[st.index]
        assert st.z == pytest.approx((z0 - STAR_SPEED * 1.5 * t) % 1.0)
        assert (st.x, st.y) == pytest.approx(project(w, h, wx, wy * h * CELL_ASPECT / w, st.z))
        assert -0.5 <= st.x < w - 0.5 and -0.5 <= st.y < h - 0.5


def test_projection_is_aspect_corrected():
    cx, cy = 19.5, 9.5  # centre of a 40 x 20 grid, which looks square on screen
    assert project(40, 20, 1.0, 0.0, 1.0) == (cx + 20, cy)
    assert project(40, 20, 0.0, 1.0, 1.0) == (cx, cy + 10)  # 10 rows look as long as 20 columns
    assert project(40, 20, -0.5, -0.5, 0.5) == (cx - 20, cy - 10)  # half the depth: twice as far
    assert project(80, 10, 0.5, 0.25, 1.0) == (39.5 + 20, 4.5 + 5)


def test_far_stars_fill_the_whole_grid():
    w, h = 60, 20
    stars = Starfield(duration=None, density=1.0).stars(w, h, 1.0)
    xs, ys = [st.x for st in stars], [st.y for st in stars]
    assert min(xs) < 2 and max(xs) > w - 3 and min(ys) < 1 and max(ys) > h - 2
    left = sum(x < (w - 1) / 2 for x in xs)
    assert 0.4 < left / len(xs) < 0.6


def test_warp_scales_the_speed():
    assert Starfield(warp=2.5).speed == pytest.approx(2.5 * STAR_SPEED)
    slow, fast = Starfield(duration=None, warp=1.0), Starfield(duration=None, warp=2.0)
    assert slow.stars(40, 20, 3.0) == fast.stars(40, 20, 1.5)


# --- glyphs, colors, streaks --------------------------------------------------


def test_glyph_and_brightness_come_from_depth():
    w, h = 60, 25
    s = Starfield(duration=None, density=0.3, streak=0.0)
    out = s.apply(Grid(w, h), 1.5)
    nearest: dict[tuple[int, int], float] = {}
    for st in s.stars(w, h, 1.5):  # far to near: the nearest star in a cell wins
        nearest[int(st.x + 0.5), int(st.y + 0.5)] = st.z
    for y in range(h):
        for x in range(w):
            assert (out.get(x, y).ch != " ") == ((x, y) in nearest)
    by_depth = sorted(nearest.items(), key=lambda kv: -kv[1])  # far first
    glyphs = [STAR_RAMP.index(out.get(*xy).ch) for xy, _ in by_depth]
    light = [sum(out.get(*xy).fg) for xy, _ in by_depth]
    assert glyphs == sorted(glyphs) and light == sorted(light)
    assert len(set(glyphs)) >= 4


def test_streaks_trail_fast_stars_toward_the_centre():
    w, h = 80, 30
    fast = Starfield(duration=None, density=0.2, warp=4.0, streak=0.1).apply(Grid(w, h), 2.0)
    checked = 0
    for y in range(h):
        for x in range(w):
            ch = fast.get(x, y).ch
            dx, dy = offset(w, h, x, y)
            if ch not in STREAK_GLYPHS or math.hypot(dx, dy) < 8:
                continue
            checked += 1
            if ch == "-":
                assert abs(dx) > abs(dy)
            elif ch == "|":
                assert abs(dy) > abs(dx)
            elif ch == "\\":
                assert dx * dy > 0  # down-right or up-left of the centre
            else:
                assert dx * dy < 0
    assert checked >= 10
    calm = Starfield(duration=None, density=0.2, warp=1.0).apply(Grid(w, h), 2.0)
    count = lambda g: sum(c.ch in STREAK_GLYPHS for c in g.cells)  # noqa: E731
    assert count(fast) > 2 * count(calm)
    off = Starfield(duration=None, density=0.2, warp=4.0, streak=0.0).apply(Grid(w, h), 2.0)
    assert count(off) == 0


@pytest.mark.parametrize("stops", [STAR_COLORS, ((255, 0, 0), (0, 0, 255)), ((7, 7, 7),)])
def test_colors_follow_the_given_stops(stops):
    out = Starfield(duration=None, density=0.3, warp=3.0, colors=stops).apply(Grid(50, 20), 1.0)
    shades = set(gradient(tuple(stops), 32))
    stars = [c for c in out.cells if c.ch != " "]
    assert stars and all(c.fg in shades and c.bg is None for c in stars)


def test_glyphs_are_single_width():
    out = Starfield(duration=None, density=0.4, warp=4.0, layer="over").apply(busy(), 1.0)
    assert {c.ch for c in out.cells} <= set(STAR_RAMP + STREAK_GLYPHS) | {c.ch for c in busy().cells}
    assert all(unicodedata.east_asian_width(c.ch) not in "WF" for c in out.cells)
    custom = Starfield(duration=None, density=0.4, ramp="oO").apply(Grid(30, 10), 1.0)
    assert {c.ch for c in custom.cells} <= set(" oO" + STREAK_GLYPHS)


# --- layering ----------------------------------------------------------------


@pytest.mark.parametrize("t", [0.8, 1.5, 4.2])
def test_under_keeps_art_in_front(t):
    g = busy()
    s = Starfield(duration=None, density=0.5, warp=3.0, streak=0.1)
    alone = s.apply(Grid(g.width, g.height), t)
    out = s.apply(g, t)
    for got, src, star in zip(out.cells, g.cells, alone.cells):
        blank = src.ch == " " and src.bg is None
        assert got == (star if blank and star.ch != " " else src)
    assert drawn(out) > drawn(g)


@pytest.mark.parametrize("t", [0.8, 1.5, 4.2])
def test_over_draws_on_top(t):
    g = busy()
    s = Starfield(duration=None, density=0.5, warp=3.0, streak=0.1, layer="over")
    alone = s.apply(Grid(g.width, g.height), t)
    out = s.apply(g, t)
    for got, src, star in zip(out.cells, g.cells, alone.cells):
        assert got == (src if star.ch == " " else star)
    full = Grid.filled(30, 10, Cell("#", GREEN))
    assert s.apply(full, t) != full
    assert Starfield(duration=None, density=0.5).apply(full, t) == full  # under: hidden


# --- envelope ----------------------------------------------------------------


@pytest.mark.parametrize("t", [-1.0, 0.0, 0.99, 3.0, 3.01, 50.0])
def test_identity_outside_start_to_end(t):
    s = Starfield(start=1.0, duration=2.0, density=0.5)
    assert s.end == 3.0 and s.end_for(art()) == 3.0
    out = s.apply(art(), t)
    assert out == art() and out.cells is not art().cells
    if not 1.0 <= t < 3.0:
        assert s.stars(16, 8, t) == []


def test_stars_fade_in_and_out():
    s = Starfield(start=1.0, duration=3.0, fade=0.6, density=0.3)
    assert [s.envelope(t) for t in (0.9, 1.0, 1.6, 2.5, 3.4, 4.0)] == [0, 0, 1, 1, 1, 0]
    count = [len(s.stars(60, 20, t)) for t in (1.05, 1.2, 1.4, 1.6, 2.5, 3.6, 3.8, 3.95)]
    assert count[:4] == sorted(count[:4]) and count[0] < count[3] // 3
    assert count[4:] == sorted(count[4:], reverse=True) and count[-1] < count[4] // 3
    # Fading only hides stars; the ones shown are exactly where they'd be anyway.
    full = {st.index: st for st in Starfield(start=1.0, duration=None, density=0.3).stars(60, 20, 3.8)}
    assert all(full[st.index] == st for st in s.stars(60, 20, 3.8))


# --- endless mode ------------------------------------------------------------


@pytest.mark.parametrize("t", [0.7, 10.0, 1000.0, 1e5])
def test_endless_runs_forever(t):
    s = Starfield(duration=None)
    assert s.end == math.inf and s.end_for(art()) == math.inf and s.envelope(t) == 1.0
    assert drawn(s.apply(Grid(60, 20), t)) > 20


def test_endless_still_starts_at_start():
    s = Starfield(start=2.0, duration=None, density=0.3)
    g = Grid(40, 15)
    assert s.apply(g, 1.9) == g
    assert 0 < len(s.stars(40, 15, 2.1)) < len(s.stars(40, 15, 3.0))
    assert s.apply(g, math.inf) == g


# --- purity ------------------------------------------------------------------


def test_input_not_mutated():
    for layer in ("under", "over"):
        g = busy()
        before = copy.deepcopy(g)
        s = Starfield(duration=2.0, density=0.5, warp=3.0, layer=layer)
        for t in (-1.0, 0.0, 0.1, 0.5, 1.0, 1.9, 2.0, 5.0):
            out = s.apply(g, t)
            assert out is not g and out.cells is not g.cells
        assert g == before


def test_deterministic_and_seeded():
    g = busy()
    a = Starfield(duration=None, seed=3)
    assert a.apply(g, 1.7) == Starfield(duration=None, seed=3).apply(g, 1.7)
    _ = a.apply(g, 9.0)
    assert a.apply(g, 1.7) == Starfield(duration=None, seed=3).apply(g, 1.7)
    assert a.apply(g, 1.7) != Starfield(duration=None, seed=4).apply(g, 1.7)
    assert a.apply(g, 1.7) != a.apply(g, 1.8)
    assert a == Starfield(duration=None, seed=3) and hash(a) == hash(Starfield(duration=None, seed=3))


def test_tiny_and_empty_grids():
    s = Starfield(duration=None, density=2.0, warp=4.0)
    for w, h in ((0, 0), (1, 1), (1, 9), (9, 1), (2, 2)):
        g = Grid(w, h)
        out = s.apply(g, 1.0)
        assert (out.width, out.height) == (w, h)
    assert drawn(s.apply(Grid(9, 1), 1.0)) > 0


def test_registered_with_usable_defaults():
    assert EFFECTS["starfield"] is Starfield
    s = Starfield()
    assert (s.start, s.duration, s.seed, s.layer, s.end, s.warp) == (0.0, 4.0, 0, "under", 4.0, 1.0)
    assert s.apply(Grid(60, 20), 1.0) != Grid(60, 20)


@pytest.mark.parametrize("kw", [{"layer": "middle"}, {"colors": ()}, {"ramp": ""}])
def test_rejects_bad_params(kw):
    with pytest.raises(ValueError):
        Starfield(**kw)
