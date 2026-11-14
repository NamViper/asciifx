import unicodedata

import pytest

from asciifx.core import BLANK, Cell, Grid, Layer, compose
from asciifx.fonts import FONTS, Font, _parse, get_font, render_text, text_width

REQUIRED = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
    ".,!?-_:;/\\@#&'\"()+=<>*%"
)
HEIGHTS = {"block": 4, "outline": 5, "small": 3, "ascii": 5}
RED, BLUE = (255, 0, 0), (0, 0, 255)


def lines(*rows: str) -> str:
    return "\n".join(rows)


def all_glyphs(font: Font):
    yield "fallback", font.fallback
    yield from font.glyphs.items()


# -- exact renderings ---------------------------------------------------------------

EXACT = {
    ("block", "HI"): lines(
        "██  ██ ██",
        "██▄▄██ ██",
        "██▀▀██ ██",
        "██  ██ ██",
    ),
    ("block", "2"): lines(
        "█████▄",
        " ▄▄▄██",
        "██▀▀▀",
        "██████",
    ),
    ("outline", "HI"): lines(
        "╷   ╷ ╶┬╴",
        "│   │  │",
        "├───┤  │",
        "│   │  │",
        "╵   ╵ ╶┴╴",
    ),
    ("outline", "2"): lines(
        "╭───╮",
        "    │",
        "╭───╯",
        "│",
        "└───╴",
    ),
    ("small", "HI"): lines(
        "█ █ ▀█▀",
        "█▀█  █",
        "▀ ▀ ▀▀▀",
    ),
    ("small", "2"): lines(
        "▀▀▄",
        "▄▀",
        "▀▀▀",
    ),
    ("ascii", "HI"): lines(
        "      ___",
        "|   |  |",
        "|___|  |",
        "|   |  |",
        "|   | _|_",
    ),
    ("ascii", "2"): lines(
        " ___",
        "/   \\",
        "  __/",
        " /",
        "/____",
    ),
}


@pytest.mark.parametrize("font,text", sorted(EXACT))
def test_exact_rendering(font, text):
    grid = render_text(text, font)
    assert grid.to_text() == EXACT[font, text]
    assert grid.height == HEIGHTS[font]
    assert grid.width == max(len(row) for row in EXACT[font, text].split("\n"))


# -- font data ------------------------------------------------------------------------


def test_registry():
    assert list(FONTS) == ["block", "outline", "small", "ascii"]
    assert {name: f.height for name, f in FONTS.items()} == HEIGHTS
    assert all(f.name == name for name, f in FONTS.items())


@pytest.mark.parametrize("name", HEIGHTS)
def test_every_required_character_present_with_right_height(name):
    font = FONTS[name]
    missing = [ch for ch in REQUIRED if ch not in font.glyphs]
    assert not missing
    for label, rows in all_glyphs(font):
        assert len(rows) == font.height, label
        assert len({len(r) for r in rows}) == 1, f"{label!r}: ragged rows"


@pytest.mark.parametrize("name", HEIGHTS)
def test_glyph_characters_are_single_width(name):
    for label, rows in all_glyphs(FONTS[name]):
        for ch in "".join(rows):
            assert unicodedata.east_asian_width(ch) not in ("W", "F"), (label, ch)
            assert not unicodedata.combining(ch), (label, ch)
            assert ch.isprintable() and unicodedata.category(ch) not in ("Cc", "Cf"), (label, ch)


@pytest.mark.parametrize("name", HEIGHTS)
def test_glyphs_have_no_blank_side_columns(name):
    # Spacing comes only from letter_spacing, so it is even between all letters.
    for label, rows in all_glyphs(FONTS[name]):
        if label == " ":
            continue
        for col in (0, len(rows[0]) - 1):
            assert any(r[col] != " " for r in rows), f"{label!r}: blank edge column"


