"""Big text: original display fonts rendered into Grids.

Four fonts, all designed for asciifx (no FIGlet or other third-party font data):

========  ======  ==========================================================
name      rows    look
========  ======  ==========================================================
block     4       solid letters from █ ▀ ▄ (8 px tall); bold, the hero font
outline   5       single-stroke letters in rounded box-drawing lines
small     3       compact 5-px letters in half blocks
ascii     5       pure ASCII line art, for terminals without Unicode
========  ======  ==========================================================

Every font covers A-Z, 0-9, space and ``. , ! ? - _ : ; / \\ @ # & ' " ( ) + = < > * %``.
Lowercase falls back to uppercase, accented letters to their base letter, and
anything else to a "tofu" box. Glyphs carry no side bearings: the gap between
letters is ``letter_spacing`` (default 1 column), which keeps spacing even.

Stdlib only.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .core.grid import Cell, Color, Grid

__all__ = ["DEFAULT_FONT", "FONTS", "Font", "get_font", "render_text", "text_width"]

ALIGNS = ("left", "center", "right")
DEFAULT_FONT = "block"


@dataclass(frozen=True, eq=False)
class Font:
    """A fixed-height font. ``glyphs`` maps one character to ``height`` equal-width rows."""

    name: str
    height: int
    glyphs: Mapping[str, tuple[str, ...]]
    fallback: tuple[str, ...]  # drawn for characters the font doesn't have

    def glyph(self, ch: str) -> tuple[str, ...]:
        """Rows for ``ch``: exact match, else uppercase, else base letter, else fallback."""
        rows = self.glyphs.get(ch)
        if rows is not None:
            return rows
        if ch.isspace():
            return self.glyphs[" "]
        upper = ch.upper()
        rows = self.glyphs.get(upper)
        if rows is not None:
            return rows
        base, *marks = unicodedata.normalize("NFD", upper) or " "
        if base in self.glyphs and all(unicodedata.combining(m) for m in marks):
            return self.glyphs[base]  # É -> E, Ñ -> N
        return self.fallback

    def char_width(self, ch: str) -> int:
        return len(self.glyph(ch)[0])


# -- glyph sheets ---------------------------------------------------------------
#
# A sheet is a sequence of glyphs, each a "~X" label line followed by exactly
# ``height`` rows. "~tofu" labels the fallback glyph. Rows are right-padded to
# the glyph's width and blank edge columns are trimmed, so trailing spaces in the
# source don't matter.


def _trim(rows: list[str], label: str) -> tuple[str, ...]:
    width = max(len(r) for r in rows)
    rows = [r.ljust(width) for r in rows]
    used = [x for x in range(width) if any(r[x] != " " for r in rows)]
    if not used:
        raise ValueError(f"glyph {label!r} is empty")
    return tuple(r[used[0] : used[-1] + 1] for r in rows)


def _parse(sheet: str, height: int) -> dict[str, tuple[str, ...]]:
    lines = sheet.split("\n")
    glyphs: dict[str, tuple[str, ...]] = {}
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if not line:
            continue
        label = line[1:]
        if not line.startswith("~") or not (len(label) == 1 or label == "tofu"):
            raise ValueError(f"line {i}: expected a '~X' glyph label, got {line!r}")
        rows = [r.rstrip() for r in lines[i : i + height]]
        if len(rows) < height or any(r.startswith("~") for r in rows):
            raise ValueError(f"line {i}: glyph {label!r} needs {height} rows")
        if label in glyphs:
            raise ValueError(f"line {i}: duplicate glyph {label!r}")
        glyphs[label] = _trim(rows, label)
        i += height
    return glyphs


def _font(name: str, height: int, space: int, sheet: str) -> Font:
    glyphs = _parse(sheet, height)
    fallback = glyphs.pop("tofu")
    glyphs[" "] = (" " * space,) * height
    return Font(name, height, MappingProxyType(glyphs), fallback)


# block: 4 rows of half blocks = 8 square pixels. Stems and bars are 2 px thick;
# most letters are 6 columns wide, M N W 7, I 2.
_BLOCK = r"""
~A
▄████▄
██▄▄██
██▀▀██
██  ██
~B
█████▄
██▄▄█▀
██▀▀█▄
█████▀
~C
▄█████
██
██
▀█████
~D
█████▄
██  ██
██  ██
█████▀
~E
██████
██▄▄▄
██▀▀▀
██████
~F
██████
██▄▄▄
██▀▀▀
██
~G
▄█████
██ ▄▄▄
██ ▀██
▀█████
~H
██  ██
██▄▄██
██▀▀██
██  ██
~I
██
██
██
██
~J
    ██
    ██
