"""Color themes for the effects.

    Matrix(duration=0.8, **BLOOD.kwargs("matrix"))
    make("glitch", BLOOD, start=1.4)            # from asciifx.effects
    Palette.mono((255, 140, 0))                  # a whole theme from one color
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.grid import Color
from .base import lerp_color
from .decode import DECODE_HIGHLIGHT
from .glitch import GLITCH_COLORS
from .matrix import MATRIX_HEAD, MATRIX_TAIL

_WHITE: Color = (255, 255, 255)
_BLACK: Color = (0, 0, 0)


def fade(color: Color, steps: int = 4, darkest: float = 0.2) -> tuple[Color, ...]:
    """``steps`` colors from ``color`` down to ``darkest`` x its brightness (a rain tail)."""
    if steps <= 1:
        return (color,) * max(0, steps)
    return tuple(
        lerp_color(color, _BLACK, (1.0 - darkest) * i / (steps - 1)) for i in range(steps)
    )


@dataclass(frozen=True)
class Palette:
    head: Color  # Matrix: drop head
    tail: tuple[Color, ...]  # Matrix: trail behind the head, nearest first
    highlight: Color  # Decode: scrambled glyphs
    glitch: tuple[Color, ...]  # Glitch: split / swap colors
    cursor: Color | None = None  # Typewriter: cursor (None: like the typed text)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tail", tuple(tuple(c) for c in self.tail))
        object.__setattr__(self, "glitch", tuple(tuple(c) for c in self.glitch))

    @classmethod
    def mono(cls, base: Color) -> Palette:
        """A full theme derived from one color."""
        return cls(
            head=lerp_color(base, _WHITE, 0.8),
            tail=fade(base),
            highlight=lerp_color(base, _WHITE, 0.45),
            glitch=(base, lerp_color(base, _BLACK, 0.5), _WHITE),
            cursor=base,
        )

    def kwargs(self, effect: str | type) -> dict[str, Any]:
        """Constructor keyword arguments that apply this palette to ``effect``
        (a registry name like "matrix", or the class)."""
        name = effect if isinstance(effect, str) else effect.__name__.lower()
        match name:
            case "matrix":
                return {"head": self.head, "tail": self.tail}
            case "decode":
                return {"highlight": self.highlight}
            case "glitch":
                return {"colors": self.glitch}
            case "typewriter":
                return {"cursor_fg": self.cursor}
            case "fire":  # black -> dark -> base -> near-white, from the rain colours
                return {"colors": ((0, 0, 0), self.tail[min(2, len(self.tail) - 1)], self.tail[0], self.head)}
            case "starfield":  # far (dim) -> near (bright)
                return {"colors": (self.tail[min(2, len(self.tail) - 1)], self.tail[0], self.head)}
        return {}


GREEN = Palette(
    head=MATRIX_HEAD,
    tail=MATRIX_TAIL,
    highlight=DECODE_HIGHLIGHT,
    glitch=GLITCH_COLORS,
    cursor=(120, 255, 140),
)
"""The effects' default colors."""

BLOOD = Palette(
    head=(255, 220, 220),
    tail=((230, 30, 30), (170, 15, 15), (110, 8, 8), (60, 4, 4)),
    highlight=(255, 90, 90),
    glitch=((255, 0, 60), (120, 0, 0), (255, 255, 255)),
    cursor=(255, 60, 60),
)

PALETTES: dict[str, Palette] = {"green": GREEN, "blood": BLOOD}
