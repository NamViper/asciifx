"""Summon variants (assembly x finale), the name reveal and the logo features, with a fake logo."""

import math

import pytest

from asciifx.core import Cell, Grid
from asciifx.effects import Lightning, Shockwave
from asciifx.logofeatures import EyeGlow, JawChatter, parse_features
from asciifx.summon import ASSEMBLIES, FINALES, Summon, pick_variants, scale_blocks

W, H = 80, 24
LOGO_FG = (0, 255, 0)
FEATURES = parse_features(
    {"eyes": [{"cx": 0.3, "cy": 0.4, "rx": 0.12, "ry": 0.12}, {"cx": 0.7, "cy": 0.4, "rx": 0.12, "ry": 0.12}],
     "jaw": {"y": 0.75, "x0": 0.25, "x1": 0.75}}
)  # fmt: skip


def fake_logo(style: str, max_w: int, max_h: int) -> Grid:
    """A solid braille block, at most 30 x 12."""
    w, h = min(30, max_w), min(12, max_h)
    return Grid.filled(w, h, Cell("⣿", LOGO_FG))


def summon(**kw) -> Summon:
    return Summon(kw.pop("loader", fake_logo), **kw)


# --- picking variants -----------------------------------------------------------------


def test_every_seed_picks_a_variant_and_all_pairs_occur():
    pairs = {pick_variants(seed) for seed in range(300)}
    assert pairs == {(a, f) for a in ASSEMBLIES for f in FINALES}
    assert pick_variants(42) == pick_variants(42)
    for seed in range(20):
        s = summon(seed=seed, features=FEATURES)
        assert (s.assembly, s.finale) == pick_variants(seed)
        assert s._effects[True]["assemble"].origin == s.assembly


def test_explicit_variants_win_and_are_validated():
    s = summon(seed=1, assembly="vortex", finale="lightning")
    assert (s.assembly, s.finale) == ("vortex", "lightning")
    assert s._effects[True]["assemble"].origin == "vortex"
    with pytest.raises(ValueError, match="assembly"):
        summon(assembly="implode")
    with pytest.raises(ValueError, match="finale"):
        summon(finale="fireworks")


def test_eyes_without_features_falls_back_to_shockwave():
    assert summon(finale="eyes").finale == "shockwave"
    assert summon(finale="eyes", features=FEATURES).finale == "eyes"
    eyes_seed = next(seed for seed in range(100) if pick_variants(seed)[1] == "eyes")
    assert summon(seed=eyes_seed).finale == "shockwave"
    fx = summon(finale="eyes")._scene(W, H).fx
    assert isinstance(fx["finale"], Shockwave) and "eyes" not in fx and "jaw" not in fx


@pytest.mark.parametrize("assembly", ASSEMBLIES)
@pytest.mark.parametrize("finale", FINALES)
def test_every_combination_plays(assembly, finale):
    s = summon(assembly=assembly, finale=finale, features=FEATURES, name="Someone", seed=3)
    assert s.duration_for(W, H) == pytest.approx(13.0)
    for t in (4.5, 7.25, 8.0, 9.2, 10.5, 12.0):
        out = s.frame(t, W, H)
        assert (out.width, out.height) == (W, H)
    assert s.frame(13.0, W, H) == Grid(W, H)


# --- the finales ------------------------------------------------------------------------


