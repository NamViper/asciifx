"""Text-frame animations: frames of plain or ANSI-colored text, from a file or a directory.

File format::

    # asciifx fps=12        <- optional header, first line only
    frame 1, line 1
    frame 1, line 2
    ---                      <- a line that is exactly the delimiter
    frame 2, line 1
    ...

The header may set ``fps`` and ``delimiter`` (``# asciifx fps=12 delimiter=%%``);
its values override the constructor's. A delimiter on the first or last line
doesn't start an empty frame; two delimiters in a row make a blank frame.

A directory works too: each ``*.txt`` file in it is one frame, in natural name
order (``frame2.txt`` before ``frame10.txt``). A header line at the top of a
file is dropped, and the first file's header applies.

ANSI SGR colors become cell colors:

* 30-37 / 40-47 and 90-97 / 100-107: the 16 VGA colors. Bold (1, undone by 22)
  turns 30-37 bright, as classic ANSI art expects.
* 38;5;n / 48;5;n: the xterm 256-color palette (0-15 are the VGA colors).
* 38;2;r;g;b / 48;2;r;g;b: truecolor. The colon forms (38:2::r:g:b, 38:5:n)
  work too.
* 0 resets everything, 39 the foreground, 49 the background.

Colors start reset at the top of every frame. Cursor movement (CSI A/B/C/D,
G, H/f) is followed within the frame, so frames captured from a terminal work;
cells skipped over stay blank. Other SGR attributes and other escape sequences
are ignored. Tabs advance to the next multiple of 8 columns and ``\\r`` returns
to the start of the line. Files that aren't valid UTF-8 are read as CP437 (DOS
ANSI art), and a SAUCE record after the DOS end-of-file mark is ignored.

Limits, so a small crafted file can't take gigabytes: like at a screen edge,
moves stop at row / column 1000; and a frame whose cursor movement and tabs
would need more than 4,000,000 blank cells (a full 1000x1000 frame needs ~1M)
is rejected with ValueError. Plain text, however long, isn't limited.

Frames are placed in the union bounding box of all frames' visible cells
(margins that every frame shares are trimmed), and that box is centered.
Only the frames' text is kept in memory; a frame is parsed when it is needed.

Stdlib only.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import overload

from ..core.grid import BLANK, Cell, Color, Grid

type FramesPath = str | os.PathLike[str]

VGA_PALETTE: tuple[Color, ...] = (
    (0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0),
    (0, 0, 170), (170, 0, 170), (0, 170, 170), (170, 170, 170),
    (85, 85, 85), (255, 85, 85), (85, 255, 85), (255, 255, 85),
    (85, 85, 255), (255, 85, 255), (85, 255, 255), (255, 255, 255),
)  # fmt: skip
"""RGB of ANSI colors 0-15 (black, red, green, yellow, blue, magenta, cyan, white; then bright)."""

_CUBE = (0, 95, 135, 175, 215, 255)  # xterm 6x6x6 color cube levels


def palette_256(n: int) -> Color:
    """RGB of xterm-256 color ``n``: VGA colors, then the 6x6x6 cube, then 24 grays."""
    if not 0 <= n <= 255:
        raise ValueError(f"256-color index out of range: {n}")
    if n < 16:
        return VGA_PALETTE[n]
    if n < 232:
        n -= 16
        return _CUBE[n // 36], _CUBE[n // 6 % 6], _CUBE[n % 6]
    v = 8 + 10 * (n - 232)
    return v, v, v


# -- ANSI parsing ------------------------------------------------------------------

_MAX_MOVE = 1000
"""Cursor-movement sequences stop at this column / row (or at text that is already
further out), the way a terminal stops them at the screen edge, so a sequence like
``ESC[999999999C`` can't make a frame billions of cells wide."""

_MAX_PAD = 4_000_000
"""Most blank cells that cursor movement and tabs may add to one frame (32 MB of cell
slots); a frame needing more is rejected. A full 1000x1000 frame of positioned text
needs ~1M, but without a cap ``ESC[999Cx`` lines (10 bytes for 1000 cells) turn a
1 MB file into 100M cells. Text itself is one cell per character and isn't counted."""


def _param(p: str) -> int:
    """A numeric CSI parameter; empty or malformed is 0, huge values saturate."""
    if not p.isdigit():
        return 0
    digits = p.lstrip("0")
    return int(digits or 0) if len(digits) <= 6 else 999_999  # int() refuses > 4300 digits


_TOKEN = re.compile(
    r"\x1b(?:"
    r"\[(?P<params>[0-?]*)[ -/]*(?P<final>[@-~])"  # CSI ... final byte
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC ... BEL or ST
    r"|[PX^_][^\x1b]*(?:\x1b\\)?"  # DCS / SOS / PM / APC ... ST
    r"|[ -/]*[0-~]"  # two-character escapes: ESC ( B, ESC =, ESC 7, ...
    r"|)"  # a lone ESC
    r"|(?P<text>[^\x1b]+)"
)


class _Pen:
    """Current SGR colors while parsing."""

    __slots__ = ("fg", "fg_index", "bg", "bold")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.fg: Color | None = None
        self.fg_index: int | None = None  # 0-7 when set by SGR 30-37 (bold brightens it)
        self.bg: Color | None = None
        self.bold = False

    def cell(self, ch: str) -> Cell:
        if ch == " ":
            return BLANK if self.bg is None else Cell(" ", None, self.bg)
        fg = self.fg
        if self.fg_index is not None:
            fg = VGA_PALETTE[self.fg_index + (8 if self.bold else 0)]
        return Cell(ch, fg, self.bg)

    def set_fg(self, color: Color | None, index: int | None = None) -> None:
        self.fg, self.fg_index = color, index

    def sgr(self, params: str) -> None:
        parts = params.split(";")
        i = 0
        while i < len(parts):
            part = parts[i]
            i += 1
            if ":" in part:  # ITU T.416 sub-parameters: 38:2::r:g:b, 38:5:n
                head, *sub = part.split(":")
                if head in ("38", "48"):
                    color = _extended_color(sub, colons=True)
                    if color is not None:
                        self._apply(int(head), color)
                continue
            code = _param(part) if part.isdigit() else 0 if part == "" else -1
            if code in (38, 48):
                color, used = _extended_color(parts[i:], colons=False), _extended_len(parts[i:])
                i += used
                if color is not None:
                    self._apply(code, color)
            elif code == 0:
                self.reset()
            elif code == 1:
                self.bold = True
            elif code == 22:
                self.bold = False
            elif 30 <= code <= 37:
                self.set_fg(None, code - 30)
            elif code == 39:
                self.set_fg(None)
            elif 90 <= code <= 97:
                self.set_fg(VGA_PALETTE[code - 90 + 8])
            elif 40 <= code <= 47:
                self.bg = VGA_PALETTE[code - 40]
            elif code == 49:
                self.bg = None
            elif 100 <= code <= 107:
                self.bg = VGA_PALETTE[code - 100 + 8]

    def _apply(self, code: int, color: Color) -> None:
        if code == 38:
            self.set_fg(color)
        else:
            self.bg = color


def _extended_len(parts: list[str]) -> int:
    """How many ``;`` parameters after 38/48 belong to it: 5;n -> 2, 2;r;g;b -> 4."""
    if parts and parts[0] == "5":
        return 2
    if parts and parts[0] == "2":
        return 4
    return 0


def _extended_color(parts: list[str], *, colons: bool) -> Color | None:
    """The color of a 38/48 sequence's parameters (after the 38/48), or None if malformed."""
    try:
        if parts and parts[0] == "5" and len(parts) >= 2:
            return palette_256(int(parts[1]))
        if parts and parts[0] == "2":
            # 38;2;r;g;b and 38:2:r:g:b, or 38:2:<colorspace>:r:g:b in the colon form
            rgb = parts[2:5] if colons and len(parts) >= 5 else parts[1:4]
            if len(rgb) == 3:
                r, g, b = (min(255, int(v or 0)) for v in rgb)
                return r, g, b
    except ValueError:
        pass
    return None


def _parse(text: str) -> list[list[Cell]]:
    """Rows of cells of one frame of (possibly ANSI-colored) text.

    Raises ValueError if reaching moved-to columns needs more than ``_MAX_PAD`` blanks.
    """
    pen = _Pen()
    rows: list[list[Cell]] = [[]]
    y = x = 0
    padded = 0  # blanks added so far to reach columns the cursor jumped to
    for m in _TOKEN.finditer(text):
        chunk = m.group("text")
        if chunk is None:
            final = m.group("final")
            params = m.group("params") or ""
            if final is None or params[:1] in ("<", "=", ">", "?"):  # not CSI, or private
                continue
            if final == "m":
                pen.sgr(params)
                continue
            args = [_param(p) for p in params.split(";")]
            n = args[0] or 1
            if final == "C":  # cursor forward / back / up / down
                x = min(x + n, max(x, _MAX_MOVE - 1))
            elif final == "D":
                x = max(0, x - n)
            elif final == "A":
                y = max(0, y - n)
            elif final == "B":
                y = min(y + n, max(y, len(rows) - 1, _MAX_MOVE - 1))
            elif final == "G":  # to column n
                x = min(n, _MAX_MOVE) - 1
            elif final in ("H", "f"):  # to row;column
                col = args[1] if len(args) > 1 and args[1] else 1
                y, x = min(n, _MAX_MOVE) - 1, min(col, _MAX_MOVE) - 1
            continue
        for ch in chunk:
            if ch == "\n":
                y, x = y + 1, 0
                rows.extend([] for _ in range(y + 1 - len(rows)))
            elif ch == "\r":
                x = 0
            elif ch == "\t":
                x = (x // 8 + 1) * 8
            elif ch < " " or "\x7f" <= ch < "\xa0":
                continue
            else:
                rows.extend([] for _ in range(y + 1 - len(rows)))
                row = rows[y]
                if x >= len(row):
                    # Checked before allocating. Every jump counts, not just C/G/H: after a long
                    # line, ESC[B keeps the column, so 4 bytes can pad a whole row that wide.
                    padded += x - len(row)
                    if padded > _MAX_PAD:
                        raise ValueError(
                            f"too much cursor movement (more than {_MAX_PAD:,} blank cells)"
                        )
                    row.extend([BLANK] * (x + 1 - len(row)))
                row[x] = pen.cell(ch)
                x += 1
    return rows


def ansi_to_grid(text: str) -> Grid:
    """A grid of (possibly ANSI-colored) text, one row per line, as wide as its widest line.

    Raises ValueError if its cursor movement needs too many blank cells (see the module
    docstring)."""
    rows = _parse(text)
    width = max((len(row) for row in rows), default=0)
    return Grid(width, len(rows), [c for row in rows for c in row + [BLANK] * (width - len(row))])


# -- files -------------------------------------------------------------------------

_HEADER = re.compile(r"#\s*asciifx\b(.*)")
_OPTION = re.compile(r"(\w+)=(\S+)")


def _read(path: Path) -> str:
    # SAUCE metadata follows the DOS end-of-file mark. Cut it off before choosing the
    # encoding: its binary fields (file size, width) aren't UTF-8.
    data = path.read_bytes().split(b"\x1a", 1)[0]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp437")  # DOS-era ANSI art
    return text.replace("\r\n", "\n")


def _take_header(text: str) -> tuple[dict[str, str], str]:
    """(header options, text without the header line)."""
    first, sep, rest = text.partition("\n")
    m = _HEADER.fullmatch(first.rstrip())
    if m is None:
        return {}, text
    return dict(_OPTION.findall(m.group(1))), rest


def _strip_final_newline(text: str) -> str:
    return text[:-1] if text.endswith("\n") else text


def _split(text: str, delimiter: str) -> list[str]:
    lines = _strip_final_newline(text).split("\n")
    frames: list[list[str]] = [[]]
    for line in lines:
        if line == delimiter:
            frames.append([])
        else:
            frames[-1].append(line)
    if len(frames) > 1 and not frames[0]:  # leading delimiter
        frames.pop(0)
    if len(frames) > 1 and not frames[-1]:  # trailing delimiter
        frames.pop()
    if frames == [[]] or frames == [[""]]:
        return []
    return ["\n".join(frame) for frame in frames]


def _natural_key(path: Path) -> tuple[list[int | str], str]:
    parts = re.split(r"(\d+)", path.name)
    return [int(p) if i % 2 else p.lower() for i, p in enumerate(parts)], path.name


type Box = tuple[int, int, int, int]
"""(left, top, right, bottom), right and bottom exclusive."""


def _bbox(texts: list[str]) -> Box:
    """The box of the visible cells of all frames; (0, 0, 0, 0) if none.

    Each frame is parsed and dropped before the next, so this needs memory for one
    frame, not all of them."""
    left = top = math.inf
    right = bottom = 0
    for i, text in enumerate(texts):
        try:
            rows = _parse(text)
        except ValueError as exc:
            raise ValueError(f"frame {i + 1}: {exc}") from None
        for y, row in enumerate(rows):
            xs = [x for x, cell in enumerate(row) if cell != BLANK]
            if xs:
                left, right = min(left, xs[0]), max(right, xs[-1] + 1)
                top, bottom = min(top, y), max(bottom, y + 1)
        del rows  # else it holds this frame while the next one is parsed
    if left is math.inf:
        return 0, 0, 0, 0
    return int(left), int(top), right, bottom


def _window(rows: list[list[Cell]], x: int, y: int, width: int, height: int) -> Grid:
    """The ``width`` x ``height`` part of parsed ``rows`` whose top-left is (x, y), which
    may be negative; cells outside the rows are BLANK."""
    grid = Grid(width, height)
    xs, xe = max(0, x), max(0, x + width)  # clamped: a negative slice bound would wrap
    for gy in range(max(0, -y), min(height, len(rows) - y)):
        part = rows[y + gy][xs:xe]
        dst = gy * width + xs - x
        grid.cells[dst : dst + len(part)] = part
    return grid


class _Frames(Sequence[Grid]):
    """Read-only frames cropped to the shared box, each parsed from its text when indexed.

    Storing every cropped frame let a few KB of crafted input (many tiny frames whose
    cells lie far apart, so the shared box is ~1000x1000) take gigabytes.
    """

    def __init__(self, texts: list[str], box: Box) -> None:
        self._texts = texts
        self._box = box

    def __len__(self) -> int:
        return len(self._texts)

    @overload
    def __getitem__(self, i: int) -> Grid: ...
    @overload
    def __getitem__(self, i: slice) -> list[Grid]: ...
    def __getitem__(self, i: int | slice) -> Grid | list[Grid]:
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(len(self)))]
        left, top, right, bottom = self._box
        return _window(_parse(self._texts[i]), left, top, right - left, bottom - top)

    def __iter__(self) -> Iterator[Grid]:
        return map(self.__getitem__, range(len(self)))


