import pytest

from asciifx.core import Tick, run


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


def test_ticks_are_time_based_and_final_tick_lands_on_duration():
    clock = FakeClock()
    ticks: list[Tick] = []
    steps = run(ticks.append, fps=10, duration=0.35, clock=clock, wait=clock.wait)
    assert [t.t for t in ticks] == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.35])
    assert [t.index for t in ticks] == [0, 1, 2, 3, 4]
    assert ticks[0].dt == 0.0
    assert ticks[1].dt == pytest.approx(0.1)
    assert steps == 5
    assert clock.now == pytest.approx(0.35)


def test_slow_steps_drop_frames_instead_of_bursting():
    clock = FakeClock()
    ticks: list[Tick] = []

    def step(tick: Tick) -> None:
        ticks.append(tick)
        if tick.index == 0:
            clock.now += 0.25  # this frame took 2.5 periods

    run(step, fps=10, duration=0.4, clock=clock, wait=clock.wait)
    assert [t.index for t in ticks] == [0, 2, 3, 4]
    assert [t.t for t in ticks] == pytest.approx([0.0, 0.2, 0.3, 0.4])
    assert ticks[1].dt == pytest.approx(0.2)


def test_step_returning_false_stops():
    clock = FakeClock()
    steps = run(lambda tick: tick.index < 3, fps=30, clock=clock, wait=clock.wait)
    assert steps == 4


def test_early_wakeups_wait_again_for_the_remainder():
    clock = FakeClock()

    def short_wait(seconds: float) -> None:
        clock.wait(min(seconds, 0.03))  # e.g. woken early by keyboard input

    ticks: list[Tick] = []
    run(ticks.append, fps=10, duration=0.2, clock=clock, wait=short_wait)
    assert [t.t for t in ticks] == pytest.approx([0.0, 0.1, 0.2])
    assert len(clock.waits) > 2


def test_zero_duration_renders_exactly_one_frame():
    clock = FakeClock()
    ticks: list[Tick] = []
    run(ticks.append, duration=0, clock=clock, wait=clock.wait)
    assert [t.t for t in ticks] == [0.0]


@pytest.mark.parametrize("kwargs", [{"fps": 0}, {"fps": -1}, {"duration": -1}])
def test_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        run(lambda tick: False, **kwargs)
