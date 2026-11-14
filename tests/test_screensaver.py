"""Screensaver timeline and frames, with a fake logo loader (no image conversion)."""

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.core.layers import Layer, compose
from asciifx.effects import BLOOD, Decode, Glitch, Matrix, Reverse
from asciifx.screensaver import (
    BURST, DISSOLVE, HOLD, INTRO, MORPH, OUTRO, REHOLD, Screensaver, default_loader,
)

W, H = 80, 24
LOGO_FG = (0, 255, 0)  # not a BLOOD color, so logo cells are easy to tell from rain
GLYPHS = {"dots": "⣿", "letters": "A", "binary": "@"}
SIZES = {"dots": (30, 12), "letters": (26, 12), "binary": (30, 9)}
NAMES = ["intro", "hold", "glitch", "morph", "hold", "glitch", "morph", "hold", "glitch",
         "dissolve", "rain"]


def fake_logo(style: str, max_w: int, max_h: int) -> Grid:
    """A checkered block of the style's glyph, at most SIZES[style]."""
    w, h = (min(a, b) for a, b in zip(SIZES[style], (max_w, max_h)))
    g = Grid(w, h)
    for y in range(h):
        for x in range(w):
            if (x + y) % 3:
                g.set(x, y, Cell(GLYPHS[style], LOGO_FG))
    return g


def centred(style: str, width: int = W, height: int = H) -> Grid:
    logo = fake_logo(style, width - 4, height - 2)
    x, y = (width - logo.width) // 2, (height - logo.height) // 2
    return compose(width, height, [Layer(logo, x, y)])


def saver(**kw) -> Screensaver:
    """The classic screensaver: skull scene only, blood red, no clock or idle overlays
    (the new scenes and overlays are tested in test_screensaver_scenes.py)."""
    kw.setdefault("styles", ("dots", "letters", "binary"))  # the styles fake_logo knows
    for key, value in (("scenes", ("skull",)), ("palette", "blood"), ("show_clock", False),
                       ("show_idle", False), ("clock", lambda: 1_700_000_000.0)):
        kw.setdefault(key, value)
    return Screensaver(kw.pop("loader", fake_logo), **kw)


def test_default_styles_are_the_users_favourites():
    assert Screensaver(None).styles == ("dots", "sketch", "outline")


def phases(ss: Screensaver, cycle: int, name: str) -> list:
    return [p for p in ss.plan(cycle) if p.name == name]


def quiet_time(phase) -> float:
    """A time inside a hold phase that is outside every glitch burst."""
    assert isinstance(phase.effect, Glitch)
    t = phase.start + 0.01
    while phase.effect.burst_at(t) is not None or phase.effect.burst_at(t + 1e-6) is not None:
        t += 0.01
    assert t < phase.end
    return t


def logo_cells(g: Grid) -> int:
    return sum(c.fg == LOGO_FG for c in g.cells)


def lit(g: Grid) -> float:
    return sum(c.ch != " " for c in g.cells) / len(g.cells)


# --- timeline -------------------------------------------------------------------


def test_cycle_timeline():
    ss = saver()
    plan = ss.plan(0)
    assert [p.name for p in plan] == NAMES
    lengths = [INTRO, HOLD, BURST, MORPH, REHOLD, BURST, MORPH, REHOLD, OUTRO, DISSOLVE, 30.0]
    assert [p.end - p.start for p in plan] == pytest.approx(lengths)
    assert plan[0].start == 0.0 and plan[-1].end == pytest.approx(ss.cycle_length)
    assert all(a.end == b.start for a, b in zip(plan, plan[1:]))
    assert ss.cycle_length == pytest.approx(48.3) and ss.logo_length == pytest.approx(18.3)
    assert [p.style for p in plan] == [
        "dots", "dots", "dots", "letters", "letters", "letters", "binary", "binary", "binary",
        "binary", None,
    ]
    assert plan[3].prev == "dots" and plan[6].prev == "letters"


