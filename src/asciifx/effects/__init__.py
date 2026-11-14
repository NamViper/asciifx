"""Effects: pure ``apply(grid, t) -> Grid`` transforms, no I/O. Stdlib only.

Every effect is a frozen dataclass whose defaults work on any grid
(``EFFECTS[name]()``), has ``start``, a duration-like parameter and ``seed``,
and is the identity once ``t >= end_for(grid)``.

The effect modules are imported on first use (PEP 562), so the shell startup
banner only pays for the few effects it shows.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping

TYPE_CHECKING = False  # not typing.TYPE_CHECKING, to match asciifx/__init__.py
if TYPE_CHECKING:
    from typing import Any

    from .base import Chain, Effect, chain, hash32, is_blank, noise
    from .crt import CRT
    from .decode import BRAILLE_CHARSET, Decode
    from .drip import Drip
    from .fire import BLOOD_FIRE_COLORS, FIRE_COLORS, FIRE_RAMP_ASCII, Fire
    from .glitch import Glitch
    from .hologram import HOLO_GHOST, Hologram
    from .lightning import Lightning
    from .matrix import Matrix
    from .palettes import BLOOD, GREEN, PALETTES, Palette, fade
    from .particles import Assemble, Glint
    from .shockwave import Shockwave
    from .starfield import Starfield
    from .transitions import MORPH_CHARSET, Morph, Reverse
    from .typewriter import Typewriter
del TYPE_CHECKING

_LAZY = {  # public name -> submodule defining it
    **dict.fromkeys(("Chain", "Effect", "chain", "hash32", "is_blank", "noise"), "base"),
    "CRT": "crt",
    **dict.fromkeys(("BRAILLE_CHARSET", "Decode"), "decode"),
    **dict.fromkeys(("BLOOD_FIRE_COLORS", "FIRE_COLORS", "FIRE_RAMP_ASCII", "Fire"), "fire"),
    "Glitch": "glitch",
    **dict.fromkeys(("HOLO_GHOST", "Hologram"), "hologram"),
    "Lightning": "lightning",
    "Shockwave": "shockwave",
    "Drip": "drip",
    "Matrix": "matrix",
    **dict.fromkeys(("BLOOD", "GREEN", "PALETTES", "Palette", "fade"), "palettes"),
    **dict.fromkeys(("Assemble", "Glint"), "particles"),
    "Starfield": "starfield",
    **dict.fromkeys(("MORPH_CHARSET", "Morph", "Reverse"), "transitions"),
    "Typewriter": "typewriter",
}

# Morph and Reverse (transitions) are not registered: Morph needs a target grid
# and Reverse wraps another effect, so neither has a usable no-argument default.
# CRT is registered with its default mode="on"; CRT(mode="off") ends blank, so it
# is a transition like Reverse rather than an effect that ends as the identity.
_REGISTRY = {  # registry name -> class name (in the order ``list(EFFECTS)`` shows)
    "typewriter": "Typewriter",
    "decode": "Decode",
    "matrix": "Matrix",
    "glitch": "Glitch",
    "crt": "CRT",
    "assemble": "Assemble",
    "glint": "Glint",
    "fire": "Fire",
    "starfield": "Starfield",
    "hologram": "Hologram",
    "lightning": "Lightning",
    "shockwave": "Shockwave",
    "drip": "Drip",
}


class _Registry(Mapping[str, type]):
    """Read-only ``name -> effect class``; a class's module is imported on lookup."""

    def __getitem__(self, name: str) -> type:
        cls: type = __getattr__(_REGISTRY[name])
        return cls

    def __contains__(self, name: object) -> bool:  # without importing (Mapping's would)
        return name in _REGISTRY

    def __iter__(self) -> Iterator[str]:
        return iter(_REGISTRY)

    def __len__(self) -> int:
        return len(_REGISTRY)

    def __repr__(self) -> str:
        return f"EFFECTS({', '.join(_REGISTRY)})"


EFFECTS: Mapping[str, type] = _Registry()


def make(name: str, palette: Palette | str | None = None, **params: Any) -> Effect:
    """``EFFECTS[name]`` built with ``palette``'s colors (a Palette or a PALETTES
    key); explicit ``params`` win over the palette."""
    if isinstance(palette, str):
        palette = __getattr__("PALETTES")[palette]
    colors = palette.kwargs(name) if palette is not None else {}
    effect: Effect = EFFECTS[name](**{**colors, **params})
    return effect


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:  # AttributeError also lets `from asciifx.effects import fire` import the submodule
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # __import__ rather than importlib.import_module: -X importtime only reports the former.
    __import__(f"{__name__}.{module}")
    value = getattr(sys.modules[f"{__name__}.{module}"], name)
    globals()[name] = value  # later lookups don't come back here
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})


__all__ = [
    "BLOOD", "BRAILLE_CHARSET", "CRT", "EFFECTS", "GREEN", "MORPH_CHARSET", "PALETTES",
    "Assemble", "Chain", "Decode", "Effect", "Glint", "Glitch", "Matrix", "Morph", "Palette",
    "Reverse", "Typewriter", "chain", "fade", "hash32", "is_blank", "make", "noise",
    "BLOOD_FIRE_COLORS", "FIRE_COLORS", "FIRE_RAMP_ASCII", "Fire", "Starfield",
    "HOLO_GHOST", "Hologram", "Drip", "Lightning", "Shockwave",
]
