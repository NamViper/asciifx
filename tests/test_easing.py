import pytest

from asciifx.core import EASINGS, clamp, lerp, ping_pong, progress


@pytest.mark.parametrize("name", sorted(EASINGS))
def test_easings_hit_endpoints_and_stay_monotonic(name):
    f = EASINGS[name]
    assert f(0.0) == pytest.approx(0.0)
    assert f(1.0) == pytest.approx(1.0)
    samples = [f(i / 50) for i in range(51)]
    assert samples == sorted(samples)


def test_clamp_lerp_progress():
    assert clamp(-1) == 0 and clamp(2) == 1 and clamp(5, 0, 10) == 5
    assert lerp(10, 20, 0.25) == 12.5
    assert progress(1.5, start=1.0, duration=2.0) == 0.25
    assert progress(0.0, start=1.0, duration=2.0) == 0.0
    assert progress(9.0, start=1.0, duration=2.0) == 1.0
    assert progress(1.0, start=1.0, duration=0.0) == 1.0


@pytest.mark.parametrize(
    "x,expected", [(0, 0), (3, 3), (10, 10), (13, 7), (20, 0), (25, 5), (-3, 3)]
)
def test_ping_pong(x, expected):
    assert ping_pong(x, 10) == pytest.approx(expected)


def test_ping_pong_zero_length():
    assert ping_pong(42, 0) == 0
