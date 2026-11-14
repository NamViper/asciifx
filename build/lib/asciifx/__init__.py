"""asciifx: terminal ASCII/Unicode art and animation.

The public names are imported on first use (PEP 562), so ``import asciifx`` is
nearly free and importing one submodule (``asciifx.term``, ``asciifx.cli``)
doesn't load the others.
"""

__version__ = "0.1.0"
__all__ = ["Cell", "Grid", "Layer", "Terminal", "compose", "play"]

_LAZY = {  # public name -> submodule defining it
    "play": "api",
    "Cell": "core",
    "Grid": "core",
    "Layer": "core",
    "compose": "core",
    "Terminal": "term",
}

TYPE_CHECKING = False  # not typing.TYPE_CHECKING: importing typing costs ~5 ms
if TYPE_CHECKING:
    from .api import play
    from .core import Cell, Grid, Layer, compose
    from .term import Terminal
del TYPE_CHECKING


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value  # later lookups don't come back here
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