def test_phase_at_boundaries():
    ss = saver()
    for k in (0, 1, 5):
        for p in ss.plan(k):
            assert ss.phase_at(p.start) == p
            assert ss.phase_at((p.start + p.end) / 2) == p
            assert ss.phase_at(p.end - 1e-6) == p
    assert ss.phase_at(ss.cycle_length).cycle == 1
    assert ss.phase_at(ss.cycle_length - 1e-6).name == "rain"
    assert ss.plan(3)[0].start == pytest.approx(3 * ss.cycle_length)


def test_intro_alternates_and_styles_rotate():
    ss = saver()
    for k in range(6):
        intro = ss.plan(k)[0]
        assert intro.name == "intro"
        if k % 2 == 0:
            assert isinstance(intro.effect, Decode) and intro.effect.appear > 0
        else:
            assert isinstance(intro.effect, Matrix) and intro.effect.reveal
        assert intro.style == ss.styles[k % 3]
        assert ss.styles_for(k)[0] == ss.styles[k % 3]
    assert ss.styles_for(1) == ("letters", "binary", "dots")
    assert isinstance(phases(ss, 0, "dissolve")[0].effect, Reverse)


def test_glitches_differ_between_cycles():
    ss = saver()
    windows = [tuple(phases(ss, k, "hold")[0].effect.windows()) for k in range(4)]
    assert len(set(windows)) == 4
    for k in range(3):  # relative to the cycle start
        a = [b - ss.plan(k)[0].start for b, _ in windows[k]]
        b = [b - ss.plan(k + 1)[0].start for b, _ in windows[k + 1]]
        assert a != pytest.approx(b)


def test_single_style_and_no_rain():
    ss = saver(styles=("dots",), rain_seconds=0)
    assert [p.name for p in ss.plan(0)] == ["intro", "hold", "glitch", "dissolve"]
    assert ss.cycle_length == pytest.approx(INTRO + HOLD + OUTRO + DISSOLVE)
    assert ss.frame(ss.cycle_length - DISSOLVE / 2, W, H) != Grid(W, H)  # dissolving
    assert ss.frame(ss.cycle_length, W, H) == Grid(W, H)  # next intro starts from nothing
    assert ss.phase_at(ss.cycle_length).name == "intro"


def test_validation():
    with pytest.raises(ValueError):
        saver(styles=())
    with pytest.raises(ValueError):
        saver(rain_seconds=-1)
    assert saver(styles=["dots", "binary"]).styles == ("dots", "binary")


# --- frames -------------------------------------------------------------------


@pytest.mark.parametrize("k", [0, 1, 2])
def test_hold_shows_exactly_the_centred_logo(k):
    ss = saver()
    holds = phases(ss, k, "hold")
    assert [h.style for h in holds] == list(ss.styles_for(k))
    for hold in holds:
        t = quiet_time(hold)
        assert ss.frame(t, W, H) == centred(hold.style)
        b, e = hold.effect.windows()[0]
        assert ss.frame((b + e) / 2, W, H) != centred(hold.style)  # mid-burst: glitched


def test_even_intro_decodes_out_of_nothing():
    ss = saver()
    intro = ss.plan(0)[0]
    assert ss.frame(0.0, W, H) == Grid(W, H)
    mid = ss.frame(intro.start + 0.6 * INTRO, W, H)
    assert 0 < logo_cells(mid) < logo_cells(centred("dots"))
    assert any(c.fg == BLOOD.highlight for c in mid.cells)  # scrambled glyphs
    assert ss.frame(intro.end - 1e-6, W, H) == centred("dots")


def test_odd_intro_rain_keeps_falling_and_paints_the_logo_in():
    ss = saver()
    c = ss.cycle_length
    intro = ss.plan(1)[0]
    assert lit(ss.frame(c - 0.05, W, H)) > 0.3  # the rain doesn't drain before a matrix intro
    early = ss.frame(c + 0.05, W, H)
    assert lit(early) > 0.3 and logo_cells(early) == 0
    mid = ss.frame(intro.start + 0.6 * INTRO, W, H)
    assert 0 < logo_cells(mid) < logo_cells(centred("letters"))
    hold = ss.phase_at(intro.end)
    assert ss.frame(quiet_time(hold), W, H) == centred("letters")  # all rain gone


