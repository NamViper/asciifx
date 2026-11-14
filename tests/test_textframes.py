"""TextFramesSource: file and directory formats, header, delimiter, ANSI colors, centering."""

import io
import tracemalloc
from collections.abc import Sequence

import pytest

from asciifx import play
from asciifx.core import BLANK, Cell, Grid
from asciifx.sources import TextFramesSource, ansi_to_grid
from asciifx.sources.textframes import _MAX_PAD, VGA_PALETTE, palette_256
from asciifx.term import Terminal

ESC = "\x1b"


def write(tmp_path, text: str, name: str = "anim.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def text_grid(*lines: str) -> Grid:
    """A grid from equal-length lines; spaces are BLANK."""
    assert len({len(line) for line in lines}) == 1
    return Grid.from_text("\n".join(lines))


# -- frames and timing -------------------------------------------------------------

THREE = "AA\nAA\n---\nB\n---\nCCC\n"


def test_three_frames_exact_grids_at_fixed_t(tmp_path):
    src = TextFramesSource(write(tmp_path, THREE))  # 10 fps
    assert len(src.frames) == 3 and src.duration == pytest.approx(0.3)
    # Shared box: 3 wide (CCC) x 2 high (AA/AA), centered in 7x4 at (2, 1).
    assert src.frame(0.0, 7, 4) == text_grid("       ", "  AA   ", "  AA   ", "       ")
    assert src.frame(0.1, 7, 4) == text_grid("       ", "  B    ", "       ", "       ")
    assert src.frame(0.25, 7, 4) == text_grid("       ", "  CCC  ", "       ", "       ")
    assert src.frame(0.3, 7, 4) == src.frame(0.0, 7, 4)  # loops
    assert src.index(10.15) == 2 and src.frame(10.15, 7, 4) == src.frame(0.25, 7, 4)


def test_no_loop_holds_last_frame(tmp_path):
    src = TextFramesSource(write(tmp_path, THREE), fps=2, loop=False)
    assert src.duration == 1.5
    assert [src.index(t) for t in (0, 0.49, 0.5, 1.0, 1.5, 99)] == [0, 0, 1, 2, 2, 2]


def test_fps_header_overrides_argument(tmp_path):
    src = TextFramesSource(write(tmp_path, "# asciifx fps=4\n" + THREE), fps=30)
    assert src.fps == 4.0 and src.duration == 0.75
    assert src.index(0.25) == 1 and src.index(0.5) == 2
    assert src.frames[0].to_text() == "AA\nAA"  # the header is not part of a frame
    with pytest.raises(ValueError, match="fps"):
        TextFramesSource(write(tmp_path, "# asciifx fps=fast\nA\n"))
    with pytest.raises(ValueError, match="fps"):
        TextFramesSource(write(tmp_path, "A\n", "b.txt"), fps=0)
    plain = TextFramesSource(write(tmp_path, "# just a comment\n---\nX\n", "c.txt"))
    assert len(plain.frames) == 2  # not a header: an ordinary line of art


def test_delimiter(tmp_path):
    text = "%%\n---\nA\n%%\n---\n%%\n\n%%\nC\n%%\n"
    src = TextFramesSource(write(tmp_path, text), delimiter="%%")
    # "---" is art here; leading/trailing delimiters add no frame; an empty line between
    # two is a blank frame. All frames share the 3x2 box.
    assert [f.to_text() for f in src.frames] == ["---\nA", "---\n", "\n", "C\n"]
    header = TextFramesSource(write(tmp_path, "# asciifx delimiter=%%\nA\n%%\nB\n", "h.txt"))
    assert header.delimiter == "%%" and len(header.frames) == 2
    exact = TextFramesSource(write(tmp_path, "A\n--- \nB\n", "e.txt"))
    assert len(exact.frames) == 1  # only a line that is exactly the delimiter splits
    with pytest.raises(ValueError, match="no frames"):
        TextFramesSource(write(tmp_path, "", "empty.txt"))


def test_directory_of_frames_in_natural_order(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    (d / "frame10.txt").write_text("C\n")
    (d / "frame2.txt").write_text("B\n---\n")  # one file = one frame, delimiter included
    (d / "frame1.txt").write_text("# asciifx fps=5\nA\n")
    (d / "notes.md").write_text("ignored")
    src = TextFramesSource(d, fps=99)
    assert src.fps == 5.0
    assert [f.to_text() for f in src.frames] == ["A\n", "B\n---", "C\n"]
    assert src.frame(0.2, 3, 2) == text_grid("B  ", "---")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match=r"\*\.txt"):
        TextFramesSource(empty)


# -- placement ---------------------------------------------------------------------


def test_centering_and_cropping(tmp_path):
    src = TextFramesSource(write(tmp_path, "abcd\nefgh\n"))
    assert src.frame(0, 6, 4) == text_grid("      ", " abcd ", " efgh ", "      ")
    assert src.frame(0, 7, 3) == text_grid(" abcd  ", " efgh  ", "       ")  # odd: left/up
    assert src.frame(0, 2, 1) == text_grid("fg")  # larger than the area: centered crop
    assert src.frame(0, 0, 5) == Grid(0, 5)


def test_shared_margins_are_trimmed_and_offsets_kept(tmp_path):
    src = TextFramesSource(write(tmp_path, "\n   X\n---\n\n    Y\n"))
    # Both frames start with a blank line and 3 blank columns: trimmed. Y stays 1 right of X.
    assert [f.to_text(rstrip=False) for f in src.frames] == ["X ", " Y"]


# -- lazy frames and memory --------------------------------------------------------


def crop(grid: Grid, left: int, top: int, width: int, height: int) -> Grid:
    """The ``width`` x ``height`` part of ``grid`` at (left, top); BLANK outside it."""
    out = Grid(width, height)
    for y in range(height):
        for x in range(width):
            if grid.in_bounds(left + x, top + y):
                out.set(x, y, grid.get(left + x, top + y))
    return out


def ink(grid: Grid) -> list[tuple[int, int, str]]:
    """(x, y, char) of each visible cell, row by row."""
    return [(i % grid.width, i // grid.width, c.ch) for i, c in enumerate(grid.cells) if c != BLANK]


def test_frames_are_a_lazy_sequence_equal_to_the_eager_crops(tmp_path):
    texts = [
        "\n  AB",
        f"\n\n   {ESC}[31mC{ESC}[4;7HD",
        "",
        f"\n   {ESC}[44m E{ESC}[0m\n\n\n    F",
    ]
    src = TextFramesSource(write(tmp_path, "\n---\n".join(texts) + "\n"))
    # What the eager version stored: each frame cut to the union box of visible cells.
    grids = [ansi_to_grid(text) for text in texts]
    seen = [(x, y) for g in grids for x, y, _ in ink(g)]
    left, top = min(x for x, _ in seen), min(y for _, y in seen)
    width, height = max(x for x, _ in seen) + 1 - left, max(y for _, y in seen) + 1 - top
    assert (left, top, width, height) == (2, 1, 5, 4)
    expected = [crop(g, left, top, width, height) for g in grids]

    frames = src.frames
    assert isinstance(frames, Sequence) and len(frames) == 4
    assert list(frames) == [frames[i] for i in range(4)] == expected
    assert frames[-1] == expected[3] and frames[-4] == expected[0]
    assert frames[1:3] == expected[1:3] and list(reversed(frames)) == expected[::-1]
    for i in (4, -5):
        with pytest.raises(IndexError):
            frames[i]
    with pytest.raises(TypeError):
        frames[0] = expected[0]  # type: ignore[index]
    # frame() cuts the screen straight out of the parsed frame; same as centering the crop.
    for i, art in enumerate(expected):
        for w, h in ((0, 0), (1, 1), (3, 2), (5, 4), (8, 7), (6, 9)):
            x0, y0 = (w - art.width) // 2, (h - art.height) // 2
            assert src.frame(i / src.fps, w, h) == crop(art, -x0, -y0, w, h), (i, w, h)


def test_many_far_apart_frames_stay_small_in_memory(tmp_path):
    # 100 one-character frames whose characters together span a 300x300 box. Storing
    # every frame cropped to that box took 100 x 300 x 300 cell pointers (~72 MB).
    spots = [k * 299 // 99 for k in range(100)]  # 0 ... 299 along the diagonal
    path = write(tmp_path, "\n---\n".join(f"{ESC}[{p + 1};{p + 1}H*" for p in spots) + "\n")
    tracemalloc.start()
    try:
        src = TextFramesSource(path)
        first, middle = src.frame(0.0, 80, 24), src.frame(5.0, 80, 24)
        indexed = src.frames[50]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 20 * 2**20, f"peak {peak / 2**20:.1f} MB"
    assert len(src.frames) == 100 and (indexed.width, indexed.height) == (300, 300)
    assert indexed.get(spots[50], spots[50]) == Cell("*")
    assert first == Grid(80, 24)  # frame 0's character is in the box's corner, off screen
    # The 300x300 box is centered on 80x24, so box (x, y) shows at (x - 110, y - 138).
    assert ink(middle) == [(spots[50] - 110, spots[50] - 138, "*")]


def test_frame_builds_only_the_visible_part(tmp_path):
    # A long line in one frame, many lines in the other: a 2000x2001 shared box (32 MB of
    # cell pointers per cropped frame) from 5 KB of plain text. The screen needs 80x24 of it.
    tall = "\n" * 1000 + " " * 1000 + "y" + "\n" * 1000 + "z"
    src = TextFramesSource(write(tmp_path, "x" * 2000 + "\n---\n" + tall + "\n"))
    tracemalloc.start()
    try:
        wide, middle = src.frame(0.0, 80, 24), src.frame(0.1, 80, 24)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 2**20, f"peak {peak / 2**20:.1f} MB"
    assert wide == Grid(80, 24)  # the x row is the box's top edge, off screen
    # Centered: box (x, y) shows at (x - 960, y - 989), so y at (1000, 1000) is at (40, 11).
    assert ink(middle) == [(40, 11, "y")]


def test_blank_padding_from_cursor_movement_is_limited(tmp_path, monkeypatch):
    monkeypatch.setattr("asciifx.sources.textframes._MAX_PAD", 5000)
    line = f"{ESC}[999Cx"  # 10 bytes: 999 blanks, then x
    assert ansi_to_grid("\n".join([line] * 5)).width == 1000  # 4995 blanks: fits
    path = write(tmp_path, "ok\n---\n" + "\n".join([line] * 6) + "\n")
    message = r"anim\.txt: frame 2: too much cursor movement \(more than 5,000 blank cells\)"
    with pytest.raises(ValueError, match=message):
        TextFramesSource(path)
    # Every jump counts: tabs, and ESC[B under a long line (the column is kept).
    for text in ("\t" * 700 + "x", "x" * 3000 + f"{ESC}[By{ESC}[By"):
        with pytest.raises(ValueError, match="too much cursor movement"):
            ansi_to_grid(text)
    # Text is one cell per character: long lines, many lines and spaces aren't limited.
    for text in ("x" * 20_000, "\n" * 20_000 + "y", " " * 20_000 + "y"):
        assert ansi_to_grid(text).to_text() == text


def test_padding_limit_bounds_the_memory_of_a_crafted_frame(tmp_path):
    # 10-byte lines of 999 blanks + x, just past the real limit: a 40 KB file. Unlimited,
    # a few MB of these parsed to hundreds of millions of cells.
    path = write(tmp_path, f"{ESC}[999Cx\n" * (_MAX_PAD // 999 + 1))
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="frame 1: too much cursor movement"):
            TextFramesSource(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 48 * 2**20, f"peak {peak / 2**20:.1f} MB"


def test_a_full_screen_of_positioned_text_is_within_the_limit(tmp_path):
    # The most a 1000x1000 frame can pad: every row reached by ESC[row;1000H (~1M blanks).
    full = "".join(f"{ESC}[{y};1000H#" for y in range(1, 1001))
    src = TextFramesSource(write(tmp_path, full + "\n---\nplain text\n"))
    big = src.frames[0]
    assert len(src.frames) == 2 and (big.width, big.height) == (1000, 1000)
    assert big.get(999, 0) == big.get(999, 999) == Cell("#") and big.get(998, 999) == BLANK


# -- ANSI colors -------------------------------------------------------------------


def test_ansi_sgr_colors_exact_cells():
    text = (
        f"{ESC}[31mr{ESC}[1mR{ESC}[22mr{ESC}[0m."
        f"{ESC}[92mg{ESC}[44m {ESC}[104mb{ESC}[49m{ESC}[39md\n"
        f"{ESC}[38;5;196mx{ESC}[38;5;21my{ESC}[38;5;244mz{ESC}[38;5;3mw"
        f"{ESC}[48;2;1;2;3;38;2;250;128;0mt{ESC}[38:2::9:8:7mc{ESC}[48:5:232mk{ESC}[mn"
    )
    grid = ansi_to_grid(text)
    assert (grid.width, grid.height) == (8, 2)
    assert grid.cells == [
        Cell("r", (170, 0, 0)), Cell("R", (255, 85, 85)), Cell("r", (170, 0, 0)), Cell("."),
        Cell("g", (85, 255, 85)), Cell(" ", None, (0, 0, 170)), Cell("b", (85, 255, 85), (85, 85, 255)),
        Cell("d"),
        Cell("x", (255, 0, 0)), Cell("y", (0, 0, 255)), Cell("z", (128, 128, 128)),
        Cell("w", (170, 85, 0)), Cell("t", (250, 128, 0), (1, 2, 3)), Cell("c", (9, 8, 7), (1, 2, 3)),
        Cell("k", (9, 8, 7), (8, 8, 8)), Cell("n"),
    ]  # fmt: skip


def test_ansi_bold_brightens_only_basic_colors_and_spaces():
    grid = ansi_to_grid(f"{ESC}[1;34mB{ESC}[38;5;4mb{ESC}[94m {ESC}[45m ")
    assert grid.cells == [
        Cell("B", VGA_PALETTE[12]), Cell("b", VGA_PALETTE[4]), BLANK, Cell(" ", None, VGA_PALETTE[5])
    ]


def test_ansi_other_sequences_and_control_characters():
    text = f"{ESC}]0;title\x07{ESC}[2J{ESC}[H{ESC}[?25la{ESC}[3Cb\tc\nxyz\rQ{ESC}(B\x07!"
    assert ansi_to_grid(text).to_text() == "a   b   c\nQ!z"


def test_ansi_cursor_movement():
    text = f"abc\ndef{ESC}[1;2HX{ESC}[B{ESC}[DY{ESC}[A{ESC}[5GZ{ESC}[4;3fW"
    assert ansi_to_grid(text).to_text() == "aXc Z\ndYf\n\n  W"


def test_ansi_frames_in_a_file_reset_colors(tmp_path):
    src = TextFramesSource(write(tmp_path, f"{ESC}[32mA\n---\nB{ESC}[0m\n"))
    assert src.frames[0].cells == [Cell("A", VGA_PALETTE[2])]
    assert src.frames[1].cells == [Cell("B")]  # green does not carry into the next frame


def test_dos_ansi_art_cp437_crlf_and_sauce(tmp_path):
    path = tmp_path / "art.ans"
    path.write_bytes(b"\x1b[1;33m\xdb\xdb\r\n\xb0\xb1\r\n\x1aSAUCE00 title...")
    src = TextFramesSource(path)
    assert src.frames[0].to_text() == "██\n░▒"
    assert src.frames[0].get(0, 0) == Cell("█", VGA_PALETTE[11])


def test_palette_256():
    assert [palette_256(n) for n in (1, 9, 16, 21, 196, 231, 232, 255)] == [
        (170, 0, 0), (255, 85, 85), (0, 0, 0), (0, 0, 255), (255, 0, 0), (255, 255, 255),
        (8, 8, 8), (238, 238, 238),
    ]  # fmt: skip
    with pytest.raises(ValueError):
        palette_256(256)


def test_plays_as_a_source(tmp_path, monkeypatch):
    monkeypatch.setenv("COLUMNS", "5")
    monkeypatch.setenv("LINES", "3")
    out = io.StringIO()
    play(TextFramesSource(write(tmp_path, THREE)), terminal=Terminal(out=out))
    assert out.getvalue() == " AA\n AA\n\n"