def test_glyph_alphabets():
    def chars(name):
        return {ch for _, rows in all_glyphs(FONTS[name]) for ch in "".join(rows)} - {" "}

    assert chars("block") == set("█▀▄")
    assert chars("small") == set("█▀▄")
    assert all(ch.isascii() for ch in chars("ascii"))
    assert all("─" <= ch <= "╿" for ch in chars("outline") - {"•"})


def test_digits_have_equal_widths():
    for font in FONTS.values():
        assert len({font.char_width(d) for d in "023456789"}) == 1, font.name


# -- character mapping -------------------------------------------------------------------


@pytest.mark.parametrize("name", HEIGHTS)
def test_lowercase_maps_to_uppercase(name):
    assert render_text("hello, world", name) == render_text("HELLO, WORLD", name)


def test_accents_map_to_base_letters():
    assert render_text("Éé Ñ", "block") == render_text("EE N", "block")
    assert render_text("É", "small") == render_text("E", "small")  # decomposed accent


@pytest.mark.parametrize("name", HEIGHTS)
def test_unknown_characters_use_fallback(name):
    font = FONTS[name]
    for ch in ("Ω", "€", "\x1b", "🙂"):
        assert font.glyph(ch) == font.fallback
    grid = render_text("Ω", name)
    assert grid.to_text(rstrip=False).split("\n") == list(font.fallback)
    assert "\x1b" not in render_text("A\x1b[31mB", name).to_text()


def test_whitespace_renders_as_space():
    assert render_text("A\tB", "small") == render_text("A B", "small")


# -- width math -------------------------------------------------------------------------


def test_text_width():
    f = FONTS["block"]
    w = {ch: f.char_width(ch) for ch in "HI "}
    assert (w["H"], w["I"], w[" "]) == (6, 2, 3)
    assert text_width("HI", "block") == 6 + 1 + 2
    assert text_width("H I", "block") == 6 + 1 + 3 + 1 + 2
    assert text_width("HI", "block", letter_spacing=3) == 6 + 3 + 2
    assert text_width("HI\nHHH", f) == 3 * 6 + 2
    assert text_width("", "block") == 0


@pytest.mark.parametrize("name", HEIGHTS)
@pytest.mark.parametrize("text", ["ASCIIFX", "ACCESS GRANTED", "Hi!\nok 2026", ""])
def test_grid_size_matches_width_math(name, text):
    grid = render_text(text, name, line_spacing=2)
    n = text.count("\n") + 1
    assert grid.width == text_width(text, name)
    assert grid.height == n * HEIGHTS[name] + (n - 1) * 2


def test_letter_spacing_zero_and_line_spacing():
    grid = render_text("HI\nI", "small", letter_spacing=0, line_spacing=0)
    assert grid.to_text() == lines("█ █▀█▀", "█▀█ █", "▀ ▀▀▀▀", "▀█▀", " █", "▀▀▀")


# -- wrapping -------------------------------------------------------------------------


def test_word_wrap():
    grid = render_text("ACCESS GRANTED", "block", max_width=60)
    assert grid.to_text() == render_text("ACCESS\nGRANTED", "block").to_text()
    assert grid.width <= 60


def test_no_wrap_when_it_fits():
    text = "AB  C"  # double space kept when no wrapping is needed
    assert render_text(text, "small", max_width=100) == render_text(text, "small")


def test_wrap_joins_words_greedily():
    # each letter 3 wide in small: "A B" = 3+1+2+1+3 = 10
    grid = render_text("A B C D", "small", max_width=10)
    assert grid.to_text() == render_text("A B\nC D", "small").to_text()


def test_long_word_falls_back_to_character_wrap():
    # small: these letters are 3 wide, 1 spacing -> 3 letters = 11 columns;
    # the word's last piece "H" shares a line with the next word ("H I" = 10)
    grid = render_text("ABCDEFH I", "small", max_width=11)
    assert grid.to_text() == render_text("ABC\nDEF\nH I", "small").to_text()
    assert grid.width == 11