def test_rain_drains_before_a_decode_intro():
    ss = saver()
    rain = phases(ss, 1, "rain")[0]  # cycle 2 starts with a decode
    assert lit(ss.frame(rain.end - 1e-6, W, H)) < 0.02
    assert ss.frame(rain.end, W, H) == Grid(W, H)


def test_morph_halfway_mixes_both_styles():
    ss = saver()
    for k in (0, 1):
        for m in phases(ss, k, "morph"):
            assert ss.frame(m.start, W, H) == centred(m.prev)
            out = ss.frame((m.start + m.end) / 2, W, H)
            chars = {c.ch for c in out.cells}
            assert GLYPHS[m.prev] in chars and GLYPHS[m.style] in chars
            assert any(c.fg == BLOOD.highlight for c in out.cells)  # scramble band
            after = ss.phase_at(m.end)
            assert after.name == "hold" and after.style == m.style
            assert ss.frame(quiet_time(after), W, H) == centred(m.style)


def test_glitch_and_dissolve():
    ss = saver()
    burst, outro = phases(ss, 0, "glitch")[0], phases(ss, 0, "glitch")[-1]
    assert ss.frame((burst.start + burst.end) / 2, W, H) != centred("dots")
    assert outro.effect.intensity == 1.0
    d = phases(ss, 0, "dissolve")[0]
    assert ss.frame(d.start, W, H) == centred("binary")
    assert 0 < logo_cells(ss.frame((d.start + d.end) / 2, W, H)) < logo_cells(centred("binary"))
    assert ss.frame(d.end - 1e-6, W, H).cells.count(BLANK) > 0.95 * W * H


@pytest.mark.parametrize("dt", [2.0, 5.5, 13.0, 15.0, 21.7, 27.0])
def test_rain_has_no_logo_and_is_dense(dt):
    ss = saver()
    rain = phases(ss, 0, "rain")[0]
    assert ss.frame(rain.start, W, H) == Grid(W, H)  # starts from black
    out = ss.frame(rain.start + dt, W, H)
    assert logo_cells(out) == 0
    assert lit(out) > 0.35
    assert {c.fg for c in out.cells if c.ch != " "} <= {BLOOD.head, *_trail_colors()}


def _trail_colors() -> set:
    from asciifx.effects.base import gradient

    return {c for n in range(1, 40) for c in gradient(BLOOD.tail, n)}


def test_rain_fills_quickly():
    ss = saver()
    rain = phases(ss, 0, "rain")[0]
    assert lit(ss.frame(rain.start + 1.0, W, H)) > 0.15
    assert lit(ss.frame(rain.start + 1.5, W, H)) > 0.35


@pytest.mark.parametrize("loader", [None, lambda style, w, h: None])
def test_no_logo_means_endless_rain(loader):
    ss = saver(loader=loader)
    assert ss.frame(0.0, W, H) == Grid(W, H)
    for t in (2.0, 7.3, 12.0, 30.0, 61.0, 1000.5):
        out = ss.frame(t, W, H)
        assert logo_cells(out) == 0 and lit(out) > 0.35, t


def test_tiny_screen_is_rain_only_without_loading():
    calls = []
    ss = saver(loader=lambda s, w, h: calls.append((s, w, h)) or fake_logo(s, w, h))
    assert logo_cells(ss.frame(4.0, 10, 5)) == 0
    assert calls == []
    assert ss.frame(0.0, 0, 0) == Grid(0, 0)


def test_missing_styles_borrow_another_logo():
    ss = saver(loader=lambda s, w, h: fake_logo(s, w, h) if s == "letters" else None)
    hold = phases(ss, 0, "hold")[0]
    assert hold.style == "dots"
    assert ss.frame(quiet_time(hold), W, H) == centred("letters")


