"""Summon timeline and frames, with a fake logo loader (no image conversion)."""

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.core.layers import Layer, compose
from asciifx.effects import CRT, Assemble, Decode, Glint, Glitch
from asciifx.summon import COUNTDOWN, NO_LOGO_TIMELINE, TIMELINE, Summon

W, H = 80, 24
LOGO_FG = (0, 255, 0)
LOGO_SIZE = (30, 12)


def fake_logo(style: str, max_w: int, max_h: int) -> Grid:
    """A checkered block of braille, at most LOGO_SIZE."""
    w, h = (min(a, b) for a, b in zip(LOGO_SIZE, (max_w, max_h)))
    g = Grid(w, h)
    for y in range(h):
        for x in range(w):
            if (x + y) % 3:
                g.set(x, y, Cell("⣿", LOGO_FG))
    return g


def centred(width: int = W, height: int = H) -> Grid:
    logo = fake_logo("dots", width - 4, height - 2)
    x, y = (width - logo.width) // 2, (height - logo.height) // 2
    return compose(width, height, [Layer(logo, x, y)])


def summon(**kw) -> Summon:
    kw = {"assembly": "scatter", "finale": "shockwave", **kw}
    return Summon(kw.pop("loader", fake_logo), **kw)


def logo_cells(g: Grid) -> int:
    return sum(c.fg == LOGO_FG for c in g.cells)


def glyphs(g: Grid) -> int:
    return sum(c.ch != " " for c in g.cells)


# --- timeline -------------------------------------------------------------------


def test_timeline():
    s = summon()
    plan = s.plan()
    assert [p.name for p in plan] == [
        "countdown", "black", "crt_on", "assemble", "finale", "reveal", "hold", "crt_off", "black"
    ]
    assert [(p.start, p.end) for p in plan] == [
        (0.0, 2.4), (2.4, 2.6), (2.6, 3.8), (3.8, 7.2), (7.2, 8.6), (8.6, 9.7), (9.7, 11.7),
        (11.7, 12.7), (12.7, 13.0),
    ]  # fmt: skip
    assert all(a.end == b.start for a, b in zip(plan, plan[1:]))
    assert s.duration == pytest.approx(13.0) == pytest.approx(sum(n for _, n in TIMELINE))
    assert Summon.phase("finale") == plan[4]


@pytest.mark.parametrize(
    "t, name",
    [(-1.0, "countdown"), (0.0, "countdown"), (2.399, "countdown"), (2.4, "black"), (2.6, "crt_on"),
     (3.799, "crt_on"), (3.8, "assemble"), (7.199, "assemble"), (7.2, "finale"), (8.6, "reveal"),
     (9.7, "hold"), (11.699, "hold"), (11.7, "crt_off"), (12.699, "crt_off"), (12.7, "black"),
     (13.0, "black"), (99.0, "black")],
)  # fmt: skip
def test_phase_at_boundaries(t, name):
    assert summon().phase_at(t).name == name


def test_no_logo_timeline():
    s = summon()
    plan = s.plan(logo=False)
    assert [p.name for p in plan] == ["black", "crt_on", "hold", "crt_off", "black"]
    assert plan[-1].end == pytest.approx(sum(n for _, n in NO_LOGO_TIMELINE)) == pytest.approx(3.5)
    assert s.phase_at(2.0, logo=False).name == "hold"
    assert Summon(None).duration == pytest.approx(3.5)


def test_effects_follow_the_timeline():
    fx = summon()._effects[True]
    on, off = fx["crt_on"], fx["crt_off"]
    assert isinstance(on, CRT) and (on.mode, on.start, on.end) == ("on", 2.6, pytest.approx(3.8))
    assert isinstance(off, CRT) and (off.mode, off.start, off.end) == ("off", 11.7, pytest.approx(12.7))
    a = fx["assemble"]
    assert isinstance(a, Assemble) and a.start == 3.8
    assert a.end - a.cooling == pytest.approx(7.2)  # the last spark lands as the finale starts
    glint = fx["glint"]
    assert isinstance(glint, Glint) and 9.7 < glint.start < glint.end < 11.7
    holo = fx["holo"]
    assert glint.end <= holo.start < holo.end < 11.7  # the hologram flicker after the glint
    dec, hit = fx["decode"], fx["hit"]
    assert isinstance(dec, Decode) and dec.start == 8.6 and dec.end <= hit.start
    assert isinstance(hit, Glitch) and len(hit.windows()) == 1 and hit.end <= 9.7
    starts = [(on.start, off.end) for _, on, off in fx["countdown"]]
    assert [text for text, _, _ in fx["countdown"]] == [text for text, _, _ in COUNTDOWN]
    assert starts[0][0] == 0.0 and all(a[1] <= b[0] for a, b in zip(starts, starts[1:]))
    assert starts[-1][1] <= 2.4


# --- frames -----------------------------------------------------------------------


def test_logo_is_loaded_once_per_size_and_centred():
    calls = []

    def loader(style, w, h):
        calls.append((style, w, h))
        return fake_logo(style, w, h)

    s = summon(loader=loader, style="letters")
    for t in (0.5, 3.0, 5.0, 8.0, 10.0, 12.0):
        s.frame(t, W, H)
    assert calls == [("letters", W - 4, H - 2)]
    assert s.logo(W, H) == centred()
    assert s.duration_for(W, H) == pytest.approx(13.0)


