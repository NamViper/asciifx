"""Logo features: the sidecar JSON, placing it on screen, and the eye glow / jaw effects."""

import json
import math
from pathlib import Path

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.logofeatures import (
    JAW_STEPS, Ellipse, EyeGlow, JawChatter, JawLine, LogoFeatures, PlacedEye,
    PlacedJaw, heartbeat, load_features, parse_features, sidecar_path,
)

REPO = Path(__file__).resolve().parents[1]
GOOD = {"version": 1, "eyes": [{"cx": 0.3, "cy": 0.5, "rx": 0.1, "ry": 0.05}], "jaw": {"y": 0.8, "x0": 0.2, "x1": 0.7}}


def test_sidecar_path():
    assert sidecar_path("/a/b/skull_logo.png") == Path("/a/b/skull_logo.asciifx.json")
    assert sidecar_path("logo.v2.jpeg").name == "logo.v2.asciifx.json"


def test_load_features(tmp_path):
    logo = tmp_path / "logo.png"
    assert load_features(logo) is None  # no sidecar: no eyes/jaw beats
    (tmp_path / "logo.asciifx.json").write_text(json.dumps(GOOD))
    f = load_features(logo)
    assert f == LogoFeatures((Ellipse(0.3, 0.5, 0.1, 0.05),), JawLine(0.8, 0.2, 0.7))
    (tmp_path / "logo.asciifx.json").write_text("{not json")
    with pytest.raises(ValueError, match="logo.asciifx.json"):
        load_features(logo)


@pytest.mark.parametrize(
    "bad",
    [
        [], {"version": 2, **{k: v for k, v in GOOD.items() if k != "version"}}, {"version": 1},
        {"eyes": "x"}, {"eyes": [{"cx": 0.3, "cy": 0.5, "rx": 0.1}]},
        {"eyes": [{"cx": 1.3, "cy": 0.5, "rx": 0.1, "ry": 0.1}]},
        {"eyes": [{"cx": 0.3, "cy": 0.5, "rx": 0.0, "ry": 0.1}]},
        {"eyes": [{"cx": True, "cy": 0.5, "rx": 0.1, "ry": 0.1}]},
        {"eyes": [{"cx": 0.3, "cy": 0.5, "rx": 0.1, "ry": 0.1}] * 5},
        {"jaw": {"y": 0.8, "x0": 0.7, "x1": 0.2}}, {"jaw": [0.8, 0.2, 0.7]},
    ],
)  # fmt: skip
def test_invalid_features(bad):
    with pytest.raises(ValueError):
        parse_features(bad)


def test_the_skull_logo_sidecar():
    f = load_features(REPO / "skull_logo.png")
    assert f is not None and len(f.eyes) == 2 and f.jaw is not None
    left, right = sorted(f.eyes, key=lambda e: e.cx)
    assert left.cx < 0.5 < right.cx and abs(left.cy - right.cy) < 0.05  # two eyes, side by side
    assert 0.45 < left.cy < 0.65 and f.jaw.y > max(left.cy, right.cy) + 0.15  # mouth below the eyes
    assert f.jaw.x0 < left.cx and right.cx < f.jaw.x1


def test_place():
    f = parse_features(GOOD)
    eyes, jaw = f.place(10, 5, 40, 20)
    assert eyes == (PlacedEye(10 + 12.0, 5 + 10.0, 4.0, 1.0),)
    assert jaw == PlacedJaw(5 + 16, 10 + 8, 10 + 28)
    tiny, _ = f.place(0, 0, 2, 2)
    assert tiny[0].rx == 0.6 and tiny[0].ry == 0.6  # never thinner than a cell


# --- effects ---------------------------------------------------------------------------

EYE = PlacedEye(10.0, 5.0, 3.0, 1.5)
FG = (90, 90, 90)


def face() -> Grid:
    g = Grid(20, 10)
    for y in range(10):
        for x in range(20):
            if (x + y) % 2:
                g.set(x, y, Cell("⣿", FG))
    return g