██  ██
▀████▀
~K
██  ██
██▄█▀
██▀█▄
██  ██
~L
██
██
██
██████
~M
██▄ ▄██
██▀█▀██
██   ██
██   ██
~N
██▄  ██
███▄ ██
██ ▀███
██  ▀██
~O
▄████▄
██  ██
██  ██
▀████▀
~P
█████▄
██▄▄██
██▀▀▀
██
~Q
▄████▄
██  ██
██ ▄██
▀█████▄
~R
█████▄
██▄▄██
██▀█▄
██  ██
~S
▄█████
██▄▄▄
 ▀▀▀██
█████▀
~T
██████
  ██
  ██
  ██
~U
██  ██
██  ██
██  ██
▀████▀
~V
██  ██
██  ██
▀█▄▄█▀
 ▀██▀
~W
██   ██
██   ██
██▄█▄██
██▀ ▀██
~X
██  ██
▀█▄▄█▀
▄█▀▀█▄
██  ██
~Y
██  ██
▀█▄▄█▀
  ██
  ██
~Z
██████
   ▄█▀
 ▄█▀
██████
~0
▄████▄
██ ▄██
██▀ ██
▀████▀
~1
▄███
  ██
  ██
██████
~2
█████▄
 ▄▄▄██
██▀▀▀
██████
~3
█████▄
  ▄▄██
  ▀▀██
█████▀
~4
██  ██
██▄▄██
▀▀▀▀██
    ██
~5
██████
██▄▄▄
▀▀▀▀██
█████▀
~6
▄█████
██▄▄▄
██▀▀█▄
▀████▀
~7
██████
   ▄█▀
  ██
  ██
~8
▄████▄
▀█▄▄█▀
▄█▀▀█▄
▀████▀
~9
▄████▄
██▄▄██
 ▀▀▀██
█████▀
~.



██
~,


▄▄
█▀
~!
██
██
▀▀
██
~?
█████▄
  ▄▄██
  ▀▀
  ██
~-

▄▄▄▄
▀▀▀▀

~_



▄▄▄▄▄▄
~:

██

██
~;

██
▄▄
█▀
~/
   ██
  ██
 ██
██
~\
██
 ██
  ██
   ██
~@
▄▀▀▀▀▀▄
█ ▄▀█ █
█ ▀▄█▄█
▀▄▄▄▄▄
~#
 █  █
▀█▀▀█▀
▄█▄▄█▄
 █  █
~&
 ▄██▄
 ██▄█▀
██ ▀█▄█▀
▀██▄▄██▄
~'
██
▀▀


~"
██ ██
▀▀ ▀▀


~(
 ▄█
██
██
 ▀█
~)
█▄
 ██
 ██
█▀
~+
  ▄▄
▄▄██▄▄
▀▀██▀▀
  ▀▀
~=
▄▄▄▄▄
▀▀▀▀▀
▄▄▄▄▄
▀▀▀▀▀
~<
  ▄█
▄█▀
▀█▄
  ▀█
~>
█▄
▀█▄
▄█▀
█▀
~*

▀▄█▄▀
▄▀█▀▄

~%
██   ▄█
   ▄█▀
 ▄█▀
█▀   ██
~tofu
█▀▀▀█
█   █
█   █
█▄▄▄█
"""

# outline: one rounded line per stroke. ╷ ╵ ╶ ╴ end strokes at the cell centre so
# every letter spans the same height; diagonals only where steps can't work.
_OUTLINE = r"""
~A
╭───╮
│   │
├───┤
│   │
╵   ╵
~B
┌──╮
│  │
├──┴╮
│   │
└───╯
~C
╭───╴
│
│
│
╰───╴
~D
┌───╮
│   │
│   │
│   │
└───╯
~E
┌───╴
│
├──╴
│
└───╴
~F
┌───╴
│
├──╴
│
╵
~G
╭───╴
│
│ ╶─╮
│   │
╰───╯
~H
╷   ╷
│   │
├───┤
│   │
╵   ╵
~I
╶┬╴
 │
 │
 │
╶┴╴
~J
    ╷
    │
    │
╷   │
╰───╯
~K
╷   ╷
│  ╭╯
├──┤
│  ╰╮
╵   ╵
~L
╷
│
│
│
└───╴
~M
╭╮ ╭╮
│╰┬╯│
│ ╵ │
│   │
╵   ╵
~N
╭╮  ╷
│╰╮ │
│ │ │
│ ╰╮│
╵  ╰╯
~O
╭───╮
│   │
│   │
│   │
╰───╯
~P
┌───╮
│   │
├───╯
│
╵
~Q
╭───╮
│   │
│   │
│  ╶┤
╰───┴╴
~R
┌───╮
│   │
├──┬╯
│  ╰╮
╵   ╵
~S
╭───╮
│
╰───╮
    │
