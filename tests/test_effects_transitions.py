"""Morph and Reverse at fixed t: which cells show source, scramble or target."""

import copy
import unicodedata

import pytest

from asciifx.core import Cell, Grid
from asciifx.effects import MORPH_CHARSET, Decode, Morph, Reverse, Typewriter, chain, is_blank
from asciifx.effects.decode import BRAILLE_CHARSET

RED, BLUE, HL = (255, 0, 0), (0, 0, 255), (9, 9, 9)


def row(text: str, fg=RED) -> Grid:
    return Grid.from_text(text, fg=fg)


def morph(target: Grid, **kw) -> Morph:
    return Morph(target, **{"start": 1.0, "duration": 1.0, "rate": 10.0, **kw})


def check_frame(m: Morph, src: Grid, t: float) -> dict[str, set[int]]:
    """Assert the per-cell rules at t; return cell indices by state."""
    out = m.apply(src, t)
    starts = m.window_starts(src)
    p = (t - m.start) / m.duration
    states: dict[str, set[int]] = {"source": set(), "scramble": set(), "target": set(), "blank": set()}
    for i, (s, d, got) in enumerate(zip(src.cells, m.target.cells, out.cells)):
        lo = starts[i]
        if lo > p:
            assert got == s, i
            states["source"].add(i)
        elif lo <= p - m.scramble:
            assert got == d, i
            states["target"].add(i)
        elif is_blank(s) and is_blank(d):
            assert got == s, i
            states["blank"].add(i)
        else:
            ref = s if is_blank(d) else d
            assert got.ch in m.charset, i
            assert got.fg == (ref.fg if m.highlight is None else m.highlight), i
            assert got.bg == ref.bg, i
            states["scramble"].add(i)
    return states


# --- Morph -------------------------------------------------------------------


def test_pure_sweep_without_scramble_switches_column_by_column():
    src, dst = row("AAAA\nAAAA"), row("BBBB\nBBBB", fg=BLUE)
    m = morph(dst, sweep=1.0, scramble=0.0)
    assert m.window_starts(src) == (0.0, 1 / 3, 2 / 3, 1.0) * 2
    assert m.apply(src, 1.5).to_text() == "BBAA\nBBAA"
    assert m.apply(src, 1.7).to_text() == "BBBA\nBBBA"
    assert m.apply(src, 1.5).get(0, 0).fg == BLUE and m.apply(src, 1.5).get(3, 0).fg == RED


def test_scramble_window_snapshot():
    src, dst = row("AAAA"), row("BBBB", fg=BLUE)
    m = morph(dst, sweep=1.0, scramble=0.5, highlight=HL, charset="?")
    assert m.window_starts(src) == (0.0, 1 / 6, 1 / 3, 0.5)
    # p = 0.3: windows that opened by 0.3 scramble, none has closed yet (closes at lo + 0.5)
    assert m.apply(src, 1.3).to_text() == "??AA"
    assert m.apply(src, 1.3).get(0, 0) == Cell("?", HL)
    # p = 0.6: cell 0's window closed at 0.5; cells 1..3 opened
    assert m.apply(src, 1.6).to_text() == "B???"
    assert check_frame(m, src, 1.6) == {"source": set(), "scramble": {1, 2, 3}, "target": {0}, "blank": set()}


@pytest.mark.parametrize("t", [1.05, 1.2, 1.35, 1.5, 1.65, 1.8, 1.95])
@pytest.mark.parametrize("sweep", [0.0, 0.4, 1.0])
def test_cells_follow_their_windows(t, sweep):
    src = Grid.from_text("⣿⣿⣿  ⣿⣿⣿\n⣿ ⣿⣿⣿⣿  \n  ⣿⣿⣿⣿⣿⣿", fg=RED)  # 8x3
    dst = Grid.from_text("ABC DE F\n GHIJ  K\nLMN  OPQ", fg=BLUE)  # 8x3
    assert (src.width, src.height) == (dst.width, dst.height) == (8, 3)
    dst.set(3, 0, Cell(" ", None, (1, 1, 1)))  # background-only: content, not blank
    for hl in (HL, None):
        states = check_frame(morph(dst, sweep=sweep, highlight=hl), src, t)
        assert sum(map(len, states.values())) == src.width * src.height


def test_start_and_end_states():
    src, dst = row("abcd"), row("wxyz", fg=BLUE)
    m = morph(dst)
    for t in (0.0, 0.99, 1.0):
        assert m.apply(src, t) == src
    for t in (2.0, 2.5):
        assert m.apply(src, t) == dst
    assert m.end == m.end_for(src) == 2.0
    assert Morph(dst, start=1.0, duration=0.0).apply(src, 1.0) == dst  # end wins


def test_blank_in_both_stays_blank_and_one_sided_cells_morph():
    src = Grid.from_text("A  ", fg=RED)
    dst = Grid.from_text("  B", fg=BLUE)
    m = morph(dst, sweep=0.0, scramble=1.0, highlight=None)  # everything scrambles all along
    out = m.apply(src, 1.5)
    assert is_blank(out.get(1, 0))
    assert out.get(0, 0).fg == RED and out.get(0, 0).ch in m.charset  # source-only: source color
    assert out.get(2, 0).fg == BLUE and out.get(2, 0).ch in m.charset  # target-only: target color
    assert m.apply(Grid(3, 1), 1.5) != Grid(3, 1)  # appears out of nothing
    assert Morph(Grid(3, 1)).apply(Grid(3, 1), 0.5) == Grid(3, 1)  # nothing to morph