def test_resize_recentres_and_caches_per_size():
    calls = []
    ss = saver(loader=lambda s, w, h: calls.append((s, w, h)) or fake_logo(s, w, h))
    hold = phases(ss, 0, "hold")[0]
    t = quiet_time(hold)
    for size in [(80, 24), (120, 40), (61, 21), (80, 24)]:
        assert ss.frame(t, *size) == centred("dots", *size)
    assert calls == [(s, w - 4, h - 2) for w, h in [(80, 24), (120, 40), (61, 21)]
                     for s in ("dots", "letters", "binary")]
    # different sizes put the logo in different places
    a, b = ss.frame(t, 80, 24), ss.frame(t, 61, 21)
    first = [next(i % g.width for i, c in enumerate(g.cells) if c.fg == LOGO_FG) for g in (a, b)]
    assert first[0] != first[1]


@pytest.mark.parametrize("size", [(80, 24), (61, 21), (120, 40)])
def test_every_style_is_centred(size):
    ss = saver()
    w, h = size
    for k in range(3):  # each style is first (held) in one of three cycles
        hold = phases(ss, k, "hold")[0]
        out = ss.frame(quiet_time(hold), w, h)
        xs = [i % w for i, c in enumerate(out.cells) if c.fg == LOGO_FG]
        ys = [i // w for i, c in enumerate(out.cells) if c.fg == LOGO_FG]
        left, right = min(xs), w - 1 - max(xs)
        top, bottom = min(ys), h - 1 - max(ys)
        assert right - left in (0, 1) and bottom - top in (0, 1), (hold.style, size)
        assert max(xs) - min(xs) + 1 == min(SIZES[hold.style][0], w - 4)


def test_frames_are_deterministic():
    a, b = saver(), saver()
    times = [0.7, 4.2, 6.6, 7.6, 12.0, 17.5, 25.0, 48.4, 50.0, 103.3]
    for t in times:
        assert a.frame(t, W, H) == b.frame(t, W, H) == a.frame(t, W, H)
    assert saver(seed=1).frame(25.0, W, H) != a.frame(25.0, W, H)
    assert len({tuple(a.frame(t, W, H).cells) for t in times}) == len(times)


def test_cycles_differ():
    ss = saver()
    c = ss.cycle_length
    assert ss.frame(25.0, W, H) != ss.frame(25.0 + 2 * c, W, H)  # same phase, other seed


# --- default_loader -------------------------------------------------------------


def test_default_loader_missing_file(tmp_path):
    load = default_loader(tmp_path / "nope.png")
    assert load("dots", 40, 20) is None
    assert Screensaver(load).logos(W, H) is None


def test_default_loader_corrupt_file_is_rain_only(tmp_path, monkeypatch):
    from asciifx import startup

    monkeypatch.delenv("ASCIIFX_DEBUG", raising=False)
    img = tmp_path / "logo.png"
    img.write_bytes(b"x")

    def broken(path, w, h, params, *, cache=None):
        raise RuntimeError("decoder blew up")  # not an OSError: still no crash

    monkeypatch.setattr(startup, "load_logo", broken)
    assert default_loader(img)("dots", 40, 20) is None
    monkeypatch.setenv("ASCIIFX_DEBUG", "1")
    with pytest.raises(RuntimeError):
        default_loader(img)("dots", 40, 20)


def test_default_loader_passes_style_and_falls_back(tmp_path, monkeypatch):
    from asciifx import startup

    img = tmp_path / "logo.png"
    img.write_bytes(b"not really an image")
    seen = []

    def fake_load(path, w, h, params, *, cache=None):
        seen.append(dict(params))
        if params.get("style") == "unknown":
            raise TypeError("image_to_grid() got an unexpected keyword argument 'style'")
        return Grid.from_text("##\n##")

    monkeypatch.setattr(startup, "load_logo", fake_load)
    load = default_loader(img)
    g = load("dots", 40, 20)
    assert seen == [{**startup.LOGO_PARAMS, "style": "dots"}]
    assert g.get(0, 0) == Cell("#", startup.BLOOD.logo)
    assert g.get(0, 1) == Cell("#", startup.BLOOD.logo_bottom)
    seen.clear()
    assert load("unknown", 40, 20) is not None
    assert seen == [{**startup.LOGO_PARAMS, "style": "unknown"}, startup.LOGO_PARAMS]