╰───╯
~T
╶─┬─╴
  │
  │
  │
  ╵
~U
╷   ╷
│   │
│   │
│   │
╰───╯
~V
╷  ╷
│  │
│  │
╰╮╭╯
 ╰╯
~W
╷   ╷
│   │
│ ╷ │
│╭┴╮│
╰╯ ╰╯
~X
╲   ╱
 ╲ ╱
  ╳
 ╱ ╲
╱   ╲
~Y
╷   ╷
╰╮ ╭╯
 ╰┬╯
  │
  ╵
~Z
╶───╱
   ╱
  ╱
 ╱
╱───╴
~0
╭───╮
│  ╱│
│ ╱ │
│╱  │
╰───╯
~1
╶╮
 │
 │
 │
╶┴╴
~2
╭───╮
    │
╭───╯
│
└───╴
~3
╶───╮
    │
 ╶──┤
    │
╶───╯
~4
╷   ╷
│   │
╰───┤
    │
    ╵
~5
┌───╴
│
└───╮
    │
╶───╯
~6
╭───╴
│
├───╮
│   │
╰───╯
~7
╶───╮
    │
   ╭╯
   │
   ╵
~8
╭───╮
│   │
├───┤
│   │
╰───╯
~9
╭───╮
│   │
╰───┤
    │
╶───╯
~.




•
~,



╷
╯
~!
╷
│
│
╵
•
~?
╭───╮
    │
  ╭─╯
  ╵
  •
~-


╶──╴


~_




╶───╴
~:

•

•

~;

•

╷
╯
~/
    ╱
   ╱
  ╱
 ╱
╱
~\
╲
 ╲
  ╲
   ╲
    ╲
~@
╭────╮
│ ╭─╮│
│ │ ││
│ ╰─┴╯
╰────╴
~#
 ╷  ╷
╶┼──┼╴
 │  │
╶┼──┼╴
 ╵  ╵
~&
╭──╮
╰╮╭╯
╭╯╰╮╷
│  ╰┤
╰───┴╴
~'
╷
╵



~"
╷ ╷
╵ ╵



~(
 ╭
╭╯
│
╰╮
 ╰
~)
╮
╰╮
 │
╭╯
╯
~+

  ╷
╶─┼─╴
  ╵

~=

╶───╴

╶───╴

~<
   ╱
  ╱
  ╲
   ╲

~>
╲
 ╲
 ╱
╱

~*

╲│╱
─┼─
╱│╲

~%
╭╮  ╱
╰╯ ╱
  ╱
 ╱ ╭╮
╱  ╰╯
~tofu
┌╌╌╌┐
╎   ╎
╎   ╎
╎   ╎
└╌╌╌┘
"""

# small: 3 rows of half blocks; letters are 5 px tall (pixel rows 0-4), with
# pixel row 5 left for underscores and comma tails. Mostly 3 columns wide.
_SMALL = r"""
~A
▄▀▄
█▀█
▀ ▀
~B
█▀▄
█▀▄
▀▀
~C
▄▀▀
█
 ▀▀
~D
█▀▄
█ █
▀▀
~E
█▀▀
█▀
▀▀▀
~F
█▀▀
█▀
▀
~G
▄▀▀▀
█ ▀█
 ▀▀▀
~H
█ █
█▀█
▀ ▀
~I
▀█▀
 █
▀▀▀
~J
  █
▄ █
 ▀
~K
█▄▀
█▄
▀ ▀
~L
█
█
▀▀▀
~M
█▄ ▄█
█ ▀ █
▀   ▀
~N
█▄ █
█ ▀█
▀  ▀
~O
▄▀▄
█ █
 ▀
~P
█▀▄
█▀
▀
~Q
▄▀▄
█ █
 ▀▀
~R
█▀▄
█▀▄
▀ ▀
~S
▄▀▀
 ▀▄
▀▀
~T
▀█▀
 █
 ▀
~U
█ █
█ █
▀▀▀
~V
█ █
█ █
 ▀
~W
█   █
█▄▀▄█
▀   ▀
~X
█ █
▄▀▄
▀ ▀
~Y
█ █
 █
 ▀
~Z
▀▀█
▄▀
▀▀▀
~0
█▀█
█ █
▀▀▀
~1
▄█
 █
▀▀▀
~2
▀▀▄
▄▀
▀▀▀
~3
▀▀▄
 ▀▄
