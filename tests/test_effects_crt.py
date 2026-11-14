"""CRT power on/off: exact beam geometry at fixed t, rendering, snow, end states."""

import copy

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.core.easing import in_out_cubic, in_quad, out_cubic, out_quad
from asciifx.effects import CRT
from asciifx.effects.base import lerp_color
from asciifx.effects.crt import CRT_GLOW, STAGES_OFF, STAGES_ON, STATIC_CHARSET, span

W, H = 40, 9
RED = (200, 40, 40)
GREY = (150, 150, 150)


def picture(w: int = W, h: int = H) -> Grid:
    """Every row labelled with its own digit, so squashing is easy to follow."""
    g = Grid(w, h)
    for y in range(h):
        g.put_text(2, y, f"{y}" * (w - 4), fg=RED if y % 2 else GREY)
    return g


def on(**kw) -> CRT:
    return CRT(**{"start": 1.0, "duration": 2.0, "static": 0.0, **kw})


def at_u(fx: CRT, u: float) -> float:
    """The time at which the power-on axis is at u."""
    f = u if fx.mode == "on" else 1.0 - u
    return fx.start + f * fx.duration


def chars(g: Grid, y: int) -> str:
    return "".join(c.ch for c in g.cells[y * g.width : (y + 1) * g.width])


# --- geometry -------------------------------------------------------------------


def test_span_centres_with_odd_leftover_after():
    assert span(2, 40) == (19, 21) and span(2, 41) == (19, 21) and span(1, 9) == (4, 5)
    assert span(40, 40) == (0, 40) and span(5, 9) == (2, 7) and span(1, 1) == (0, 1)


def test_dot_stage():
    fx = on()
    a = STAGES_ON[0]
    b = fx.beam(at_u(fx, a / 2), W, H)
    assert b.stage == "dot" and b.level == pytest.approx(out_quad(0.5))
    assert (b.x0, b.x1, b.y0, b.y1) == (19, 21, 4, 5)
    assert fx.beam(at_u(fx, 0.0), W, H).level == 0.0


