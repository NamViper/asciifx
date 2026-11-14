"""TextFramesSource: file and directory formats, header, delimiter, ANSI colors, centering."""

import io

import pytest

from asciifx import play
from asciifx.core import BLANK, Cell, Grid
from asciifx.sources import TextFramesSource, ansi_to_grid
from asciifx.sources.textframes import VGA_PALETTE, palette_256
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