def test_countdown_flicks_through_its_texts():
    s = summon()
    scene = s._scene(W, H)
    for text, start, length in COUNTDOWN:
        art = scene.texts[text]
        assert (art.width, art.height) == (W, H) and glyphs(art) > 0
        mid = start + 0.55 * length  # on, before the power-off: the text, CRT-styled
        assert glyphs(s.frame(mid, W, H)) == glyphs(art)
        assert logo_cells(s.frame(mid, W, H)) == 0
    assert "SUMMONING" not in scene.texts["SUMMONING"].to_text()  # big letters, not plain text
    three = scene.texts["3"]
    rows = [y for y in range(H) if any(c.ch != " " for c in three.cells[y * W : (y + 1) * W])]
    assert len(rows) > 4  # the digits are scaled up past the font's 4 rows
    assert s.frame(0.0, W, H) == Grid(W, H)  # the tube is still dark
    assert s.frame(1.03, W, H) == Grid(W, H)  # between SUMMONING and 3


def test_black_and_crt_on_show_no_logo():
    s = summon()
    assert s.frame(2.4, W, H) == Grid(W, H) and s.frame(2.55, W, H) == Grid(W, H)
    on = s._effects[True]["crt_on"]
    for t in (2.7, 3.0, 3.4, 3.7, 3.79):
        out = s.frame(t, W, H)
        assert out == on.apply(Grid(W, H), t) and logo_cells(out) == 0
    assert any(c.ch == "█" for c in s.frame(3.0, W, H).cells)  # the line
    assert s.frame(3.8, W, H) == Grid(W, H)  # the screen is on, still empty


def test_assemble_builds_the_logo():
    s = summon()
    logo = centred()
    lit = [sum(c != BLANK for c in s.frame(t, W, H).cells) for t in (3.8, 4.5, 5.5, 6.5)]
    assert lit[0] < lit[1] < lit[2]
    mid = s.frame(6.6, W, H)
    assert 0 < logo_cells(mid) < logo_cells(logo)  # some home and cooled, not all
    a = s._effects[True]["assemble"]
    assert a.apply(logo, 8.0) == logo  # cooled by 8.0, during the finale


def test_finale_and_hold_are_the_logo_with_the_effects_on_it():
    s = summon()
    logo = centred()
    fx, sfx = s._effects[True], s._scene(W, H).fx
    shock, drip, glint, holo = sfx["finale"], sfx["drip"], fx["glint"], fx["holo"]
    assert shock.start == 7.2 and drip.start > shock.start
    t = 7.5  # the ring is out, no drips yet
    assert s.frame(t, W, H) == shock.apply(fx["assemble"].apply(logo, t), t)
    t = 8.5  # the ring is gone, blood runs
    assert s.frame(t, W, H) == drip.apply(logo, t) != logo
    t = (glint.start + glint.end) / 2
    assert holo.start > t
    assert s.frame(t, W, H) == drip.apply(glint.apply(logo, t), t)
    t = (holo.start + holo.end) / 2
    assert s.frame(t, W, H) == holo.apply(drip.apply(logo, t), t)


def test_crt_off_squashes_the_show_to_black():
    s = summon()
    drip = s._scene(W, H).fx["drip"]
    assert s.frame(11.7, W, H) == drip.apply(centred(), 11.7)
    squashed = s.frame(11.95, W, H)
    n = sum(c.ch == "⣿" for c in squashed.cells)
    assert 0 < n < sum(c.ch == "⣿" for c in centred().cells)
    assert all(c.fg != LOGO_FG for c in squashed.cells if c.ch == "⣿")  # over-bright
    assert any(c.ch == "█" for c in s.frame(12.25, W, H).cells)  # the line
    assert s.frame(12.7, W, H) == Grid(W, H) == s.frame(13.0, W, H)


def test_frames_are_pure_and_seeded():
    a, b = summon(seed=1), summon(seed=2)
    for t in (0.5, 3.0, 4.5, 7.5, 9.0, 10.5, 12.0):
        assert a.frame(t, W, H) == a.frame(t, W, H) == summon(seed=1).frame(t, W, H)
    assert a.frame(4.5, W, H) != b.frame(4.5, W, H)


@pytest.mark.parametrize("loader", [None, lambda style, w, h: None])
def test_no_logo_is_a_short_crt_only_run(loader):
    s = Summon(loader)
    assert s.duration_for(W, H) == pytest.approx(3.5)
    for k in range(0, 140):
        t = k * 0.1
        out = s.frame(t, W, H)
        assert (out.width, out.height) == (W, H) and logo_cells(out) == 0
    assert any(c.ch == "█" for c in s.frame(0.8, W, H).cells)  # powers on...
    assert s.frame(2.0, W, H) == Grid(W, H)
    assert any(c.ch == "█" for c in s.frame(2.9, W, H).cells)  # ...and off
    assert s.frame(3.5, W, H) == Grid(W, H) == s.frame(8.0, W, H)


def test_tiny_and_odd_screens():
    calls = []

    def loader(style, w, h):
        calls.append((w, h))
        return fake_logo(style, w, h)

    s = summon(loader=loader, name="Someone")
    for size in [(0, 0), (1, 1), (10, 5), (11, 6), (13, 7), (300, 90)]:
        for t in (0.3, 1.3, 2.7, 3.0, 5.0, 7.3, 9.0, 10.5, 12.0, 12.4):
            out = s.frame(t, *size)
            assert (out.width, out.height) == size
    assert (6, 3) not in calls and (7, 4) not in calls  # too small: no logo, CRT only
    assert (9, 5) in calls and s.duration_for(13, 7) == pytest.approx(13.0)
    assert s.duration_for(10, 5) == pytest.approx(3.5)
