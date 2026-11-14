"""Fixed-timestep animation loop.

Ticks are scheduled at ``t = index / fps`` seconds after start. If a step runs
late, the missed ticks are dropped (never replayed in a burst) and the next
step gets the most recent due time, so animation speed is tied to wall-clock
time, not to how many frames were actually drawn.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_FPS = 30.0


@dataclass(frozen=True, slots=True)
class Tick:
    t: float  # seconds since start (scheduled time of this tick)
    dt: float  # seconds since the previous tick that was run (0 for the first)
    index: int  # tick number; jumps forward when ticks are dropped


def run(
    step: Callable[[Tick], bool | None],
    *,
    fps: float = DEFAULT_FPS,
    duration: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], object] = time.sleep,
) -> int:
    """Call ``step(tick)`` at a fixed rate until it returns ``False``.

    ``wait(seconds)`` idles until the next tick; it may return early (e.g. on
    keyboard input) and is then called again for the remaining time.
    With ``duration``, a final tick at exactly ``t == duration`` is guaranteed.
    Returns the number of steps run.
    """
    if not 0 < fps < float("inf"):  # also rejects NaN
        raise ValueError(f"fps must be positive and finite, got {fps}")
    if duration is not None and not duration >= 0:  # NaN would never end
        raise ValueError(f"duration must be non-negative, got {duration}")
    period = 1.0 / fps
    start = clock()
    index, prev_t, steps = 0, 0.0, 0
    while True:
        t = index * period
        if duration is not None:
            t = min(t, duration)
        steps += 1
        if step(Tick(t, t - prev_t if index else 0.0, index)) is False:
            break
        if duration is not None and t >= duration:
            break
        prev_t = t
        next_index = max(index + 1, int((clock() - start) / period))
        deadline = next_index * period
        if duration is not None:
            deadline = min(deadline, duration)
        while (remaining := deadline - (clock() - start)) > 0:
            wait(remaining)
        index = next_index
    return steps
