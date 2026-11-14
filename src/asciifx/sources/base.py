"""The Source protocol: anything that can render a frame for time t at a given size."""

from __future__ import annotations

from typing import Protocol

from ..core.grid import Grid


class Source(Protocol):
    def frame(self, t: float, width: int, height: int) -> Grid:
        """Render the frame at ``t`` seconds as a ``width`` x ``height`` grid."""
        ...
