"""Glyph (shape-matching) mode, the generated glyph masks, outline, and named styles."""

import subprocess
import sys

import pytest
from PIL import Image

from asciifx.core import BLANK, Cell
from asciifx.sources import ImageSource, glyph_masks, image_to_grid
from asciifx.sources.image import DEFAULT_RAMP, MASK_H, MASK_W
from asciifx.sources.styles import DIGITS, LETTERS, PRINTABLE, PUNCTUATION, STYLES

WHITE = (255, 255, 255)
CHARSETS = {
    "printable": PRINTABLE, "punctuation": PUNCTUATION, "letters": LETTERS, "digits": DIGITS, "binary": "01"
}


def cells(cols: int, rows: int, lit) -> Image.Image:
    """Black image of cols x rows glyph cells (MASK_W x MASK_H px each), white where lit(x, y)."""
    im = Image.new("RGB", (cols * MASK_W, rows * MASK_H))
    for y in range(im.height):
        for x in range(im.width):
            if lit(x, y):
                im.putpixel((x, y), WHITE)
    return im


def glyph(im: Image.Image, **kw) -> str:
    return image_to_grid(im, im.width // MASK_W, mode="glyph", **kw).to_text(rstrip=False)


def mask_values(ch: str) -> list[int]:
    return [int(d, 16) for d in glyph_masks.MASKS[ch] if d != " "]


# -- the generated mask table ------------------------------------------------------


def test_glyph_mask_table_is_complete():
    masks = glyph_masks.MASKS
    assert len(masks) == 95
    assert "".join(masks) == "".join(map(chr, range(0x20, 0x7F)))
    for ch, mask in masks.items():
        rows = mask.split(" ")
        assert len(rows) == glyph_masks.MASK_H and {len(r) for r in rows} == {glyph_masks.MASK_W}, ch
        assert all(0 <= v <= glyph_masks.LEVELS for v in mask_values(ch))
    assert set(mask_values(" ")) == {0}
    assert all(any(mask_values(ch)) for ch in PRINTABLE)  # every visible glyph has ink
    assert "Cascadia" in glyph_masks.FONT
    assert (MASK_W, MASK_H) == (glyph_masks.MASK_W, glyph_masks.MASK_H) == (5, 10)


def test_glyph_masks_look_like_their_glyphs():
    rows = glyph_masks.MASKS["|"].split(" ")
    assert all(r[2] != "0" and r[0] == r[1] == r[3] == r[4] == "0" for r in rows)  # centered stroke
    under = glyph_masks.MASKS["_"].split(" ")
    assert [r != "00000" for r in under] == [False] * 8 + [True, False]  # one low stroke


# -- exact glyph-mode output ---------------------------------------------------------


@pytest.mark.parametrize("name", CHARSETS)
def test_solid_ink_is_the_densest_glyph(name):
    charset = CHARSETS[name]
    densest = max(sum(mask_values(c)) for c in charset)
    got = glyph(cells(1, 1, lambda x, y: True), charset=charset)
    assert sum(mask_values(got)) == densest  # Q and W tie for letters, 0 and 8 for digits
    assert got == {"printable": "@", "punctuation": "@", "letters": "Q", "digits": "8", "binary": "0"}[name]


@pytest.mark.parametrize("name", CHARSETS)
def test_no_ink_is_blank(name):
    grid = image_to_grid(cells(2, 2, lambda x, y: False), 2, mode="glyph", charset=CHARSETS[name])
    assert set(grid.cells) == {BLANK}


@pytest.mark.parametrize(
    "name,expected",
    [("printable", "|"), ("punctuation", "|"), ("letters", "l"), ("digits", "1"), ("binary", "1")],
)
def test_thin_vertical_bar(name, expected):
    assert glyph(cells(1, 1, lambda x, y: x == 2), charset=CHARSETS[name]) == expected


def test_bottom_bar_is_underscore():
    bottom = cells(1, 1, lambda x, y: y >= MASK_H - 2)
    assert glyph(bottom, charset=PUNCTUATION) == "_"
    assert glyph(bottom) == "_"


def test_diagonals_are_slashes():
    rising = cells(1, 1, lambda x, y: abs((MASK_W - 1 - x) * MASK_H / MASK_W - y) < 1.2)
    falling = cells(1, 1, lambda x, y: abs(x * MASK_H / MASK_W - y) < 1.2)
    assert glyph(rising) == glyph(rising, charset=PUNCTUATION) == "/"
    assert glyph(falling) == glyph(falling, charset=PUNCTUATION) == "\\"
    # Across several cells: a 2x2-cell diagonal line becomes a staircase of slashes.
    assert glyph(cells(2, 2, lambda x, y: abs((9 - x) * 2 - y) < 1.5)) == " /\n/ "
    assert glyph(cells(2, 2, lambda x, y: abs(x * 2 - y) < 1.5)) == "\\ \n \\"


def test_thin_rectangle():
    frame = cells(5, 4, lambda x, y: 2 <= x <= 22 and 5 <= y <= 34 and not (3 <= x <= 21 and 6 <= y <= 33))
    assert glyph(frame) == ",---,\n|   |\n|   |\n'---'"


def test_invert_turns_dark_ink_into_glyphs():
    dark_bar = Image.new("RGB", (MASK_W, MASK_H), WHITE)
    for y in range(MASK_H):
        dark_bar.putpixel((2, y), (0, 0, 0))
    assert image_to_grid(dark_bar, 1, mode="glyph", invert=True).to_text() == "|"


def test_glyph_color_is_the_ink_color():
    im = Image.new("RGB", (MASK_W, MASK_H))
    for y in range(MASK_H):
        for x in (0, 1):
            im.putpixel((x, y), (255, 128, 0))
    # The black pixels carry no ink, so they don't darken the glyph's color.
    assert image_to_grid(im, 1, mode="glyph").cells == [Cell("L", (255, 128, 0))]
    assert image_to_grid(im, 1, mode="glyph", color=False).cells == [Cell("L")]
    tinted = image_to_grid(im, 1, mode="glyph", tint=(0, 200, 100))
    assert tinted.cells == [Cell("L", (0, 114, 57))]  # tint scaled by luminance 0.57


def test_transparent_cells_are_blank():
    im = Image.new("RGBA", (2 * MASK_W, MASK_H), (255, 255, 255, 0))  # transparent *white*
    for y in range(MASK_H):
        im.putpixel((2, y), (255, 255, 255, 255))
    grid = image_to_grid(im, 2, mode="glyph")
    assert grid.cells == [Cell("|", WHITE), BLANK]


def test_fill_gives_every_inked_cell_a_glyph():
    # Left cell 30% ink (top three rows), right cell 10% (top row).
    im = cells(2, 1, lambda x, y: y < 3 if x < MASK_W else y < 1)
    assert glyph(im, charset="01") == "  "  # shape matching: neither looks like 0 or 1
    assert glyph(im, charset="01", fill=True, threshold=0.25) == "1 "
    assert glyph(im, charset="01", fill=True, threshold=0.05) == "11"
    assert glyph(im, charset="01", fill=True, threshold=0.5) == "  "


def test_outline_keeps_only_the_rim():
    square = cells(5, 4, lambda x, y: 2 <= x <= 22 and 5 <= y <= 34)
    assert glyph(square) == ",ggg,\n]@@@[\n]@@@[\n^\"\"\"^"
    assert glyph(square, outline=True) == ",---,\n|   |\n|   |\n'---'"
    assert glyph(square, outline=True, threshold=1.0) == ",---,\n|   |\n|   |\n'---'"  # white is >= 1.0
    red = Image.new("RGB", (6, 6), (255, 0, 0))  # luminance 0.21
    half = image_to_grid(red, 6, mode="half", outline=True, threshold=0.2)
    assert half.to_text() == "█▀▀▀▀█\n█    █\n█▄▄▄▄█"  # the inside is transparent, even in color
    assert half.get(1, 1) == BLANK and half.get(0, 1) == Cell("█", (255, 0, 0))
    dots = image_to_grid(Image.new("RGB", (4, 8), WHITE), 2, mode="braille", outline=True)
    assert dots.to_text() == "⡏⢹\n⣇⣸"


def test_charset_validation():
    im = cells(1, 1, lambda x, y: True)
    with pytest.raises(ValueError, match="glyph mask"):
        image_to_grid(im, 1, mode="glyph", charset="ab█")
    with pytest.raises(ValueError, match="non-space"):
        image_to_grid(im, 1, mode="glyph", charset=" ")
    assert glyph(im, charset="..@@ ") == "@"  # duplicates and spaces are fine


def test_glyph_mode_on_a_real_size_image_is_tight():
    im = Image.new("RGB", (100, 100), WHITE)
    grid = image_to_grid(im, 40, 20, mode="glyph")
    assert (grid.width, grid.height) == (40, 20)
    assert set(grid.to_text()) == {"@", "\n"}


# -- styles ------------------------------------------------------------------------

LOOK_KEYS = {"mode", "charset", "ramp", "threshold", "fill", "outline", "dither"}


def blob() -> Image.Image:
    """Dark ink on white: a ring with a bar through it, like a small logo."""
    im = Image.new("RGB", (120, 120), WHITE)
    for y in range(120):
        for x in range(120):
            r2 = (x - 60) ** 2 + (y - 60) ** 2
            if 1600 <= r2 <= 2500 or (abs(x - y) < 6 and r2 < 1600):
                im.putpixel((x, y), (0, 0, 0))
    return im


def test_required_styles_exist_and_only_set_look_keys():
    for name in ("dots", "blocks", "symbols", "letters", "numbers", "binary", "ascii", "classic", "shade"):
        assert name in STYLES
    for name, kw in STYLES.items():
        assert set(kw) <= LOOK_KEYS, name
        assert "mode" in kw, name
    assert STYLES["classic"]["ramp"] == DEFAULT_RAMP
    assert STYLES["dots"]["mode"] == "braille"


@pytest.mark.parametrize("style", list(STYLES))
def test_every_style_renders_at_the_right_size(style):
    look = {"invert": True, "transparent_bg": True, "autocrop": True, "despeckle": True, "color": False}
    grid = image_to_grid(blob(), 40, 20, style=style, **look)
    assert (grid.width, grid.height) == (40, 20)  # the ring is round: 40 wide, 20 tall
    drawn = {c.ch for c in grid.cells} - {" "}
    assert drawn
    kw = STYLES[style]
    if kw["mode"] == "glyph" and "charset" in kw:
        assert drawn <= set(kw["charset"])
    assert BLANK in grid.cells  # transparent outside the ring


def test_style_overrides_its_keys_and_keeps_the_rest():
    im = cells(1, 1, lambda x, y: x == 2)
    assert image_to_grid(im, 1, mode="braille", style="numbers").to_text() == "1"
    assert image_to_grid(im, 1, charset="xyz", style="symbols").to_text() == "|"
    # Keys the style doesn't set stay the caller's.
    assert image_to_grid(im, 1, style="numbers", invert=True).to_text() != "1"
    src = ImageSource(im, mode="half", style="letters")
    assert (src.mode, src.charset) == ("glyph", LETTERS)
    assert src.frame(0.0, 3, 1).to_text(rstrip=False) == " l "


def test_unknown_style_raises_with_the_valid_names():
    with pytest.raises(ValueError, match="unknown style 'sixel'") as err:
        image_to_grid(cells(1, 1, lambda x, y: True), 1, style="sixel")
    assert all(name in str(err.value) for name in STYLES)
    with pytest.raises(ValueError):
        ImageSource(cells(1, 1, lambda x, y: True), style="nope")


# -- imports -----------------------------------------------------------------------


def test_styles_and_masks_import_without_numpy_or_pillow():
    code = (
        "import sys, asciifx.sources.styles as s, asciifx.sources.glyph_masks as g;"
        "assert s.STYLES and g.MASKS;"
        "assert 'numpy' not in sys.modules and 'PIL' not in sys.modules, 'eager import'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