def test_glyphs_change_at_rate():
    src = Grid.from_text("\n".join(["#" * 40] * 10), fg=RED)
    dst = Grid.from_text("\n".join(["%" * 40] * 10), fg=BLUE)
    m = morph(dst, scramble=1.0, rate=10.0)
    assert m.apply(src, 1.41) == m.apply(src, 1.49)  # same 0.1 s slot
    assert m.apply(src, 1.41) != m.apply(src, 1.51)


def test_pure_deterministic_and_seeded():
    src = Grid.from_text("\n".join(["⣿⣿⣿⣿⣿⣿⣿⣿"] * 4), fg=RED)
    dst = Grid.from_text("\n".join(["01010101"] * 4), fg=BLUE)
    before_src, before_dst = copy.deepcopy(src), copy.deepcopy(dst)
    m = morph(dst)
    out = m.apply(src, 1.5)
    assert out is not src and out.cells is not src.cells
    assert m.apply(src, 1.5) == out
    assert morph(dst, seed=1).apply(src, 1.5) != morph(dst, seed=2).apply(src, 1.5)
    assert src == before_src and dst == before_dst
    dst.set(0, 0, Cell("Z"))  # the target is copied on construction
    assert m.target.get(0, 0).ch == "0"


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        morph(row("ab")).apply(row("abc"), 1.5)
    with pytest.raises(ValueError):
        Morph(row("ab"), charset="")


def test_default_charset_mixes_braille_and_ascii():
    assert any(ch in BRAILLE_CHARSET for ch in MORPH_CHARSET)
    assert any(ch.isascii() and ch.isalnum() for ch in MORPH_CHARSET)
    assert any(ch.isascii() and not ch.isalnum() for ch in MORPH_CHARSET)
    assert " " not in MORPH_CHARSET
    for ch in MORPH_CHARSET:
        assert unicodedata.east_asian_width(ch) not in ("W", "F"), ch
    assert Morph(row("a")).charset == MORPH_CHARSET


def test_morph_is_hashable_and_comparable():
    a, b = Morph(row("ab"), seed=1), Morph(row("ab"), seed=1)
    assert a == b and hash(a) == hash(b)
    assert a != Morph(row("ac"), seed=1)


# --- Reverse -------------------------------------------------------------------


def dissolve(**kw) -> Reverse:
    return Reverse(Decode(**{"start": 1.0, "duration": 1.0, "appear": 0.6, "sweep": 1.0,
                             "charset": "?", "highlight": HL, **kw}))


def test_reverse_mirrors_the_effect_inside_its_window():
    g = row("ABCDE\nFGHIJ")
    r = dissolve()
    assert (r.start, r.end, r.end_for(g)) == (1.0, 2.0, 2.0)
    for t in (1.0, 1.1, 1.37, 1.5, 1.8, 1.99):
        assert r.apply(g, t) == r.effect.apply(g, 3.0 - t)


def test_dissolve_snapshot():
    g = row("ABCDE")
    r = dissolve()  # sweep=1: column x locks at x / 4 of the duration
    assert r.apply(g, 0.5) == g  # before: the plain grid
    assert r.apply(g, 1.0) == g
    # t = 1.3 -> decode time p = 0.7: x=0,1,2 (lock 0, .25, .5) locked; x=3 (lock .75,
    # appears at .45) scrambled; x=4 (lock 1, appears at .6) scrambled
    assert r.apply(g, 1.3).to_text() == "ABC??"
    # t = 1.6 -> p = 0.4: x=0,1 (lock 0, .25) locked, x=2 (lock .5, appears at .3)
    # scrambled, x=3 (appears at .45) and x=4 (appears at .6) gone
    assert r.apply(g, 1.6).to_text() == "AB?"
    assert r.apply(g, 1.6).get(2, 0) == Cell("?", HL)
    # from the end on: nothing, constant
    for t in (2.0, 2.5, 10.0):
        assert r.apply(g, t) == Grid(5, 1)


def test_reverse_of_typewriter_untypes():
    g = row("abcd")
    r = Reverse(Typewriter(start=0.0, duration=1.0, cursor=None))
    assert r.apply(g, -1.0) == g
    assert r.apply(g, 0.5).to_text() == "ab"
    assert r.apply(g, 1.0) == Grid(4, 1)


def test_reverse_is_pure_and_accepts_effects_without_start():
    g = row("abcd")
    before = copy.deepcopy(g)
    r = Reverse(chain(Decode(duration=1.0, appear=0.5)))
    assert r.start == 0.0 and r.end == 1.0
    out = r.apply(g, 0.5)
    assert out is not g and out == r.apply(g, 0.5)
    assert r.apply(g, -0.1) == g and r.apply(g, -0.1) is not g
    assert r.apply(g, 1.0) == Grid(4, 1)
    assert g == before
