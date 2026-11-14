"""The rules every effect follows, plus base helpers, Chain, registry and palettes."""

import copy
import math

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.effects import (
    BLOOD, EFFECTS, GREEN, PALETTES, Chain, Decode, Fire, Glitch, Hologram, Matrix, Morph, Palette,
    Reverse, Starfield, Typewriter, chain, fade, hash32, is_blank, make, noise,
)
from asciifx.effects.base import T_LIMIT, cell_hashes, finite_t, gradient, hash_table, time_bucket


def mixed_grid(w: int = 30, h: int = 10) -> Grid:
    g = Grid(w, h)
    for y in range(h):
        for x in range(w):
            u = noise(42, x, y)
            if u < 0.4:
                g.set(x, y, Cell("⣿⡇#@%"[int(u * 12)], (200, 30, 30)))
            elif u < 0.45:
                g.set(x, y, Cell(" ", None, (40, 0, 0)))
            elif u < 0.5:
                g.set(x, y, Cell("x"))
    return g


EFFECT_CASES = {
    **{name: cls() for name, cls in EFFECTS.items()},
    "typewriter_duration": Typewriter(start=0.2, duration=0.5),
    "decode_appear": Decode(start=0.2, appear=0.6, sweep=1.0, highlight=None),
    "matrix_rain_only": Matrix(reveal=False, duration=0.7, seed=3),
    "glitch_heavy": Glitch(intensity=1.0, bursts=5, seed=9),
    "chain": chain(Decode(duration=0.6), Matrix(duration=0.5), Glitch(start=0.4, duration=0.4)),
    "blood": chain(*(make(name, BLOOD) for name in ("decode", "matrix", "glitch"))),
}
GRIDS = {
    "mixed": mixed_grid(),
    "blank": Grid(7, 3),
    "empty": Grid(0, 0),
    "one": Grid.from_text("@"),
    "wide": mixed_grid(100, 25),
}


def sample_times(effect, grid) -> list[float]:
    end = effect.end_for(grid)
    return [-0.5, 0.0] + [end * k / 23 for k in range(1, 23)] + [end, end + 1.0]


@pytest.mark.parametrize("grid_name", GRIDS)
@pytest.mark.parametrize("name", EFFECT_CASES)
def test_contract(name, grid_name):
    effect, grid = EFFECT_CASES[name], GRIDS[grid_name]
    before = copy.deepcopy(grid)
    end = effect.end_for(grid)
    for t in sample_times(effect, grid):
        out = effect.apply(grid, t)
        assert out is not grid and out.cells is not grid.cells
        assert (out.width, out.height, len(out.cells)) == (grid.width, grid.height, len(grid.cells))
        assert all(isinstance(c, Cell) and len(c.ch) == 1 for c in out.cells)
        assert effect.apply(grid, t) == out  # pure: same t, same frame
        if t >= end:
            assert out == grid
    assert grid == before  # input never mutated


SMALL = mixed_grid(12, 5)
NONFINITE_CASES = {
    **EFFECT_CASES,
    "fire_endless": Fire(duration=None),
    "starfield_endless": Starfield(duration=None),
    "hologram_endless": Hologram(duration=None),
    "morph": Morph(target=Grid.from_text("\n".join(["morph target"] * 5))),  # 12x5, like SMALL
    "reverse_typewriter": Reverse(Typewriter()),
}
TRANSITIONS = {"morph", "reverse_typewriter"}  # not the identity once done