▀▀
~4
█ █
▀▀█
  ▀
~5
█▀▀
▀▀▄
▀▀
~6
▄▀▀
█▀▄
 ▀
~7
▀▀█
 █
 ▀
~8
▄▀▄
▄▀▄
 ▀
~9
▄▀▄
 ▀█
▀▀
~.


▀
~,


▄▀
~!
█
▀
▀
~?
▀▀▄
 ▀
 ▀
~-

▀▀▀

~_


▄▄▄
~:
▄
▄

~;
 ▄
 ▄
▀
~/
  █
▄▀
▀
~\
█
 ▀▄
  ▀
~@
▄▀▀▄
█ ▀▀
 ▀▀▀
~#
▄█▄█▄
▄█▄█▄
 ▀ ▀
~&
▄▀▄
▄▀▄▄
 ▀▀
~'
█


~"
█ █


~(
▄▀
█
 ▀
~)
▀▄
 █
▀
~+
 ▄
▀█▀

~=
▄▄▄
▄▄▄

~<
 ▄▀
▀▄
  ▀
~>
▀▄
 ▄▀
▀
~*
▄ ▄
▄▀▄

~%
▀ █
▄▀
▀ ▀
~tofu
███
███
▀▀▀
"""

# ascii: row 0 holds only "_" strokes (they sit on the cell's bottom edge, i.e.
# the top of the letter); stems run rows 1-4.
_ASCII = r"""
~A
 ___
/   \
|___|
|   |
|   |
~B
 ___
|   \
|___/
|   \
|___/
~C
 ____
/
|
|
\____
~D
 ___
|   \
|   |
|   |
|___/
~E
 ____
|
|___
|
|____
~F
 ____
|
|___
|
|
~G
 ____
/
| __
|   |
\___|
~H

|   |
|___|
|   |
|   |
~I
___
 |
 |
 |
_|_
~J

    |
    |
    |
\___/
~K

|   /
|__/
|  \
|   \
~L

|
|
|
|____
~M

|\  /|
| \/ |
|    |
|    |
~N

|\   |
| \  |
|  \ |
|   \|
~O
 ___
/   \
|   |
|   |
\___/
~P
 ___
|   \
|___/
|
|
~Q
 ___
/   \
|   |
|  \|
\___\
~R
 ___
|   \
|___/
|  \
|   \
~S
 ____
/
\___
    \
____/
~T
_____
  |
  |
  |
  |
~U

|   |
|   |
|   |
\___/
~V

|    |
|    |
 \  /
  \/
~W

|    |
|    |
| /\ |
|/  \|
~X

\   /
 \ /
 / \
/   \
~Y

\   /
 \ /
  |
  |
~Z
_____
   /
  /
 /
/____
~0
 ___
/  /\
| / |
|/  |
\___/
~1

 /|
  |
  |
 _|_
~2
 ___
/   \
  __/
 /
/____
~3
____
    \
 ___/
    \
____/
~4

|   |
|___|
    |
    |
~5
 ____
|
|___
    \
____/
~6
 ___
/
|___
|   \
\___/
~7
_____
    /
   /
  /
 /
~8
 ___
/   \
\___/
/   \
\___/
~9
 ___
/   \
\___|
    |
 ___/
~.




o
~,



 o
/
~!

|
|
|
o
~?
 ___
/   \
  __/
  |
  o
~-


____


~_




_____
~:

o

o

~;

 o

 o
/
~/

   /
  /
 /
/
~\

\
 \
  \
   \
~@
 ____
/ __ \
|/  ||
|\__/|
\____
~#

_|_|_
 | |
_|_|_
 | |
~&
 __
/  \
\ _/
/ \/\
\__/\
~'

|



~"

| |



~(

 /
|
|
 \
~)

\
 |
 |
/
~+

  |
--+--
  |

~=

____
____


~<

  /
 /
 \
  \
~>

\
 \
 /
/
~*

\|/
-+-
/|\

~%

o  /
  /
 /
/  o
~tofu
 ___