def test_heartbeat():
    lub, dub, rest = heartbeat(0.07), heartbeat(0.31), heartbeat(0.6)
    assert lub == pytest.approx(1.0, abs=0.01) and 0.5 < dub < lub and rest < 0.05
    assert heartbeat(0.07 + 60 / 72) == pytest.approx(lub)  # 72 bpm
    assert all(0.0 <= heartbeat(k / 97) <= 1.0 for k in range(200))


def test_eye_glow_ignites_then_beats():
    e = EyeGlow(start=1.0, eyes=(EYE,), ignite=0.6)
    assert e.level(0.99) == 0.0 and e.end == math.inf
    assert e.level(1.03) == 0.8 and e.level(1.08) == 0.12  # the stutter
    assert e.level(1.0 + 0.32 * 0.6) == pytest.approx(1.6)  # the flare
    beats = [e.level(1.6 + k / 50) for k in range(50)]
    assert min(beats) >= e.low - 1e-9 and max(beats) == pytest.approx(1.0, abs=0.02)
    assert EyeGlow(start=1.0, duration=2.0, eyes=(EYE,)).level(3.0) == 0.0


def test_eye_glow_colors_the_eye_and_fills_it():
    g = face()
    assert EyeGlow(start=1.0, eyes=(EYE,)).apply(g, 0.5) == g
    flare = EyeGlow(start=1.0, eyes=(EYE,)).apply(g, 1.0 + 0.32 * 0.6)
    centre = flare.get(9, 4)  # cell (9, 4) spans [9, 10) x [4, 5): its centre is 0.5, 0.5 off the eye's
    assert centre.ch == "⣿" and centre.fg[0] > 240 and centre.fg[1] > 60  # white-hot core
    assert flare.get(10, 5).ch == "⣿"  # blank cells inside the eye are filled
    ring = flare.get(12, 5)  # inside, near the rim: red
    assert ring.fg[0] > 200 and ring.fg[1] < 120
    halo = flare.get(14, 5)  # outside, in the halo: tinted toward red
    assert halo.fg != FG and halo.fg[0] > halo.fg[1]
    far = flare.get(19, 0)
    assert far == g.get(19, 0)
    beat = EyeGlow(start=1.0, eyes=(EYE,)).apply(g, 5.0)
    assert beat.get(10, 5).fg[0] < centre.fg[0] or beat.get(10, 5).fg[1] < centre.fg[1]
    assert EyeGlow(start=1.0, eyes=(EYE,)).apply(g, 5.0) == beat  # pure


def test_jaw_drops_and_chatters():
    jaw = PlacedJaw(6, 4, 14)
    e = JawChatter(start=2.0, jaw=jaw)
    steps = [e.drop(2.0 + at + 0.01) for at, _ in JAW_STEPS]
    assert steps == [n for _, n in JAW_STEPS] and max(steps) == 2
    opens = sum(1 for a, b in zip(steps, steps[1:]) if b > a and a == 0)
    assert opens == 2  # chatters twice after the drop
    assert e.drop(1.9) == 0 and e.drop(e.end) == 0

    g = face()
    out = e.apply(g, 2.2)  # 2 rows down
    for y in range(10):
        for x in range(20):
            if 4 <= x < 14 and y >= 6:
                expect = g.get(x, y - 2) if y - 2 >= 6 else BLANK
                assert out.get(x, y) == expect
            else:
                assert out.get(x, y) == g.get(x, y)
    assert e.apply(g, 1.0) == g == e.apply(g, e.end)


@pytest.mark.parametrize("effect", [EyeGlow(start=0.0, eyes=(EYE,)), JawChatter(start=0.0, jaw=PlacedJaw(6, 4, 14))])
def test_eye_and_jaw_survive_non_finite_t(effect):
    g = face()
    assert effect.apply(g, math.nan) == effect.apply(g, 0.0)  # NaN reads as 0, like the effects
    assert effect.apply(g, math.inf) == g == effect.apply(g, -math.inf)
