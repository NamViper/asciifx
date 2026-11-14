"""Fire at fixed t: snapshot, flame shape, layering, envelope, purity, endless mode."""

import copy
import math
import unicodedata

import pytest

from asciifx.core import Cell, Grid
from asciifx.effects import BLOOD_FIRE_COLORS, EFFECTS, FIRE_COLORS, FIRE_RAMP_ASCII, Fire
from asciifx.effects import fire as fire_mod
from asciifx.effects.base import gradient
from asciifx.effects.fire import FIRE_RAMP

GREEN = (0, 255, 0)


def art() -> Grid:
    g = Grid(12, 6)
    g.put_text(4, 1, "HI", fg=GREEN)
    return g


def busy(w: int = 20, h: int = 10) -> Grid:
    """Text on every other row, plus a space with a background (not blank)."""
    g = Grid(w, h)
    for y in range(0, h, 2):
        g.put_text(y % 3, y, "ab cd" * w, fg=GREEN)
    g.set(3, h - 1, Cell(" ", None, (9, 9, 9)))
    return g


def flames(g: Grid) -> int:
    return sum(c.ch != " " for c in g.cells)


def top_flame_row(g: Grid) -> int:
    """Height (rows from the bottom, 1 = bottom row) of the highest flame; 0 if none."""
    for y in range(g.height):
        if any(c.ch != " " for c in g.cells[y * g.width : (y + 1) * g.width]):
            return g.height - y
    return 0


# --- exact cells -------------------------------------------------------------


def test_snapshot():
    out = Fire(seed=7, duration=None, height=0.8).apply(art(), 1.25)
    assert out.to_text() == (
        "\n"
        "    HI    ..\n"
        ":    .....::\n"
        "▓▒▒▒▒░░░:::░\n"
        "██████▓▓▒▒░░\n"
        "████████▓▒▒▒"
    )
    assert [out.get(x, 3).fg for x in (0, 3, 6, 9)] == [
        (255, 196, 40), (255, 128, 4), (241, 93, 0), (193, 28, 0),
    ]
    assert [out.get(x, 5).fg for x in (0, 3, 6, 9)] == [
        (255, 239, 168), (255, 239, 168), (255, 251, 211), (255, 169, 26),
    ]
    assert out.get(4, 1) == Cell("H", GREEN) and out.get(0, 0) == Cell()
    ascii_ramp = Fire(seed=7, duration=None, height=0.8, ramp=FIRE_RAMP_ASCII)
    assert ascii_ramp.apply(art(), 1.25).to_text() == (
        "\n    HI    ..\n:    ....,::\n#*++++==;;;=\n@@@@@@%#*+==\n%%%%%@@@%*+*"
    )