class TextFramesSource:
    """Frames of text played at ``fps``, centered; see the module docstring for the format.

    ``frame(t, ...)`` shows frame ``floor(t * fps)``, wrapping with ``loop`` and
    holding the last frame without it. Only the frames' text is kept: a frame is
    parsed when it is shown or indexed (and once at the start, one at a time, to
    find the shared box).
    """

    def __init__(
        self,
        path: FramesPath,
        *,
        fps: float = 10.0,
        loop: bool = True,
        delimiter: str = "---",
    ) -> None:
        self.path = Path(path)
        if self.path.is_dir():
            files = sorted(
                (f for f in self.path.iterdir() if f.suffix.lower() == ".txt" and f.is_file()),
                key=_natural_key,
            )
            if not files:
                raise ValueError(f"{self.path}: no *.txt frame files in this directory")
            texts, header = [], {}
            for i, f in enumerate(files):
                options, text = _take_header(_read(f))
                if i == 0:
                    header = options
                texts.append(_strip_final_newline(text))
        else:
            header, text = _take_header(_read(self.path))
            delimiter = header.get("delimiter", delimiter)
            texts = _split(text, delimiter)
            if not texts:
                raise ValueError(f"{self.path}: no frames")
        if "fps" in header:
            try:
                fps = float(header["fps"])
            except ValueError:
                raise ValueError(f"{self.path}: bad fps in header: {header['fps']!r}") from None
        if not (math.isfinite(fps) and fps > 0):
            raise ValueError(f"fps must be a positive number, got {fps}")
        if not delimiter:
            raise ValueError("delimiter must not be empty")
        self.fps = fps
        self.loop = loop
        self.delimiter = delimiter
        self._texts = texts
        try:  # parses every frame, so frame() and frames[i] can't fail later
            self._box = _bbox(texts)
        except ValueError as exc:
            raise ValueError(f"{self.path}: {exc}") from None
        self.frames: Sequence[Grid] = _Frames(texts, self._box)
        """Each frame, cropped to the shared bounding box (all the same size). Lazy:
        indexing parses the frame."""
        self._cache: tuple[tuple[int, int, int], Grid] | None = None

    @property
    def duration(self) -> float:
        """Seconds one pass through the frames takes."""
        return len(self.frames) / self.fps

    def index(self, t: float) -> int:
        """Which frame shows at time ``t``."""
        i = math.floor(max(t, 0.0) * self.fps + 1e-6)
        return i % len(self.frames) if self.loop else min(i, len(self.frames) - 1)

    def frame(self, t: float, width: int, height: int) -> Grid:
        """Frame ``index(t)`` centered in ``width`` x ``height``."""
        width, height = max(0, width), max(0, height)
        key = (self.index(t), width, height)
        if self._cache is None or self._cache[0] != key:
            # Cut the visible part straight out of the parsed frame instead of building the
            # cropped frame first: that can be far bigger than the screen. Every frame is
            # blank outside the shared box, so the window may extend past it.
            left, top, right, bottom = self._box
            x0, y0 = (width - (right - left)) // 2, (height - (bottom - top)) // 2  # odd: left/up
            rows = _parse(self._texts[key[0]])
            self._cache = (key, _window(rows, left - x0, top - y0, width, height))
        return self._cache[1].copy()
