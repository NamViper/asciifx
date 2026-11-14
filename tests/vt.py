"""A tiny VT emulator for tests. It understands exactly what asciifx emits and
fails loudly on anything else, so a stray or malformed sequence breaks the test.

Supported: printable chars, CR, LF (scrolls at the bottom row), CUP ``H``, CUU
``A``, CUD ``B``, CHA ``G``, ED ``J``/``0J``/``2J``, EL ``2K``, DECSC/DECRC
(``ESC 7``/``ESC 8``, saving position *and* colors like real terminals), SGR
0/39/49/38;2/48;2/38;5/48;5 and DEC private modes ``?...h/l`` (recorded, only
``?7`` autowrap has an effect). Erasing and scrolling fill with the current
background color (BCE), like xterm.

Autowrap works like xterm's: printing in the last column leaves the cursor
there; with autowrap off the next char overwrites that column, with autowrap
on it first wraps to the start of the next line (a CR, LF or cursor move in
between cancels the pending wrap).

Truecolor is stored as an (r, g, b) tuple, 256-color as the palette index.
"""

from __future__ import annotations

import re
from typing import Any

from asciifx.core import Cell, Grid

_TOKEN = re.compile(
    r"\x1b\[(?P<priv>\??)(?P<params>[0-9;]*)(?P<final>[@-~])"
    r"|\x1b(?P<esc>[^\[])"
    r"|(?P<ctl>[\x00-\x1f\x7f-\x9f])"
    r"|(?P<text>[^\x00-\x1f\x7f-\x9f]+)",
    re.DOTALL,
)


class VTError(AssertionError):
    pass


class VT:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.lines = [self._blank_line(None) for _ in range(height)]
        self.x = self.y = 0
        self.fg: Any = None
        self.bg: Any = None
        self.saved: tuple[int, int, Any, Any] | None = None
        self.autowrap = True  # terminals start with autowrap on
        self.modes: dict[int, bool] = {}
        self.scrolled = 0  # lines scrolled off the top so far
        self.wrap_pending = False  # autowrap on and the last column was just written

    # -- inspection ------------------------------------------------------

    @property
    def cursor(self) -> tuple[int, int]:
        return self.x, self.y

    def grid(self, x: int = 0, y: int = 0, width: int | None = None, height: int | None = None) -> Grid:
        """A copy of a screen region (default: the whole screen)."""
        width = self.width - x if width is None else width
        height = self.height - y if height is None else height
        return Grid(width, height, [c for line in self.lines[y : y + height] for c in line[x : x + width]])

    def line(self, y: int) -> str:
        return "".join(c.ch for c in self.lines[y]).rstrip()

    # -- input -----------------------------------------------------------

    def feed(self, data: str) -> VT:
        pos = 0
        while pos < len(data):
            m = _TOKEN.match(data, pos)
            assert m is not None  # every character matches some branch
            pos = m.end()
            if (text := m["text"]) is not None:
                for ch in text:
                    self._put(ch)
            elif (ctl := m["ctl"]) is not None:
                self.wrap_pending = False
                self._control(ctl, data, m.start())
            elif (esc := m["esc"]) is not None:
                self.wrap_pending = False
                self._esc(esc, data, m.start())
            else:
                if not m["priv"] and m["final"] != "m":  # modes and colors keep a pending wrap
                    self.wrap_pending = False
                self._csi(m["priv"], m["params"], m["final"], data, m.start())
        return self

    def _fail(self, what: str, data: str, at: int) -> None:
        raise VTError(f"unsupported {what} at offset {at}: {data[max(0, at - 20) : at + 20]!r}")

    def _blank_line(self, bg: Any) -> list[Cell]:
        return [Cell(" ", None, bg)] * self.width

    def _put(self, ch: str) -> None:
        if self.wrap_pending:
            self.wrap_pending = False
            if self.autowrap:
                self.x = 0
                self._line_feed()
        self.lines[self.y][self.x] = Cell(ch, self.fg, self.bg)
        if self.x < self.width - 1:
            self.x += 1
        elif self.autowrap:
            self.wrap_pending = True

    def _line_feed(self) -> None:
        if self.y == self.height - 1:
            del self.lines[0]
            self.lines.append(self._blank_line(self.bg))
            self.scrolled += 1
        else:
            self.y += 1

    def _control(self, ch: str, data: str, at: int) -> None:
        if ch == "\r":
            self.x = 0
        elif ch == "\n":
            self._line_feed()
        else:
            self._fail(f"control char {ch!r}", data, at)

    def _esc(self, ch: str, data: str, at: int) -> None:
        if ch == "7":
            self.saved = (self.x, self.y, self.fg, self.bg)
        elif ch == "8":
            if self.saved is None:
                raise VTError("DECRC without a preceding DECSC")
            self.x, self.y, self.fg, self.bg = self.saved
        else:
            self._fail(f"escape ESC {ch!r}", data, at)

    def _csi(self, priv: str, params: str, final: str, data: str, at: int) -> None:
        if priv:
            if final not in "hl" or not params:
                self._fail("private CSI", data, at)
            for p in params.split(";"):
                self.modes[int(p)] = final == "h"
                if p == "7":
                    self.autowrap = final == "h"
            return
        if final == "m":
            self._sgr(params, data, at)
            return
        nums = [int(p) if p else 0 for p in params.split(";")] if params else []
        if final == "H":
            if len(nums) > 2:
                self._fail("CUP", data, at)
            row, col = (nums + [0, 0])[:2]
            self.y = min(self.height, max(1, row)) - 1
            self.x = min(self.width, max(1, col)) - 1
            return
        if len(nums) > 1:
            self._fail(f"CSI {final}", data, at)
        n = nums[0] if nums else 0
        if final == "A":
            self.y = max(0, self.y - max(1, n))
        elif final == "B":
            self.y = min(self.height - 1, self.y + max(1, n))
        elif final == "G":
            self.x = min(self.width, max(1, n)) - 1
        elif final == "J" and n == 2:
            self.lines = [self._blank_line(self.bg) for _ in range(self.height)]
        elif final == "J" and n == 0:  # from the cursor to the end of the screen
            self.lines[self.y][self.x :] = self._blank_line(self.bg)[self.x :]
            for y in range(self.y + 1, self.height):
                self.lines[y] = self._blank_line(self.bg)
        elif final == "K" and n == 2:
            self.lines[self.y] = self._blank_line(self.bg)
        else:
            self._fail(f"CSI {params}{final}", data, at)

    def _sgr(self, params: str, data: str, at: int) -> None:
        vals = [int(p) if p else 0 for p in params.split(";")] if params else [0]
        i = 0
        while i < len(vals):
            v = vals[i]
            if v == 0:
                self.fg = self.bg = None
                i += 1
            elif v in (39, 49):
                setattr(self, "fg" if v == 39 else "bg", None)
                i += 1
            elif v in (38, 48) and i + 1 < len(vals) and vals[i + 1] in (2, 5):
                if vals[i + 1] == 2:
                    color: Any = tuple(vals[i + 2 : i + 5])
                    ok = len(color) == 3 and all(0 <= c <= 255 for c in color)
                    i += 5
                else:
                    color = vals[i + 2] if i + 2 < len(vals) else -1
                    ok = 0 <= color <= 255
                    i += 3
                if not ok:
                    self._fail("SGR color", data, at)
                setattr(self, "fg" if v == 38 else "bg", color)
            else:
                self._fail(f"SGR {v}", data, at)


class Recorder:
    """Wraps an output stream and records every ``write`` call."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.writes: list[str] = []

    def write(self, s: str) -> int:
        self.writes.append(s)
        return self.inner.write(s)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)