@pytest.mark.parametrize("frac", [0.25, 0.5, 0.75])
def test_line_stretches_with_out_cubic(frac):
    fx = on()
    a, b, _ = STAGES_ON
    beam = fx.beam(at_u(fx, a + frac * (b - a)), W, H)
    n = round(2 + (W - 2) * out_cubic(frac))
    assert beam.stage == "line" and beam.level == 1.0
    assert (beam.x0, beam.x1) == ((W - n) // 2, (W - n) // 2 + n)
    assert (beam.y0, beam.y1) == (4, 5)


def test_line_holds_full_width():
    fx = on()
    _, b, c = STAGES_ON
    beam = fx.beam(at_u(fx, (b + c) / 2), W, H)
    assert beam.stage == "line" and (beam.x0, beam.x1) == (0, W)


@pytest.mark.parametrize("frac", [0.3, 0.5, 0.8])
def test_band_opens_with_in_out_cubic(frac):
    fx = on(static=0.4)
    c = STAGES_ON[2]
    beam = fx.beam(at_u(fx, c + frac * (1 - c)), W, H)
    v = in_out_cubic(frac)
    n = round(1 + (H - 1) * v)
    assert beam.stage == "band" and beam.opening == pytest.approx(v)
    assert (beam.y0, beam.y1) == ((H - n) // 2, (H - n) // 2 + n)
    assert beam.static == pytest.approx(0.4 * (1 - v) ** 2)  # fades as the band opens


def test_off_is_the_same_geometry_backwards():
    off = CRT(mode="off", start=0.0, duration=1.0)
    a, b, c = STAGES_OFF
    assert off.beam(-0.1, W, H).stage == "picture"
    beam = off.beam(1 - (c + 0.5 * (1 - c)), W, H)  # half-way through the squash
    assert beam.stage == "band" and beam.y1 - beam.y0 == round(1 + (H - 1) * in_out_cubic(0.5))
    beam = off.beam(1 - (a + 0.5 * (b - a)), W, H)  # half-way through the shrink
    n = round(2 + (W - 2) * out_cubic(0.5))
    assert beam.stage == "line" and beam.x1 - beam.x0 == n
    beam = off.beam(1 - a / 2, W, H)  # the dot fading: quick drop, long afterglow
    assert beam.stage == "dot" and beam.level == pytest.approx(in_quad(0.5))
    assert off.beam(1.0, W, H).stage == "dark"


def test_band_heights_grow_monotonically_and_reach_full_screen():
    fx = on()
    heights = [fx.beam(fx.start + k / 100 * fx.duration, W, H) for k in range(101)]
    hs = [b.y1 - b.y0 for b in heights]
    assert hs == sorted(hs) and hs[0] == 1 and hs[-1] == H


# --- rendering ------------------------------------------------------------------


def test_on_is_blank_before_start_and_exactly_the_input_at_end():
    fx, g = on(static=0.5), picture()
    before = copy.deepcopy(g)
    assert fx.apply(g, 0.99) == Grid(W, H)
    assert fx.apply(g, fx.end) == g and fx.apply(g, fx.end + 5) == g
    assert fx.apply(g, fx.end) is not g
    for k in range(40):
        fx.apply(g, fx.start + k / 40 * fx.duration)
    assert g == before  # never mutated


def test_off_shows_input_before_start_and_is_blank_at_end():
    fx, g = CRT(mode="off", start=2.0, duration=1.0), picture()
    assert fx.apply(g, 1.0) == g and fx.apply(g, 2.0) == g
    assert fx.apply(g, 3.0) == Grid(W, H) and fx.apply(g, 9.0) == Grid(W, H)


def test_dot_render():
    fx = on()
    out = fx.apply(picture(), at_u(fx, STAGES_ON[0] / 2))
    lit = {(i % W, i // W): c.ch for i, c in enumerate(out.cells) if c != BLANK}
    assert lit == {
        (19, 4): "█", (20, 4): "█", (19, 3): "▄", (20, 3): "▄", (19, 5): "▀", (20, 5): "▀",
        (18, 4): "▐", (21, 4): "▌",
    }  # fmt: skip


def test_line_render_matches_beam():
    fx = on()
    a, b, _ = STAGES_ON
    t = at_u(fx, a + 0.5 * (b - a))
    beam = fx.beam(t, W, H)
    out = fx.apply(picture(), t)
    n = beam.x1 - beam.x0
    pad = beam.x0 - 1
    assert chars(out, 4) == " " * pad + "▐" + "█" * n + "▌" + " " * (W - beam.x1 - 1)
    assert chars(out, 3) == " " * beam.x0 + "▄" * n + " " * (W - beam.x1)
    assert chars(out, 5) == " " * beam.x0 + "▀" * n + " " * (W - beam.x1)
    assert all(chars(out, y).strip() == "" for y in (0, 1, 2, 6, 7, 8))
    # white-hot core, the ends shading toward the glow color
    d = (n / 2 - 0.5) / (n / 2)
    assert out.get(beam.x0, 4).fg == lerp_color(fx.line_color, fx.glow_color, 0.55 * d**4)
    assert out.get(beam.x0, 4).fg != out.get(W // 2, 4).fg
    assert out.get(W // 2, 4).fg == fx.line_color


def test_band_render_squashes_rows_and_draws_edges():
    fx = on()
    c = STAGES_ON[2]
    t = at_u(fx, c + 0.5 * (1 - c))
    beam = fx.beam(t, W, H)
    out = fx.apply(picture(), t)
    n = beam.y1 - beam.y0
    for j in range(n):  # band row j shows source row (2j + 1) * H // (2n)
        assert chars(out, beam.y0 + j)[2:-2] == str((2 * j + 1) * H // (2 * n)) * (W - 4)
    assert chars(out, beam.y0 - 1) == "▄" * W and chars(out, beam.y1) == "▀" * W
    assert all(chars(out, y).strip() == "" for y in range(H) if y < beam.y0 - 1 or y > beam.y1)
    # over-bright while thin
    boost = 0.85 * (1 - beam.opening) ** 2
    assert out.get(5, beam.y0).fg == lerp_color(picture().get(5, H // (2 * n)).fg, fx.line_color, boost)


def test_band_crops_when_not_squashing():
    fx = on(squash=False)
    c = STAGES_ON[2]
    t = at_u(fx, c + 0.5 * (1 - c))
    beam = fx.beam(t, W, H)
    out = fx.apply(picture(), t)
    for y in range(beam.y0, beam.y1):
        assert chars(out, y)[2:-2] == str(y) * (W - 4)


def test_one_cell_and_empty_grids():
    fx = on()
    one = Grid.from_text("@")
    for k in range(21):
        out = fx.apply(one, fx.start + k / 20 * fx.duration)
        assert (out.width, out.height) == (1, 1)
    assert fx.apply(Grid(0, 0), 1.5) == Grid(0, 0)


# --- snow -----------------------------------------------------------------------


def snow_cells(fx: CRT, g: Grid, t: float) -> list[int]:
    out = fx.apply(g, t)
    return [i for i, c in enumerate(out.cells) if c.ch in STATIC_CHARSET]


def test_static_is_deterministic_inside_the_band_and_fades():
    big = Grid(80, 30)
    fx = on(static=0.6, seed=4)
    c = STAGES_ON[2]
    t = at_u(fx, c + 0.35 * (1 - c))
    beam = fx.beam(t, 80, 30)
    a = fx.apply(big, t)
    assert a == fx.apply(big, t)  # pure
    cells = snow_cells(fx, big, t)
    assert cells and all(beam.y0 * 80 <= i < beam.y1 * 80 for i in cells)
    band = (beam.y1 - beam.y0) * 80
    assert 0.5 * beam.static * band < len(cells) < 1.05 * beam.static * band  # flicker 55-100%
    assert {c.fg for c in a.cells if c.ch in STATIC_CHARSET} <= set(fx.static_colors)
    # the pattern changes with time and seed, and is gone by the end
    assert snow_cells(fx, big, t + 0.05) != cells
    assert snow_cells(on(static=0.6, seed=5), big, t) != cells
    late = at_u(fx, 0.999)
    assert len(snow_cells(fx, big, late)) < len(cells) / 20


def test_static_off_and_bool():
    big = Grid(80, 30)
    c = STAGES_ON[2]
    fx = on(static=False)
    assert fx.static == 0.0 and snow_cells(fx, big, at_u(fx, c + 0.2 * (1 - c))) == []
    assert CRT(static=True).static == 0.4


def test_validation():
    with pytest.raises(ValueError):
        CRT(mode="sideways")
    with pytest.raises(ValueError):
        CRT(static_charset="")
    with pytest.raises(ValueError):
        CRT(static_colors=())


def test_defaults():
    fx = CRT()
    assert (fx.start, fx.duration, fx.mode, fx.seed) == (0.0, 1.2, "on", 0)
    assert fx.glow_color == CRT_GLOW and fx.end == fx.end_for(Grid(3, 3)) == 1.2
    assert isinstance(fx.apply(Grid.from_text("ab"), 0.5).cells[0], Cell)
