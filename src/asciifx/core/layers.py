"""Layers and compositing.

Blend rule for a non-opaque layer, per cell (top over bottom):
  * top has a bg color      -> top fully covers the cell
  * top is a space (no bg)  -> transparent, bottom shows through
  * top is a glyph (no bg)  -> top glyph + fg drawn over bottom's bg
An ``opaque`` layer copies its cells verbatim.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .grid import BLANK, Cell, Grid


@dataclass
class Layer:
    grid: Grid
    x: int = 0
    y: int = 0
    opaque: bool = False
    visible: bool = True


def blend(bottom: Cell, top: Cell) -> Cell:
    if top.bg is not None:
        return top
    if top.ch == " ":
        return bottom
    if bottom.bg is None:
        return top
    return Cell(top.ch, top.fg, bottom.bg)


def composite(dst: Grid, layer: Layer) -> None:
    """Draw ``layer`` onto ``dst`` in place, clipping to ``dst``'s bounds."""
    if not layer.visible:
        return
    src = layer.grid
    x0, x1 = max(0, layer.x), min(dst.width, layer.x + src.width)
    if x0 >= x1:
        return
    out, inp = dst.cells, src.cells
    for y in range(max(0, layer.y), min(dst.height, layer.y + src.height)):
        d_row = y * dst.width
        s_row = (y - layer.y) * src.width - layer.x
        if layer.opaque:
            out[d_row + x0 : d_row + x1] = inp[s_row + x0 : s_row + x1]
            continue
        for x in range(x0, x1):
            out[d_row + x] = blend(out[d_row + x], inp[s_row + x])


def compose(width: int, height: int, layers: Iterable[Layer], background: Cell = BLANK) -> Grid:
    """Composite layers bottom-to-top onto a fresh ``background``-filled grid."""
    grid = Grid.filled(width, height, background)
    for layer in layers:
        composite(grid, layer)
    return grid
