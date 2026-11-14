"""Assemble (particles fly home) and Glint, at fixed times."""

import copy
import math
import time

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.effects import Assemble, Glint, noise
from asciifx.effects.base import lerp_color
from asciifx.effects.particles import ASSEMBLE_HOT, ASSEMBLE_WHITE_HOT, TRAIL_GLYPHS

W, H = 60, 20
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def sparse(n: int = 24, w: int = W, h: int = H, seed: int = 1) -> Grid:
    """n cells with unique letters at noise positions, each with its own color."""
    g = Grid(w, h)
    placed = 0
    k = 0
    while placed < n:
        x, y = int(noise(seed, k, 0) * w), int(noise(seed, k, 1) * h)
        k += 1
        if g.get(x, y) == BLANK:
            g.set(x, y, Cell(LETTERS[placed], (40 + 8 * placed, 90, 200)))
            placed += 1
    return g


def dense(w: int, h: int) -> Grid:
    """A filled braille ellipse with a vertical color gradient (a stand-in logo)."""
    g = Grid(w, h)
    for y in range(h):
        for x in range(w):
            if ((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2 < 1:
                g.set(x, y, Cell("⣿⡇⠿⣶"[(x + y) % 4], (240 - 2 * y, 200 - 3 * y, 200 - 3 * y)))
    return g


def fx(**kw) -> Assemble:
    return Assemble(**{"start": 1.0, "duration": 3.0, "cool_time": 0.5, "spread": 0.5, **kw})


def visible(g: Grid) -> dict[tuple[int, int], Cell]:
    return {(i % g.width, i // g.width): c for i, c in enumerate(g.cells) if c != BLANK}


# --- contract -------------------------------------------------------------------


def test_blank_before_start_and_exactly_the_input_from_end():
    e, g = fx(), sparse()
    assert e.apply(g, 0.0) == Grid(W, H) and e.apply(g, 0.999) == Grid(W, H)
    assert e.apply(g, e.end) == g and e.apply(g, 100.0) == g
    assert e.apply(g, e.end) is not g


def test_input_never_mutated_and_frames_are_pure():
    e, g = fx(), sparse()
    before = copy.deepcopy(g)
    for k in range(60):
        t = 0.9 + k * 0.05
        assert e.apply(g, t) == e.apply(g, t)
    assert g == before


def test_cache_follows_in_place_mutation():
    e, g = fx(), sparse()
    e.apply(g, 2.0)
    g.set(0, 0, Cell("Z", (1, 2, 3)))
    assert e.apply(g, e.end) == g
    assert len(e.particles(g)) == 25
    assert e.apply(g, 9.0).get(0, 0) == Cell("Z", (1, 2, 3))


# --- schedule -------------------------------------------------------------------


def test_schedule():
    e, g = fx(seed=3), sparse()
    parts = e.particles(g)
    assert len(parts) == 24
    assert {(p.x, p.y) for p in parts} == set(visible(g))
    assert e.cooling == 0.5 and e.flight_time == pytest.approx((3.0 - 0.5) * 0.5)
    for p in parts:
        assert p.launch == pytest.approx(1.0 + e.delay(p.x, p.y))
        assert p.land == pytest.approx(p.launch + e.flight_time)
        assert 1.0 <= p.launch <= 1.0 + 2.5 * 0.5 and p.land <= e.end - e.cooling + 1e-9
    assert [(p.land, p.y, p.x) for p in parts] == sorted((p.land, p.y, p.x) for p in parts)
    # noise-based: other seeds, other delays
    assert [p.launch for p in fx(seed=4).particles(g)] != [p.launch for p in parts]


def test_particles_at_home_match_the_delay_schedule():
    e, g = fx(glyphs="*", trail_speed=math.inf), sparse(30)
    parts = e.particles(g)
    counts = []
    for t in (1.5, 2.5, 2.9, 3.3, 3.45):
        landed = [p for p in parts if p.land <= t]
        counts.append(len(landed))
        expected = sum(1.0 + e.delay(p.x, p.y) + e.flight_time <= t for p in parts)
        assert len(landed) == expected
        out = e.apply(g, t)
        # in flight everything is a "*", so letters are exactly the landed particles
        # (unless a spark happens to cover one: none do at these times)
        letters = {xy for xy, c in visible(out).items() if c.ch != "*"}
        assert letters == {(p.x, p.y) for p in landed}
    assert counts == [0, 7, 14, 24, 29]  # staggered: the logo builds up


def test_positions_move_monotonically_toward_home():
    e, g = fx(), sparse()
    for p in e.particles(g):
        assert e.position(p, 0.0) == (p.sx, p.sy)
        assert e.position(p, p.land) == (p.x, p.y)
        dists = [math.dist(e.position(p, p.launch + k / 30 * e.flight_time), (p.x, p.y)) for k in range(31)]
        assert all(b <= a + 1e-9 for a, b in zip(dists, dists[1:]))
        assert dists[0] > dists[15] > dists[30] == 0.0


def test_in_flight_particles_are_drawn_at_their_rounded_position():
    e, g = fx(glyphs=None, trail_speed=math.inf), sparse(12)
    t = 2.0
    out = e.apply(g, t)
    for p in e.particles(g):
        if p.launch <= t < p.land:
            x, y = e.position(p, t)
            xi, yi = math.floor(x + 0.5), math.floor(y + 0.5)
            if 0 <= xi < W and 0 <= yi < H:
                assert out.get(xi, yi).ch == g.get(p.x, p.y).ch


def test_later_particles_win_shared_cells():
    # Everything launches at once from the centre: many particles share cells.
    g = Grid(30, 12)
    for k, ch in enumerate(LETTERS[:40]):
        g.set(k % 20 + 5, k // 20 + 3, Cell(ch, (200, 0, 0)))
    e = Assemble(start=0.0, duration=2.0, origin="center", spread=0.0, trail_speed=math.inf)
    t = 0.02
    owner: dict[tuple[int, int], str] = {}
    for p in e.particles(g):  # drawing order
        x, y = e.position(p, t)
        owner[(math.floor(x + 0.5), math.floor(y + 0.5))] = g.get(p.x, p.y).ch
    assert len(owner) < 40  # there really are collisions
    assert {xy: c.ch for xy, c in visible(e.apply(g, t)).items()} == owner


# --- origins ----------------------------------------------------------------------


def test_scatter_starts_cover_the_screen_and_beyond():
    e = fx(origin="scatter", overscan=0.25)
    g = dense(60, 20)
    xs = [p.sx for p in e.particles(g)]
    ys = [p.sy for p in e.particles(g)]
    assert -0.25 * 60 - 0.5 <= min(xs) < 0 and 60 < max(xs) <= 1.25 * 60
    assert -0.25 * 20 - 0.5 <= min(ys) < 0 and 20 < max(ys) <= 1.25 * 20


def test_edge_starts_are_outside_the_screen():
    e = fx(origin="edges")
    for p in e.particles(dense(60, 20)):
        assert not (0 <= p.sx <= 59 and 0 <= p.sy <= 19)
        assert p.sx < -1.9 or p.sx > 60.9 or p.sy < -1.9 or p.sy > 20.9


def test_center_starts_are_a_round_burst():
    e = fx(origin="center")
    for p in e.particles(dense(60, 20)):
        # within 1.5 cell-widths of the centre, measured with 2:1 cells
        assert math.hypot(p.sx - 29.5, 2 * (p.sy - 9.5)) <= 1.5 + 1e-9


def visual_polar(x: float, y: float, cx: float = 29.5, cy: float = 9.5) -> tuple[float, float]:
    """(radius in cell widths, angle) of a point around the centre, 2:1 cells."""
    return math.hypot(x - cx, 2 * (y - cy)), math.atan2(2 * (y - cy), x - cx)


def test_vortex_spirals_in_from_outside_the_screen():
    e, g = fx(origin="vortex", turns=1.0), dense(60, 20)
    reach = math.hypot(30, 20)
    for p in e.particles(g):
        r0, _ = visual_polar(p.sx, p.sy)
        assert 1.05 * reach - 1e-6 <= r0 <= 1.4 * reach + 1e-6  # off-screen, all round
        assert (p.mx, p.my) == (29.5, 9.5) and 0.7 * 2 * math.pi <= p.spin <= 1.3 * 2 * math.pi + 1e-9
        assert e.position(p, p.launch) == pytest.approx((p.sx, p.sy))
        assert e.position(p, p.land) == (p.x, p.y)
    p = max(e.particles(g), key=lambda q: q.spin)
    # Along the flight the radius shrinks steadily while the angle turns by ``spin``: an orbit, not a line.
    track = [visual_polar(*e.position(p, p.launch + k / 40 * e.flight_time)) for k in range(41)]
    assert all(b[0] <= a[0] + 1e-9 for a, b in zip(track, track[1:]))
    turned = sum((b[1] - a[1] + math.pi) % (2 * math.pi) - math.pi for a, b in zip(track, track[1:]))
    assert turned == pytest.approx(-p.spin, abs=0.05)


def test_explosion_bursts_past_home_then_is_pulled_back():
    from asciifx.effects.particles import BURST_SHARE

    e, g = fx(origin="explosion", spread=0.05, burst=0.5), dense(60, 20)
    reach = math.hypot(30, 20)
    for p in e.particles(g):
        assert math.hypot(p.sx - 29.5, 2 * (p.sy - 9.5)) <= 1.5 + 1e-9  # from the central ball
        far = math.hypot(p.mx - p.x, 2 * (p.my - p.y))
        assert 0.6 * 0.5 * reach - 1e-6 <= far <= 1.4 * 0.5 * reach + 1e-6  # overshoots its home
        turn = p.launch + BURST_SHARE * e.flight_time
        assert e.position(p, turn) == pytest.approx((p.mx, p.my))
        assert e.position(p, p.land) == (p.x, p.y)
    p = max(e.particles(g), key=lambda q: math.hypot(q.x - 29.5, 2 * (q.y - 9.5)))
    out_d = [visual_polar(*e.position(p, p.launch + k / 20 * BURST_SHARE * e.flight_time))[0] for k in range(21)]
    assert all(b >= a - 1e-9 for a, b in zip(out_d, out_d[1:]))  # outward first
    assert out_d[-1] > visual_polar(p.x, p.y)[0]  # beyond its home


@pytest.mark.parametrize("origin", ["vortex", "explosion"])
def test_curved_origins_draw_particles_at_their_positions(origin):
    e, g = fx(origin=origin, glyphs=None, trail_speed=math.inf), sparse(12)
    for t in (1.4, 2.0, 2.6):
        out = e.apply(g, t)
        for p in e.particles(g):
            if p.launch <= t < p.land:
                x, y = e.position(p, t)
                xi, yi = math.floor(x + 0.5), math.floor(y + 0.5)
                if 0 <= xi < W and 0 <= yi < H:
                    assert out.get(xi, yi).ch == g.get(p.x, p.y).ch
    assert e.apply(g, e.end) == g and e.apply(g, 0.5) == Grid(W, H)


@pytest.mark.parametrize("origin", ["vortex", "explosion"])
def test_curved_origins_leave_trails_and_are_cheap(origin):
    g = dense(200, 50)
    e = Assemble(start=0.0, duration=4.0, origin=origin, spread=0.45 if origin == "vortex" else 0.06,
                 cool_time=0.8)  # fmt: skip
    frames = [e.apply(g, t) for t in (0.3, 1.0)]
    assert any(c.ch in TRAIL_GLYPHS for f in frames for c in f.cells)
    t0 = time.perf_counter()
    for k in range(5):
        e.apply(g, 0.5 + 0.5 * k)
    per_frame = (time.perf_counter() - t0) / 5
    assert per_frame < 0.05, f"{per_frame * 1000:.1f} ms per frame"


# --- colors -----------------------------------------------------------------------


def on_hot_line(c: tuple[int, int, int]) -> bool:
    """c lies on the hot -> white-hot segment (within rounding)."""
    (r0, g0, b0), (r1, g1, b1) = ASSEMBLE_HOT, ASSEMBLE_WHITE_HOT
    u = (c[1] - g0) / (g1 - g0)
    return 0 <= u <= 1 and abs(c[0] - (r0 + (r1 - r0) * u)) <= 1 and abs(c[2] - (b0 + (b1 - b0) * u)) <= 3


def test_hot_in_flight_then_cooling_to_the_true_color():
    e, g = fx(glyphs="*", trail_speed=math.inf), sparse()
    parts = e.particles(g)
    p = parts[len(parts) // 2]
    true = g.get(p.x, p.y)
    # in flight: hot colors only, whiter just after launch than just before landing
    out = e.apply(g, 2.0)
    sparks = [c.fg for c in out.cells if c.ch == "*"]
    assert sparks and all(on_hot_line(fg) for fg in sparks)
    q = parts[-1]
    x, y = e.position(q, q.launch + 0.01)
    early = e.apply(g, q.launch + 0.01).get(math.floor(x + 0.5), math.floor(y + 0.5)).fg
    x, y = e.position(q, q.land - 0.01)
    late = e.apply(g, q.land - 0.01).get(math.floor(x + 0.5), math.floor(y + 0.5)).fg
    assert early[1] > late[1] and late == pytest.approx(ASSEMBLE_HOT, abs=2)
    # landing: hot; half-way through cooling: in between; after cool_time: the true cell
    assert e.apply(g, p.land).get(p.x, p.y) == Cell(true.ch, ASSEMBLE_HOT, true.bg)
    mid = e.apply(g, p.land + e.cooling / 2).get(p.x, p.y)
    assert mid.ch == true.ch and mid.fg not in (ASSEMBLE_HOT, true.fg)
    assert e.apply(g, p.land + e.cooling).get(p.x, p.y) == true


def test_glyphs_and_default_glyphs():
    g = sparse()
    out = fx(glyphs="+x", trail_speed=math.inf).apply(g, 1.6)
    assert {c.ch for c in out.cells if c != BLANK} <= set("+x") | set(LETTERS)
    out = fx(trail_speed=math.inf).apply(g, 1.6)
    assert {c.ch for c in out.cells if c != BLANK} <= set(LETTERS)


def test_background_only_cells_fly_as_blocks_and_cool_their_bg():
    g = Grid(10, 4)
    g.set(5, 2, Cell(" ", None, (0, 0, 200)))
    e = Assemble(start=0.0, duration=1.0, cool_time=0.4, spread=0.0, trail_speed=math.inf)
    assert "█" in {c.ch for c in e.apply(g, 0.1).cells}
    p = e.particles(g)[0]
    assert e.apply(g, p.land).get(5, 2) == Cell(" ", None, ASSEMBLE_HOT)
    assert e.apply(g, 1.0) == g


# --- trails -----------------------------------------------------------------------


def test_fast_particles_leave_a_dim_streak_behind():
    g = Grid(80, 20)
    g.set(75, 10, Cell("@", (255, 255, 255)))
    e = Assemble(start=0.0, duration=2.0, origin="center", spread=0.0, cool_time=0.2)
    p = e.particles(g)[0]
    t = 0.1  # just launched: fast, moving right
    out = visible(e.apply(g, t))
    x, y = e.position(p, t)
    head = (math.floor(x + 0.5), math.floor(y + 0.5))
    assert out[head].ch == "@"
    assert out[(head[0] - 1, head[1])].ch == TRAIL_GLYPHS[0]  # horizontal streak behind
    assert sum(out[(head[0] - 1, head[1])].fg) < sum(out[head].fg)
    assert len(out) == 2
    late = visible(e.apply(g, p.land - 0.05))  # slow near the end: no trail
    assert len(late) == 1
    no_trail = Assemble(start=0.0, duration=2.0, origin="center", spread=0.0, cool_time=0.2, trail_speed=math.inf)
    assert len(visible(no_trail.apply(g, t))) == 1


def test_trails_never_cover_particles():
    g = dense(80, 20)
    kw = {"start": 0.0, "duration": 1.5, "cool_time": 0.2, "spread": 0.3, "origin": "center"}
    e = Assemble(**kw)
    trails = 0
    for t in (0.05, 0.15, 0.3):
        out = e.apply(g, t)
        no = Assemble(**kw, trail_speed=math.inf).apply(g, t)
        # every particle cell drawn without trails is still a particle with trails on
        for a, b in zip(no.cells, out.cells):
            if a != BLANK:
                assert b == a
            elif b != BLANK:
                trails += 1
                assert b.ch in TRAIL_GLYPHS
    assert trails > 20


def test_validation():
    for bad in ({"origin": "left"}, {"ease": "bounce"}, {"glyphs": ""}, {"trail_glyphs": "ab"}):
        with pytest.raises(ValueError):
            Assemble(**bad)
    assert Assemble(spread=3.0).spread == 1.0


def test_out_back_overshoots_then_settles():
    e, g = fx(ease="out_back"), sparse()
    p = max(e.particles(g), key=lambda q: abs(q.x - q.sx))
    sign = 1 if p.x > p.sx else -1
    xs = [e.position(p, p.launch + k / 50 * e.flight_time)[0] for k in range(51)]
    assert max(sign * (x - p.x) for x in xs) > 0.5  # went past home
    assert xs[-1] == p.x and e.apply(g, e.end) == g


def test_spread_one_lands_instantly():
    e = Assemble(start=0.0, duration=1.0, spread=1.0, cool_time=0.2)
    g = sparse()
    assert e.flight_time == 0.0
    for t in (0.0, 0.3, 0.7, 0.99):
        out = e.apply(g, t)
        assert set(visible(out)) <= set(visible(g))


def test_full_screen_braille_frame_is_cheap():
    g = dense(200, 50)  # ~7800 particles, well above a real logo's ~2-3k
    e = Assemble(start=0.0, duration=5.0, spread=0.6, cool_time=0.9)
    e.apply(g, 0.5)  # build the per-grid state once
    t0 = time.perf_counter()
    for k in range(10):
        e.apply(g, 1.0 + 0.2 * k)
    per_frame = (time.perf_counter() - t0) / 10
    assert per_frame < 0.05, f"{per_frame * 1000:.1f} ms per frame"


# --- Glint ------------------------------------------------------------------------


def test_glint_identity_outside_its_window():
    gl, g = Glint(start=1.0, duration=1.0), sparse()
    for t in (0.0, 0.99, 2.0, 5.0):
        assert gl.apply(g, t) == g and gl.apply(g, t) is not g


def test_glint_band_only_brightens_glyph_colors_near_its_centre():
    g = dense(60, 20)
    gl = Glint(start=0.0, duration=1.0, width=5.0, strength=0.8, color=(255, 255, 255))
    for t in (0.3, 0.5, 0.7):
        c = gl.centre(g, t)
        out = gl.apply(g, t)
        changed = [(i % 60, i // 60) for i, (a, b) in enumerate(zip(g.cells, out.cells)) if a != b]
        assert changed
        for x, y in changed:
            a, b = g.get(x, y), out.get(x, y)
            assert abs(x + 2 * y - c) < 5.0
            assert b.ch == a.ch and b.bg == a.bg and sum(b.fg) > sum(a.fg)
    # the centre sweeps from before the art's top-left to past its bottom-right
    assert gl.centre(g, 0.0) < min(i % 60 + 2 * (i // 60) for i, c in enumerate(g.cells) if c != BLANK)
    assert gl.centre(g, 0.9999) > max(i % 60 + 2 * (i // 60) for i, c in enumerate(g.cells) if c != BLANK)


def test_glint_strength_profile():
    g = Grid.filled(30, 1, Cell("#", (0, 0, 0)))
    gl = Glint(start=0.0, duration=1.0, width=4.0, strength=1.0, color=(255, 255, 255))
    out = gl.apply(g, 0.5)
    c = gl.centre(g, 0.5)
    brightest = max(range(30), key=lambda x: out.get(x, 0).fg[0])
    assert abs(brightest - c) <= 0.5
    assert out.get(brightest, 0).fg == lerp_color((0, 0, 0), (255, 255, 255), round(32 * (1 - ((brightest - c) / 4) ** 2) ** 2) / 32)
    assert gl.apply(Grid(5, 5), 0.5) == Grid(5, 5)  # nothing to light