|   |
| ? |
|   |
|___|
"""

FONTS: dict[str, Font] = {
    "block": _font("block", 4, 3, _BLOCK),
    "outline": _font("outline", 5, 3, _OUTLINE),
    "small": _font("small", 3, 2, _SMALL),
    "ascii": _font("ascii", 5, 3, _ASCII),
}
"""Registry: font name -> Font."""


def get_font(font: str | Font) -> Font:
    """``font`` itself if it is a Font, else the registered font of that name."""
    if isinstance(font, Font):
        return font
    try:
        return FONTS[font.lower()]
    except KeyError:
        raise ValueError(f"unknown font {font!r}; choose from: {', '.join(FONTS)}") from None


# -- layout ---------------------------------------------------------------------


def _clean(line: str) -> str:
    """Compose accents onto their letters and drop any marks left over (zero width)."""
    line = unicodedata.normalize("NFC", line.rstrip("\r"))
    return "".join(ch for ch in line if not unicodedata.combining(ch))


def _line_width(line: str, font: Font, letter_spacing: int) -> int:
    if not line:
        return 0
    return sum(font.char_width(ch) for ch in line) + letter_spacing * (len(line) - 1)


def text_width(text: str, font: str | Font = DEFAULT_FONT, *, letter_spacing: int = 1) -> int:
    """Columns ``render_text`` needs for ``text`` (its widest line), without wrapping."""
    f = get_font(font)
    return max(_line_width(_clean(line), f, letter_spacing) for line in text.split("\n"))


def _wrap(line: str, font: Font, letter_spacing: int, max_width: int) -> list[str]:
    """Greedy word wrap; words wider than ``max_width`` are broken between characters."""

    def fits(s: str) -> bool:
        return _line_width(s, font, letter_spacing) <= max_width

    if fits(line):
        return [line]
    out: list[str] = []
    current = ""
    for word in line.split(" "):
        if not word:
            continue  # runs of spaces collapse when wrapping
        joined = f"{current} {word}" if current else word
        if fits(joined):
            current = joined
            continue
        if current:
            out.append(current)
        while not fits(word):
            n = 1  # always take at least one character, even if it is too wide alone
            while n < len(word) and fits(word[: n + 1]):
                n += 1
            out.append(word[:n])
            word = word[n:]
        current = word
    if current or not out:
        out.append(current)
    return out


def _row_colors(height: int, fg: Color | None, gradient: tuple[Color, Color] | None) -> list[Color | None]:
    if gradient is None:
        return [fg] * height
    top, bottom = gradient
    colors: list[Color | None] = []
    for row in range(height):
        u = row / max(1, height - 1)
        colors.append(tuple(round(a + (b - a) * u) for a, b in zip(top, bottom)))  # type: ignore[arg-type]
    return colors


def render_text(
    text: str,
    font: str | Font = DEFAULT_FONT,
    *,
    fg: Color | None = None,
    gradient: tuple[Color, Color] | None = None,
    letter_spacing: int = 1,
    line_spacing: int = 1,
    align: str = "left",
    max_width: int | None = None,
) -> Grid:
    """Render ``text`` in a big font. ``\\n`` starts a new line.

    * ``fg`` colours every glyph cell; ``gradient=(top, bottom)`` instead fades
      each text line from ``top`` to ``bottom`` (not both).
    * ``letter_spacing`` blank columns between glyphs, ``line_spacing`` blank
      rows between lines.
    * ``max_width`` word-wraps lines to fit (breaking words that are too long on
      their own); the grid is never wider than ``max_width``.
    * ``align`` (left, center, right) positions each line within the widest one.

    The grid is exactly as wide as the widest line. Cells outside glyph strokes
    are ``BLANK``, so the result composites transparently over other layers.
    """
    f = get_font(font)
    if fg is not None and gradient is not None:
        raise ValueError("pass fg or gradient, not both")
    if align not in ALIGNS:
        raise ValueError(f"align must be one of {', '.join(ALIGNS)}, got {align!r}")
    if letter_spacing < 0 or line_spacing < 0:
        raise ValueError("letter_spacing and line_spacing must be >= 0")
    if max_width is not None and max_width < 1:
        raise ValueError(f"max_width must be >= 1, got {max_width}")

    lines: list[str] = []
    for line in text.split("\n"):
        line = _clean(line)
        lines.extend([line] if max_width is None else _wrap(line, f, letter_spacing, max_width))
    widths = [_line_width(line, f, letter_spacing) for line in lines]
    width = max(widths) if max_width is None else min(max(widths), max_width)
    pitch = f.height + line_spacing
    grid = Grid(width, len(lines) * pitch - line_spacing)

    colors = _row_colors(f.height, fg, gradient)
    for i, (line, w) in enumerate(zip(lines, widths)):
        x = {"left": 0, "center": (width - w) // 2, "right": width - w}[align]
        top = i * pitch
        for ch in line:
            rows = f.glyph(ch)
            for dy, row in enumerate(rows):
                color = colors[dy]
                for dx, glyph_ch in enumerate(row):
                    if glyph_ch != " ":
                        grid.set(x + dx, top + dy, Cell(glyph_ch, color))
            x += len(rows[0]) + letter_spacing
    return grid