@pytest.mark.parametrize("t", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("name", NONFINITE_CASES)
def test_non_finite_t(name, t):
    effect, grid = NONFINITE_CASES[name], SMALL
    before = copy.deepcopy(grid)
    out = effect.apply(grid, t)
    assert isinstance(out, Grid) and out is not grid and out.cells is not grid.cells
    assert (out.width, out.height, len(out.cells)) == (grid.width, grid.height, len(grid.cells))
    assert all(isinstance(c, Cell) and len(c.ch) == 1 for c in out.cells)
    assert effect.apply(grid, t) == out
    assert grid == before
    if math.isnan(t):  # NaN reads as the start of the timeline
        assert out == effect.apply(grid, 0.0)
    elif t > 0:  # the finished state
        assert out == effect.apply(grid, effect.end_for(grid) + 1.0)
        if name not in TRANSITIONS:
            assert out == grid
    else:  # long before start (where that state is static: Decode's scramble flickers)
        early = effect.apply(grid, -1e6)
        if effect.apply(grid, -2e6) == early:
            assert out == early


@pytest.mark.parametrize("name", EFFECT_CASES)
def test_frames_actually_change_on_content(name):
    effect, grid = EFFECT_CASES[name], GRIDS["mixed"]
    frames = {tuple(effect.apply(grid, t).cells) for t in sample_times(effect, grid)}
    assert len(frames) > 2


def test_registry_and_defaults():
    assert list(EFFECTS) == [
        # every registered effect, in registration order
        "typewriter", "decode", "matrix", "glitch", "crt", "assemble", "glint",
        "fire", "starfield", "hologram", "lightning", "shockwave", "drip",
    ]
    for cls in EFFECTS.values():
        e = cls()
        assert e.start == 0.0 and e.seed == 0
    assert Decode().end == 1.0 and Matrix().end == 1.5 and Glitch().end == 1.0
    assert Typewriter(duration=2.0).end == 2.0


def test_effects_are_hashable_values():
    assert Decode(seed=1) == Decode(seed=1) and hash(Decode(seed=1)) == hash(Decode(seed=1))
    with pytest.raises(AttributeError):
        Decode().start = 1.0  # type: ignore[misc]


def test_seed_changes_output():
    g = GRIDS["mixed"]
    for cls in (Decode, Matrix):
        assert cls(seed=1).apply(g, 0.5) != cls(seed=2).apply(g, 0.5)
    assert Glitch(seed=1).windows() != Glitch(seed=2).windows()


# --- base helpers ----------------------------------------------------------


def test_hash32_pinned_values():
    # Pinned so snapshot tests elsewhere stay meaningful.
    assert hash32(0) == 4282259411
    assert hash32(0, 1, 2) == 2496488887
    assert hash32(7, -1) == hash32(7, 0xFFFFFFFF)
    assert hash32(1, 2) != hash32(2, 1)
    assert noise(0, 1, 2) == hash32(0, 1, 2) / 2**32


def test_noise_range_and_spread():
    vals = [noise(5, x, y) for x in range(100) for y in range(25)]
    assert all(0.0 <= v < 1.0 for v in vals)
    assert 0.45 < sum(vals) / len(vals) < 0.55
    assert len(set(vals)) == len(vals)
    for lo in (0.0, 0.25, 0.5, 0.75):  # roughly uniform
        assert 0.2 < sum(lo <= v < lo + 0.25 for v in vals) / len(vals) < 0.3


def test_cached_tables_match_hash32():
    assert cell_hashes(3, 9, 4, 2) == tuple(hash32(3, 9, x, y) for y in range(2) for x in range(4))
    assert hash_table(3, 9, 5) == tuple(hash32(3, 9, j) for j in range(5))


def test_is_blank():
    assert is_blank(BLANK) and is_blank(Cell(" ", (1, 2, 3)))
    assert not is_blank(Cell("x")) and not is_blank(Cell(" ", None, (1, 2, 3)))


def test_gradient_and_time_bucket():
    assert gradient(((0, 0, 0), (100, 200, 50)), 3) == ((0, 0, 0), (50, 100, 25), (100, 200, 50))
    assert gradient(((0, 0, 0), (10, 10, 10), (20, 0, 0)), 5) == (
        (0, 0, 0), (5, 5, 5), (10, 10, 10), (15, 5, 5), (20, 0, 0),
    )
    assert gradient(((1, 2, 3),), 2) == ((1, 2, 3), (1, 2, 3)) and gradient(((1, 2, 3),), 0) == ()
    assert [time_bucket(t, 10) for t in (0.0, 0.09, 0.1, 0.25, -0.01)] == [0, 0, 1, 2, -1]
    assert time_bucket(0.05, 10, 0.5) == 1 and time_bucket(5.0, 0) == 0


def test_finite_t():
    assert finite_t(math.nan) == 0.0
    assert finite_t(math.inf) == T_LIMIT and finite_t(-math.inf) == -T_LIMIT
    assert finite_t(1.5) == 1.5 and finite_t(-1e300) == -1e300 and finite_t(3) == 3


# --- Chain ------------------------------------------------------------------


def test_chain_applies_in_order_with_same_t():
    g = Grid.from_text("abcd")
    tw = Typewriter(cps=10)
    dec = Decode(sweep=1.0, duration=1.0, charset="?")
    c = chain(tw, dec)
    assert isinstance(c, Chain) and c.effects == (tw, dec)
    for t in (0.0, 0.15, 0.25, 0.5):
        assert c.apply(g, t) == dec.apply(tw.apply(g, t), t)
    # "ab" typed plus cursor; the sweep locks "a" (x=0) first, "b" and the cursor not yet.
    assert c.apply(g, 0.25).to_text() == "a??"
    assert chain().apply(g, 1.0) == g and chain().apply(g, 1.0) is not g


def test_chain_end_is_max_of_parts():
    c = chain(Decode(duration=0.5), Glitch(start=1.0, duration=0.7), Matrix(duration=1.2))
    assert c.end == pytest.approx(1.7)
    g = GRIDS["mixed"]
    assert c.end_for(g) == pytest.approx(1.7)
    text = Grid.from_text("abcdefghij")
    assert chain(Typewriter(start=1.5, cps=4), Decode()).end_for(text) == pytest.approx(4.0)
    with pytest.raises(ValueError):
        chain(Typewriter(), Decode()).end
    assert chain().end == 0.0


# --- registry helpers and palettes ------------------------------------------


def test_make_applies_palette_then_params():
    m = make("matrix", BLOOD, duration=0.8, head=(1, 2, 3))
    assert isinstance(m, Matrix) and m.duration == 0.8
    assert m.head == (1, 2, 3) and m.tail == BLOOD.tail
    assert make("decode", "blood").highlight == BLOOD.highlight
    assert make("glitch", BLOOD).colors == BLOOD.glitch
    assert make("typewriter", BLOOD).cursor_fg == BLOOD.cursor
    assert make("decode") == Decode()


def test_palettes():
    assert PALETTES == {"green": GREEN, "blood": BLOOD}
    assert GREEN.kwargs("matrix") == {"head": Matrix().head, "tail": Matrix().tail}
    assert GREEN.kwargs(Decode) == {"highlight": Decode().highlight}
    assert GREEN.kwargs("glitch") == {"colors": Glitch().colors}
    assert BLOOD.kwargs("chain") == {}
    assert BLOOD.head == (255, 220, 220) and BLOOD.tail[0] == (230, 30, 30)
    assert Palette((1, 1, 1), [(2, 2, 2)], (3, 3, 3), [(4, 4, 4)]).tail == ((2, 2, 2),)


def test_fade_and_mono():
    assert fade((200, 100, 0), 3, darkest=0.5) == ((200, 100, 0), (150, 75, 0), (100, 50, 0))
    assert fade((10, 10, 10), 1) == ((10, 10, 10),)
    p = Palette.mono((255, 0, 0))
    assert p.tail[0] == (255, 0, 0) and p.cursor == (255, 0, 0)
    assert p.head[0] == 255 and p.head[1] > 150