def test_glyph_wider_than_max_width_is_clipped():
    grid = render_text("MM", "block", max_width=4)
    assert grid.width == 4
    assert grid.height == 4 + 1 + 4  # one M per line
    assert grid.to_text().split("\n")[0] == "██▄"


def test_wrap_respects_explicit_newlines():
    grid = render_text("AB\nCD EF", "small", max_width=7)
    assert grid.to_text() == render_text("AB\nCD\nEF", "small").to_text()


# -- alignment ------------------------------------------------------------------------


def first_ink(grid: Grid, row: int) -> int:
    return next(x for x in range(grid.width) if grid.get(x, row) != BLANK)


@pytest.mark.parametrize("align,offset", [("left", 0), ("center", 8), ("right", 16)])
def test_alignment(align, offset):
    # small: "I" is 3 wide; "HHHHH" = 5*3 + 4 = 19 -> centre offset (19-3)//2 = 8
    grid = render_text("I\nHHHHH", "small", align=align)
    assert grid.width == 19
    assert first_ink(grid, 0) == offset
    assert first_ink(grid, 4) == 0  # the widest line always starts at 0


def test_center_alignment_with_wrapping():
    grid = render_text("AAA B", "small", align="center", max_width=11)
    assert grid.width == 11
    assert first_ink(grid, 4) == 4  # "B" (3 wide) centred in 11


# -- colours --------------------------------------------------------------------------


def test_fg_colours_ink_only():
    grid = render_text("HI", "block", fg=RED)
    for cell in grid.cells:
        assert cell == BLANK or (cell.fg == RED and cell.bg is None)
    assert grid.get(2, 0) == BLANK


def test_gradient_runs_top_to_bottom_on_each_line():
    grid = render_text("H\nH", "block", gradient=(RED, BLUE), line_spacing=1)
    expected = [RED, (170, 0, 85), (85, 0, 170), BLUE]
    for line_top in (0, 5):
        for dy, color in enumerate(expected):
            row = [c for c in grid.cells[(line_top + dy) * grid.width : (line_top + dy + 1) * grid.width]]
            assert {c.fg for c in row if c != BLANK} == {color}
    assert all(c == BLANK for c in grid.cells[4 * grid.width : 5 * grid.width])  # spacing row


def test_gradient_single_row_font_uses_top_colour():
    font = Font("tiny", 1, {" ": (" ",), "X": ("x",)}, ("?",))
    assert render_text("X", font, gradient=(RED, BLUE)).get(0, 0) == Cell("x", RED)


def test_blank_cells_are_transparent_when_composited():
    bg = Cell(".", BLUE)
    text = render_text("HI", "outline", fg=RED)
    out = compose(text.width, text.height, [Layer(Grid.filled(text.width, text.height, bg)), Layer(text)])
    assert out.get(1, 0) == bg  # gap inside the H
    assert out.get(0, 0) == Cell("╷", RED)


# -- errors -----------------------------------------------------------------------------


def test_get_font():
    assert get_font("Block") is FONTS["block"]
    assert get_font(FONTS["small"]) is FONTS["small"]
    with pytest.raises(ValueError, match="block, outline, small, ascii"):
        get_font("comic")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"align": "middle"},
        {"letter_spacing": -1},
        {"line_spacing": -1},
        {"max_width": 0},
        {"fg": RED, "gradient": (RED, BLUE)},
    ],
)
def test_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        render_text("HI", **kwargs)


@pytest.mark.parametrize(
    "sheet",
    [
        "A\nx\nx",  # no label
        "~A\nx",  # too few rows
        "~A\nx\n~B\nx\nx",  # label swallowed as a row
        "~A\n \n ",  # empty glyph
        "~A\nx\nx\n~A\ny\ny",  # duplicate
    ],
)
def test_malformed_sheets_rejected(sheet):
    with pytest.raises(ValueError):
        _parse(sheet, 2)