def test_shockwave_rings_out_from_the_logo_centre():
    s = summon(finale="shockwave")
    shock = s._scene(W, H).fx["finale"]
    x, y, w, h = s._scene(W, H).box
    assert isinstance(shock, Shockwave) and shock.start == Summon.phase("finale").start
    assert shock.origin(W, H) == pytest.approx((x + w / 2, y + h / 2))
    r, _ = shock.crest(7.5, W, H)
    out = s.frame(7.5, W, H)
    inside = {(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
    ring = [(i % W, i // W) for i, c in enumerate(out.cells) if c.ch != " " and (i % W, i // W) not in inside]
    assert len(ring) > 30
    assert all(abs(math.hypot(cx + 0.5 - 40, 2 * (cy + 0.5 - 12)) - r) < 1.3 for cx, cy in ring)


def test_lightning_strikes_as_the_last_spark_lands():
    s = summon(finale="lightning", features=FEATURES)
    bolt = s._scene(W, H).fx["finale"]
    assert isinstance(bolt, Lightning) and bolt.strike == pytest.approx(Summon.phase("finale").start)
    flash = s.frame(7.22, W, H)
    assert all(c.bg == (255, 255, 255) for c in flash.cells)
    x, y, w, h = s._scene(W, H).box
    assert bolt.target == pytest.approx(((x + 0.5 * w) / W, (y + 0.4 * h) / H))  # between the eyes
    leader = s.frame(7.12, W, H)
    assert any(0x2800 < ord(c.ch) <= 0x28FF and c.fg != LOGO_FG for c in leader.cells[: 2 * W])  # from the top
    no_eyes = summon(finale="lightning")._scene(W, H).fx["finale"]
    assert no_eyes.target == pytest.approx(((x + 0.5 * w) / W, (y + 0.45 * h) / H))


def test_eyes_ignite_and_the_jaw_laughs():
    s = summon(finale="eyes", features=FEATURES)
    fx = s._scene(W, H).fx
    eyes, jaw = fx["eyes"], fx["jaw"]
    assert isinstance(eyes, EyeGlow) and isinstance(jaw, JawChatter) and "finale" not in fx
    start = Summon.phase("finale").start
    assert eyes.start == start and start < jaw.start < jaw.end < Summon.phase("reveal").end
    flare = s.frame(start + 0.25, W, H)
    left = eyes.eyes[0]
    c = flare.get(int(left.cx), int(left.cy))
    assert c.fg[0] > 200 and c.fg != LOGO_FG
    x, y, w, h = s._scene(W, H).box
    open_ = s.frame(jaw.start + 0.1, W, H)
    assert all(open_.get(cx, jaw.jaw.y).ch == " " for cx in range(jaw.jaw.x0, jaw.jaw.x1))  # dropped
    assert s.frame(10.5, W, H).get(int(left.cx), int(left.cy)).fg[0] > 100  # still glowing in the hold


def test_drips_run_from_the_teeth_or_off_the_logo():
    s = summon(finale="shockwave", features=FEATURES)
    drip = s._scene(W, H).fx["drip"]
    x, y, w, h = s._scene(W, H).box
    jaw_y = y + round(0.75 * h)
    assert drip.origins and all(oy == jaw_y and x + 0.25 * w <= ox < x + 0.75 * w for ox, oy in drip.origins)
    plain = summon(finale="shockwave")._scene(W, H).fx["drip"]
    assert plain.origins and all(oy == y + h for _, oy in plain.origins)  # just under the logo
    assert drip.start > Summon.phase("finale").start and drip.end >= Summon.plan()[-1].end
    late = s.frame(11.6, W, H)
    assert any(c.ch in "⣀⣤⣶" for c in late.cells[(H - 1) * W :])  # pooled at the bottom


# --- the name -----------------------------------------------------------------------------


def test_name_in_block_letters_under_the_logo():
    s = summon(name="NamViper")
    scene = s._scene(W, H)
    x, y, w, h = scene.box
    assert h < 12 or y + h < H  # room was made below the logo
    nx, ny = scene.name_at
    assert not scene.name_over and ny == y + h + 1 and scene.name.height == 4
    assert nx == (W - scene.name.width) // 2
    assert all(c.ch in " █▀▄" for c in scene.name.cells)
    rows = slice(ny * W, (ny + scene.name.height) * W)
    assert not any(c.ch in "█▀▄" for c in s.frame(8.5, W, H).cells[rows])  # no letters before the reveal
    assert s.frame(9.65, W, H).cells[rows][nx : nx + 5] != Grid(5, 1).cells  # decoded, the glitch hit done
    revealed = s.frame(9.65, W, H).cells[rows]
    got = [revealed[r * W + nx + k] for r in range(scene.name.height) for k in range(scene.name.width)]
    # every letter cell is in place (blood running over a letter stains it but keeps its shape)
    assert all(g.ch == c.ch for g, c in zip(got, scene.name.cells) if c.ch != " ")


def test_name_falls_back_to_smaller_fonts_then_over_the_logo_then_nothing():
    small = summon(name="NamViper")._scene(45, 24)
    assert small.name.height == 3 and not small.name_over  # the block font is too wide
    plain = summon(name="NamViper")._scene(30, 24)
    assert plain.name.height == 1 and plain.name.to_text() == "NamViper"
    over = summon(name="NamViper")._scene(30, 7)
    assert over.name_over and over.name_at[1] == over.box[1] + over.box[3] - 1
    none = summon(name="A name far too long for this screen")._scene(20, 10)
    assert none is not None and none.name is None
    assert summon(name="  \t ")._scene(W, H).name is None
    assert summon(name="a\nb\x1bc").name == "a b c"


def test_no_name_keeps_the_logo_centred():
    s = summon()
    x, y, w, h = s._scene(W, H).box
    assert (x, y) == ((W - w) // 2, (H - h) // 2) and s._scene(W, H).name is None


# --- odds and ends --------------------------------------------------------------------------


def test_scale_blocks():
    g = Grid.from_text("▀▄\n█ ", fg=(1, 2, 3))
    s2 = scale_blocks(g, 2)  # every half-block pixel becomes 2 x 2 pixels
    assert (s2.width, s2.height) == (4, 4)
    assert s2.to_text().split("\n") == ["██", "  ██", "██", "██"]
    assert {c.fg for c in s2.cells if c.ch != " "} == {(1, 2, 3)}
    assert scale_blocks(Grid.from_text("▀"), 3).to_text().split("\n") == ["███", "▀▀▀", ""]
    assert scale_blocks(g, 1) == g


def test_hologram_flicker_in_the_hold():
    holo = summon()._effects[True]["holo"]
    hold = Summon.phase("hold")
    assert hold.start < holo.start < holo.end < hold.end
    assert type(holo).__name__ == "Hologram"  # effects/hologram.py is there (else: a Glitch stands in)
