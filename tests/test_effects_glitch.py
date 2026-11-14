"""Glitch bursts: timing, identity outside bursts, and what a burst may change."""

import unicodedata

import pytest

from asciifx.core import BLANK, Grid
from asciifx.effects import BLOOD, Glitch
from asciifx.effects.glitch import GLITCH_CHARSET, GLITCH_COLORS

RED = (255, 0, 0)


def grid() -> Grid:
    return Grid.from_text("ABCDEFGHIJ\nKLMNOPQRST\nUVWXYZ0123\n4567890abc", fg=RED)


def gl(**kw) -> Glitch:
    return Glitch(**{"start": 1.0, "duration": 0.9, "intensity": 1.0, **kw})


def times(start: float, stop: float, step: float = 0.004) -> list[float]:
    return [start + i * step for i in range(int((stop - start) / step) + 1)]


def test_windows():
    e = gl()
    wins = e.windows()
    flat = [v for win in wins for v in win]
    assert flat == pytest.approx([1.2035049, 1.2844172, 1.3078274, 1.3998308, 1.6626904, 1.7437549])
    for k, (b, end) in enumerate(wins):
        assert 1.0 + k * 0.3 <= b < end <= 1.0 + (k + 1) * 0.3  # one per slot, no overlap
        assert 0.05 <= end - b <= 0.12


def test_windows_are_clamped_to_their_slot():
    wins = Glitch(duration=0.3, bursts=10).windows()
    assert len(wins) == 10
    assert all(end - b == pytest.approx(0.03) for b, end in wins)
    assert Glitch(bursts=0).windows() == [] and Glitch(duration=0).windows() == []


def test_identity_outside_bursts_changed_inside():
    e, g = gl(), grid()
    for t in times(0.0, 2.2):
        out = e.apply(g, t)
        if e.burst_at(t) is None:
            assert out == g, t
        else:
            assert out != g, t


def test_snapshot_first_burst():
    out = gl().apply(grid(), 1.21)
    assert out.to_text() == "DE^GHI#\n KLMNOPQRS\n UVWX*Z012\n  4567890a"
    assert {c.fg for c in out.cells if c.ch != " "} == {RED, *GLITCH_COLORS}


def test_burst_only_shifts_rows_swaps_glyphs_and_colors():
    """Each row of a burst frame is its source row torn sideways (cells pushed
    off an edge leave blanks), with some glyphs and colors swapped."""
    e, g = gl(), grid()  # the source grid has no blanks
    allowed_fg = {RED, *e.colors}
    for b, end in e.windows():
        for t in times(b, end, 0.01):
            out = e.apply(g, t)
            for y in range(g.height):
                src = [g.get(x, y) for x in range(g.width)]
                got = [out.get(x, y) for x in range(g.width)]
                lead = next((x for x, c in enumerate(got) if c != BLANK), g.width)
                trail = next((x for x, c in enumerate(reversed(got)) if c != BLANK), 0)
                seg = got[lead : g.width - trail]
                assert BLANK not in seg
                assert any(
                    all(
                        0 <= lead + i - dx < g.width
                        and c.ch in (src[lead + i - dx].ch, *e.charset)
                        and c.fg in allowed_fg
                        for i, c in enumerate(seg)
                    )
                    for dx in range(-9, 10)
                ), (t, y)


def test_pattern_is_stable_within_a_rate_slot():
    e, g = gl(rate=10.0), grid()
    b, end = e.windows()[0]  # 1.2035 .. 1.2844, all inside the 1.2 .. 1.3 slot
    assert e.apply(g, b) == e.apply(g, end - 1e-6)
    b, end = e.windows()[1]  # 1.3078 .. 1.3998, inside the 1.3 .. 1.4 slot
    assert e.apply(g, b) == e.apply(g, end - 1e-6)
    assert gl(rate=100.0).apply(g, 1.21) != gl(rate=100.0).apply(g, 1.23)


def test_blank_grid_stays_blank():
    e = gl()
    for b, end in e.windows():
        assert e.apply(Grid(10, 4), b) == Grid(10, 4)


def test_zero_intensity_is_identity():
    e, g = gl(intensity=0.0), grid()
    assert all(e.apply(g, t) == g for t in times(1.0, 1.9, 0.01))


def test_intensity_scales_damage():
    g = Grid.from_text("\n".join(["#" * 60] * 20), fg=RED)
    t = gl().windows()[0][0]

    def changed(intensity: float) -> int:
        out = gl(intensity=intensity).apply(g, t)
        return sum(a != b for a, b in zip(out.cells, g.cells))

    assert 0 < changed(0.2) < changed(1.0)


def test_palette_colors():
    e, g = gl(colors=BLOOD.glitch), grid()
    out = e.apply(g, 1.21)
    assert {c.fg for c in out.cells if c.ch != " "} <= {RED, *BLOOD.glitch}
    assert gl(**BLOOD.kwargs("glitch")).colors == BLOOD.glitch


def test_glyphs_are_not_wide():
    for ch in GLITCH_CHARSET:
        assert unicodedata.east_asian_width(ch) not in ("W", "F"), ch


def test_rejects_empty_charset_or_colors():
    with pytest.raises(ValueError):
        Glitch(charset="")
    with pytest.raises(ValueError):
        Glitch(colors=())
