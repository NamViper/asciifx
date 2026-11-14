"""Escape sequences and frame encoding (pure string building, no I/O).

Frames are encoded as a *diff* against what is already on screen, see
``encode_frame``. Two cursor-addressing schemes share the same diff logic:

* fullscreen: the grid sits at the screen's top-left; rows are addressed with
  CUP (``ESC[row;colH``).
* inline: the grid sits in a region of the normal screen reserved by
  ``reserve_region``, which saves the region's top-left with DECSC. Rows are
  addressed relative to it: DECRC, then CUD ``y`` lines, then CHA to the column.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from ..core.grid import Cell, Color, Grid
from .color import ColorMode, rgb_to_256

ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"
CURSOR_HIDE = "\x1b[?25l"
CURSOR_SHOW = "\x1b[?25h"
SYNC_BEGIN = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"
AUTOWRAP_OFF = "\x1b[?7l"
AUTOWRAP_ON = "\x1b[?7h"
RESET = "\x1b[0m"
ERASE_SCREEN = "\x1b[2J"  # ED 2: doesn't move the cursor
ERASE_LINE = "\x1b[2K"  # EL 2: doesn't move the cursor
ERASE_DOWN = "\x1b[J"  # ED 0: from the cursor to the end of the screen
CLEAR = "\x1b[H" + ERASE_SCREEN
SAVE_CURSOR = "\x1b7"  # DECSC: saves position *and* SGR attributes
RESTORE_CURSOR = "\x1b8"  # DECRC: restores both

ENTER = ALT_SCREEN_ON + CURSOR_HIDE + AUTOWRAP_OFF + RESET + CLEAR
# Starts with ESC, so it also aborts any half-written sequence from an interrupted frame.
LEAVE = SYNC_END + RESET + AUTOWRAP_ON + CURSOR_SHOW + ALT_SCREEN_OFF

ENTER_INLINE = CURSOR_HIDE + AUTOWRAP_OFF

REWRITE_GAP = 4
"""Up to this many unchanged cells between two changes are re-sent instead of moving the cursor."""

_DEFAULT_SGR: tuple[Color | None, Color | None] = (None, None)
_UNKNOWN_SGR = object()

_CONTROLS = {c: " " for c in [*range(0x20), *range(0x7F, 0xA0)]}  # C0, DEL, C1: same set as Grid's


def printable_cells(cells: list[Cell]) -> list[Cell]:
    """``cells`` with control characters and empty glyphs replaced by spaces.

    ``Grid.put_text`` filters controls, but ``Grid.set`` and direct ``Cell``
    construction don't: an ESC in a glyph would otherwise reach the terminal
    as a live escape sequence. Returns ``cells`` itself in the common case
    (every glyph one printable character), which is checked at C speed.
    """
    text = "".join([c.ch for c in cells])  # a list comprehension beats map(attrgetter) here
    if len(text) == len(cells) and text.isprintable():
        return cells
    out = []
    for c in cells:
        ch = c.ch.translate(_CONTROLS) or " "
        out.append(c if ch == c.ch else Cell(ch, c.fg, c.bg))
    return out


def move(x: int, y: int) -> str:
    """CUP: absolute position (0-based x, y)."""
    return f"\x1b[{y + 1};{x + 1}H"


def column(x: int) -> str:
    """CHA: absolute column (0-based) on the current line."""
    return f"\x1b[{x + 1}G"


def cursor_up(n: int) -> str:
    return f"\x1b[{n}A" if n > 0 else ""  # a parameter of 0 would mean 1


def cursor_down(n: int) -> str:
    return f"\x1b[{n}B" if n > 0 else ""


def inline_move(x: int, y: int) -> str:
    """Position relative to the inline region's top-left (saved by ``reserve_region``).

    DECRC also restores the SGR state saved by DECSC, which is the default
    (``reserve_region`` resets before saving).
    """
    return RESTORE_CURSOR + cursor_down(y) + column(x)


def reserve_region(rows: int) -> str:
    """Make room for an inline region of ``rows`` lines starting at the cursor's line.

    Assumes the cursor is at column 0. Writing ``rows`` newlines scrolls the
    screen if needed, so the region *and* the line below it are on screen as
    long as ``rows`` < screen height; the cursor then goes back up to the
    region's first line, which is saved with DECSC (together with a reset SGR
    state, which ``inline_move`` relies on).
    """
    return RESET + "\n" * rows + cursor_up(rows) + SAVE_CURSOR


def print_above(rows: int | None, lines: Iterable[str]) -> str:
    """Print ``lines`` for good where the inline region is, then reserve a new region below them.

    With a region (``rows`` not None), printing starts at its top-left;
    without one, at the cursor (assumed at column 0, where the region will
    go). Everything from there down is erased first, so no leftovers of the
    old frame remain next to a short or wrapped line; the lines scroll the
    screen as needed. Autowrap is on while printing, so long lines wrap like
    normal output. The new region is empty: the caller redraws the frame into
    it. ``lines`` must not contain control characters.
    """
    start = (RESET if rows is None else RESTORE_CURSOR) + ERASE_DOWN
    body = "".join(line + "\r\n" for line in lines)
    tail = "" if rows is None else reserve_region(rows)
    return SYNC_BEGIN + start + AUTOWRAP_ON + body + AUTOWRAP_OFF + tail + SYNC_END


def leave_inline(rows: int | None) -> str:
    """Undo ``ENTER_INLINE``; with a reserved region, end on the line just below it."""
    below = "" if rows is None else RESTORE_CURSOR + cursor_down(rows) + "\r"
    return SYNC_END + below + RESET + AUTOWRAP_ON + CURSOR_SHOW


def _color(c: Color | None, mode: ColorMode, base: int) -> str:
    # base 30 -> fg, 40 -> bg
    if c is None:
        return str(base + 9)
    if mode is ColorMode.TRUECOLOR:
        return f"{base + 8};2;{c[0]};{c[1]};{c[2]}"
    return f"{base + 8};5;{rgb_to_256(*c)}"


def _sgr(fg: Color | None, bg: Color | None, mode: ColorMode) -> str:
    return f"\x1b[{_color(fg, mode, 30)};{_color(bg, mode, 40)}m"


_sgr_cached = lru_cache(maxsize=4096)(_sgr)  # ~3x faster frames with many colors


def sgr(fg: Color | None, bg: Color | None, mode: ColorMode) -> str:
    try:
        return _sgr_cached(fg, bg, mode)
    except TypeError:  # unhashable colors (e.g. lists) still work, just uncached
        return _sgr(fg, bg, mode)


def encode_frame(grid: Grid, mode: ColorMode, prev: Grid | None = None, *, inline: bool = False) -> str:
    """Encode the update that turns the screen from ``prev`` into ``grid``.

    Returns ``""`` when nothing changed; otherwise one string wrapped in
    synchronized output (SYNC_BEGIN ... RESET SYNC_END).

    * ``prev`` is ``None`` or a different size: full redraw. Fullscreen resets
      the colors and erases the whole screen first; inline erases each of the
      grid's rows (and only those) before rewriting it.
    * otherwise only cells that differ from ``prev`` are written.
    * ``inline``: address cells relative to the region saved by
      ``reserve_region`` instead of the screen's top-left.

    Assumes autowrap is off and every cell is exactly one column wide (wide
    characters such as CJK or emoji are not supported and will misalign).
    Control characters in glyphs, and empty glyphs, are sent as spaces
    (``printable_cells``).
    """
    w, h = grid.width, grid.height
    new = grid.cells
    goto = inline_move if inline else move
    out: list[str] = [SYNC_BEGIN]
    cur: object = _UNKNOWN_SGR  # (fg, bg) the terminal currently draws with

    if prev is None or prev.width != w or prev.height != h:
        if not inline:
            out.append(RESET + ERASE_SCREEN)
            cur = _DEFAULT_SGR
        for y in range(h):
            out.append(goto(0, y))
            if inline:
                out.append(RESET + ERASE_LINE)
                cur = _DEFAULT_SGR
            for c in printable_cells(new[y * w : (y + 1) * w]):
                colors = (c.fg, c.bg)
                if colors != cur:
                    out.append(sgr(c.fg, c.bg, mode))
                    cur = colors
                out.append(c.ch)
    else:
        old = prev.cells
        for y in range(h):
            start = y * w
            new_row = new[start : start + w]
            old_row = old[start : start + w]
            if old_row == new_row:  # C-level compare; identity short-circuits per cell
                continue
            new_row = printable_cells(new_row)
            # Column the cursor is at, or -1 = unknown (always unknown at the start
            # of a row). Writing at x leaves it at x + 1; at the last column it
            # stays put (autowrap off), but nothing follows the last column, so
            # x + 1 is never used there.
            cursor = -1
            for x in range(w):
                c = new_row[x]
                o = old_row[x]
                if o is c or o == c:
                    continue
                if cursor < 0:
                    out.append(goto(x, y))
                    if inline:
                        cur = _DEFAULT_SGR  # DECRC restored the SGR state saved with the region
                elif x - cursor > REWRITE_GAP:
                    out.append(column(x))  # same row: CHA is enough
                else:
                    for k in range(cursor, x):  # cheaper to re-send the few cells in between
                        u = new_row[k]
                        colors = (u.fg, u.bg)
                        if colors != cur:
                            out.append(sgr(u.fg, u.bg, mode))
                            cur = colors
                        out.append(u.ch)
                colors = (c.fg, c.bg)
                if colors != cur:
                    out.append(sgr(c.fg, c.bg, mode))
                    cur = colors
                out.append(c.ch)
                cursor = x + 1
        if len(out) == 1:
            return ""

    out.append(RESET)
    out.append(SYNC_END)
    return "".join(out)