# --- shape -------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 5])
def test_intensity_falls_with_height(seed):
    w, h, ramp = 60, 30, FIRE_RAMP_ASCII
    f = Fire(duration=None, height=0.5, ramp=ramp, seed=seed)
    times = [1.0 + 0.37 * k for k in range(40)]
    level = [0.0] * h  # mean ramp index per row, bottom row first
    warmth = [0.0] * h  # mean red + green of flame cells per row
    count = [0] * h
    for t in times:
        out = f.apply(Grid(w, h), t)
        for row in range(h):
            for c in out.cells[(h - 1 - row) * w : (h - row) * w]:
                level[row] += ramp.index(c.ch) / (w * len(times))
                if c.fg is not None:
                    warmth[row] += c.fg[0] + c.fg[1]
                    count[row] += 1
    assert all(upper < lower for lower, upper in zip(level, level[1:]))
    assert level[0] > 0.7 * (len(ramp) - 1)  # white-hot base...
    assert level[h // 2] < 0.6 * level[0]  # ...fading by height * h rows up
    assert level[-1] < 0.01 * level[0]  # and nearly nothing at the top
    mean_warmth = [s / n for s, n in zip(warmth, count) if n]
    assert mean_warmth[0] > mean_warmth[h // 3] > mean_warmth[2 * h // 3]


def test_flames_vary_along_a_row_and_rise_over_time():
    f = Fire(duration=None, height=0.5, ramp=FIRE_RAMP_ASCII)
    a, b = f.apply(Grid(60, 30), 2.0), f.apply(Grid(60, 30), 2.1)
    mid = a.cells[20 * 60 : 21 * 60]  # 10 rows up: ragged tongues, not a flat band
    assert len({c.ch for c in mid}) >= 4
    assert a != b  # animated...
    assert f.apply(Grid(60, 30), 2.0) == a  # ...as a function of t alone


def test_height_param_raises_the_flames():
    g = Grid(40, 30)
    low = [top_flame_row(Fire(duration=None, height=0.2).apply(g, t)) for t in (1, 2, 3, 4)]
    high = [top_flame_row(Fire(duration=None, height=0.7).apply(g, t)) for t in (1, 2, 3, 4)]
    assert sum(high) > 2 * sum(low) and min(low) >= 3
    assert Fire(height=0).apply(art(), 1.0) == art()


# --- layering ----------------------------------------------------------------


@pytest.mark.parametrize("t", [0.7, 1.0, 2.3])
def test_under_draws_only_in_blank_cells(t):
    g = busy()
    fire = Fire(duration=None, height=0.9)
    alone = fire.apply(Grid(g.width, g.height), t)  # the flames by themselves
    out = fire.apply(g, t)
    for got, src, flame in zip(out.cells, g.cells, alone.cells):
        blank = src.ch == " " and src.bg is None
        assert got == (flame if blank and flame.ch != " " else src)
    assert flames(out) > flames(g)  # something burns between the letters
    full = Grid.filled(10, 4, Cell("#", GREEN))
    assert fire.apply(full, t) == full


@pytest.mark.parametrize("t", [0.7, 1.0, 2.3])
def test_over_draws_on_top_of_everything(t):
    g = busy()
    fire = Fire(duration=None, height=0.9, layer="over")
    alone = fire.apply(Grid(g.width, g.height), t)
    out = fire.apply(g, t)
    for got, src, flame in zip(out.cells, g.cells, alone.cells):
        assert got == (src if flame.ch == " " else flame)
    bottom = out.cells[-g.width :]
    assert all(c.ch in FIRE_RAMP[1:] for c in bottom)  # the base always burns
    full = Grid.filled(10, 4, Cell("#", GREEN))
    assert fire.apply(full, t) != full


# --- envelope ----------------------------------------------------------------


@pytest.mark.parametrize("t", [-1.0, 0.0, 0.99, 3.0, 3.01, 50.0])
def test_identity_outside_start_to_end(t):
    f = Fire(start=1.0, duration=2.0)
    assert f.end == 3.0 and f.end_for(art()) == 3.0
    out = f.apply(art(), t)
    assert out == art() and out.cells is not art().cells


def test_grows_in_and_dies_down():
    f = Fire(start=1.0, duration=3.0, fade=0.6, height=0.6)
    g = Grid(40, 20)
    assert [f.envelope(t) for t in (0.9, 1.0, 1.6, 2.5, 3.4, 4.0)] == [0, 0, 1, 1, 1, 0]
    assert 0 < f.envelope(1.2) < f.envelope(1.4) < 1 and 1 > f.envelope(3.7) > f.envelope(3.9) > 0
    grow = [top_flame_row(f.apply(g, t)) for t in (1.02, 1.15, 1.3, 1.6)]
    die = [top_flame_row(f.apply(g, t)) for t in (3.4, 3.7, 3.85, 3.98)]
    assert grow == sorted(grow) and grow[0] < grow[-1]
    assert die == sorted(die, reverse=True) and die[0] > die[-1]
    assert flames(f.apply(g, 1.05)) < flames(f.apply(g, 2.0)) > flames(f.apply(g, 3.95))
    assert f.apply(g, 1.02) != g  # flames from the very start


def test_short_duration_and_no_fade():
    g = Grid(20, 8)
    short = Fire(duration=0.5, fade=0.6)  # fade clipped to half the duration
    assert short.envelope(0.25) == 1.0 and short.apply(g, 0.25) != g
    assert short.apply(g, 0.5) == g
    sharp = Fire(duration=1.0, fade=0.0)
    assert sharp.envelope(0.0) == 1.0 and sharp.envelope(0.999) == 1.0
    assert Fire(duration=0.0).apply(g, 0.0) == g and Fire(duration=-1.0).end == 0.0


# --- endless mode ------------------------------------------------------------


@pytest.mark.parametrize("t", [0.7, 10.0, 1000.0, 1e5])
def test_endless_burns_forever(t):
    f = Fire(duration=None)
    assert f.end == math.inf and f.end_for(art()) == math.inf
    assert f.envelope(t) == 1.0
    assert flames(f.apply(Grid(30, 10), t)) > 30


def test_endless_still_starts_at_start():
    f = Fire(start=2.0, duration=None)
    g = Grid(30, 10)
    assert f.apply(g, 1.9) == g
    assert 0 < flames(f.apply(g, 2.1)) < flames(f.apply(g, 3.0))
    assert f.apply(g, math.inf) == g  # t >= end (inf): identity, as for every effect


# --- purity ------------------------------------------------------------------


def test_input_not_mutated():
    for layer in ("under", "over"):
        g = busy()
        before = copy.deepcopy(g)
        f = Fire(duration=2.0, height=0.9, layer=layer)
        for t in (-1.0, 0.0, 0.1, 0.5, 1.0, 1.9, 2.0, 5.0):
            out = f.apply(g, t)
            assert out is not g and out.cells is not g.cells
        assert g == before


def test_deterministic_and_seeded():
    g = busy(40, 15)
    a = Fire(duration=None, seed=3)
    assert a.apply(g, 1.7) == Fire(duration=None, seed=3).apply(g, 1.7)
    later_first = [a.apply(g, t) for t in (2.5, 1.7)]  # call order doesn't matter
    assert later_first[1] == a.apply(g, 1.7)
    assert a.apply(g, 1.7) != Fire(duration=None, seed=4).apply(g, 1.7)
    assert a == Fire(duration=None, seed=3) and hash(a) == hash(Fire(duration=None, seed=3))
    with pytest.raises(AttributeError):
        a.seed = 1  # type: ignore[misc]


# --- colors and glyphs -------------------------------------------------------


@pytest.mark.parametrize(
    "stops", [FIRE_COLORS, BLOOD_FIRE_COLORS, ((0, 0, 0), (0, 0, 255)), ((10, 20, 30),)]
)
def test_colors_follow_the_given_stops(stops):
    out = Fire(duration=None, colors=stops).apply(Grid(30, 12), 1.3)
    shades = set(gradient(tuple(stops), 64))
    drawn = [c for c in out.cells if c.ch != " "]
    assert drawn and all(c.fg in shades and c.bg is None for c in drawn)


def test_colors_run_from_cool_to_hot():
    out = Fire(duration=None, colors=BLOOD_FIRE_COLORS, height=0.6).apply(Grid(40, 20), 1.0)
    by_glyph = {c.ch: c.fg for c in out.cells if c.ch != " "}
    assert sum(by_glyph["█"]) > sum(by_glyph["▒"]) > sum(by_glyph["."])
    assert Fire(colors=[[0, 0, 0], [9, 9, 9]]).colors == ((0, 0, 0), (9, 9, 9))  # hashable


def test_glyphs_are_single_width_ramp_glyphs():
    g = busy(50, 20)
    for ramp in (FIRE_RAMP, FIRE_RAMP_ASCII):
        out = Fire(duration=None, ramp=ramp, layer="over").apply(g, 1.1)
        assert {c.ch for c in out.cells} <= set(ramp) | {c.ch for c in g.cells}
        assert all(unicodedata.east_asian_width(c.ch) not in "WF" for c in out.cells)


def test_cell_table_indices_stay_in_range(monkeypatch):
    """apply() relies on its intensity bounds instead of clamping each cell."""

    class Strict(tuple):
        def __getitem__(self, i):
            assert 0 <= i < len(self), i
            return tuple.__getitem__(self, i)

    real = fire_mod._cell_table
    monkeypatch.setattr(fire_mod, "_cell_table", lambda r, c: Strict(real(r, c)))
    for w, h in ((1, 1), (3, 40), (80, 2), (50, 25)):
        for height in (0.01, 0.3, 1.0, 3.0):
            for t in (0.001, 0.05, 1.0, 3.99, 3.9999, 1e4):
                Fire(height=height, duration=4.0 if t < 4 else None).apply(Grid(w, h), t)


def test_registered_with_usable_defaults():
    assert EFFECTS["fire"] is Fire
    f = Fire()
    assert (f.start, f.duration, f.seed, f.layer, f.end) == (0.0, 4.0, 0, "under", 4.0)
    assert f.apply(art(), 1.0) != art()


@pytest.mark.parametrize(
    "kw", [{"layer": "sideways"}, {"colors": ()}, {"ramp": " "}, {"ramp": ""}]
)
def test_rejects_bad_params(kw):
    with pytest.raises(ValueError):
        Fire(**kw)


def test_palettes_recolor_fire_and_stars():
    from asciifx.effects import BLOOD, GREEN, make

    blood = make("fire", BLOOD).colors
    assert blood[0] == (0, 0, 0) and blood[-1] == BLOOD.head and blood[2] == BLOOD.tail[0]
    assert make("fire", GREEN).colors[-1] == GREEN.head
    assert make("starfield", BLOOD).colors[-1] == BLOOD.head
