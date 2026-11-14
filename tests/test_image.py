"""Image -> grid conversion (ramp / half / braille), fitting, transparency, ImageSource.

Glyph mode, outline and styles are in test_image_glyph.py.
"""

import subprocess
import sys

import pytest
from PIL import Image

from asciifx.core import BLANK, Cell, Grid
from asciifx.sources import ImageSource, image_to_grid
from asciifx.sources import image as image_mod
from asciifx.sources.image import DEFAULT_RAMP

BLACK, WHITE = (0, 0, 0), (255, 255, 255)
RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)
CLEAR = (0, 0, 0, 0)


def make(w: int, h: int, pixels: dict[tuple[int, int], tuple[int, ...]], fill=BLACK) -> Image.Image:
    """An RGBA image filled with ``fill`` and the given pixels set."""
    im = Image.new("RGBA", (w, h), (*fill, 255) if len(fill) == 3 else fill)
    for xy, c in pixels.items():
        im.putpixel(xy, (*c, 255) if len(c) == 3 else c)
    return im


# -- braille ---------------------------------------------------------------------


def braille_pattern() -> Image.Image:
    """4x8 px = 2x2 braille cells: dot 1 / right column / bottom row / all dots."""
    lit = {(0, 0)}  # cell (0,0): top-left dot only
    lit |= {(3, y) for y in range(4)}  # cell (1,0): whole right column
    lit |= {(0, 7), (1, 7)}  # cell (0,1): bottom row
    lit |= {(x, y) for x in (2, 3) for y in range(4, 8)}  # cell (1,1): all eight dots
    return make(4, 8, {xy: WHITE for xy in lit})


def test_braille_exact_glyphs_and_bit_order():
    grid = image_to_grid(braille_pattern(), 2, mode="braille")
    assert (grid.width, grid.height) == (2, 2)
    assert grid.to_text() == "⠁⢸\n⣀⣿"
    assert [c.fg for c in grid.cells] == [WHITE] * 4
    assert all(c.bg is None for c in grid.cells)


def test_braille_without_color_and_inverted():
    mono = image_to_grid(braille_pattern(), 2, mode="braille", color=False)
    assert all(c.fg is None for c in mono.cells)
    # Inverted, the unlit dots light up: complement of each cell's bits.
    inv = image_to_grid(braille_pattern(), 2, mode="braille", invert=True, color=False)
    assert inv.to_text() == f"{chr(0x28FE)}{chr(0x2847)}\n{chr(0x283F)}"
    assert inv.get(1, 1) == BLANK  # no dots -> transparent cell, not U+2800


def test_braille_dots_use_threshold_and_mean_color_of_lit_dots():
    im = make(2, 4, {(0, 0): (200, 200, 200), (1, 0): (100, 100, 100), (0, 1): (255, 0, 255)})
    grid = image_to_grid(im, 1, mode="braille", threshold=0.5)
    # (255,0,255) has Rec.709 luma ~0.28 -> below threshold, like the 100-gray.
    assert grid.cells == [Cell(chr(0x2801), (200, 200, 200))]
    low = image_to_grid(im, 1, mode="braille", threshold=0.25)
    assert low.cells == [Cell(chr(0x2800 | 0x01 | 0x02 | 0x08), (185, 100, 185))]


def test_braille_dither_mixes_dots_for_mid_gray():
    im = Image.new("RGB", (16, 32), (128, 128, 128))
    plain = image_to_grid(im, 8, mode="braille", threshold=0.55)
    dithered = image_to_grid(im, 8, mode="braille", threshold=0.55, dither=True)
    assert set(plain.cells) == {BLANK}  # every pixel is below the threshold
    dots = sum(bin(ord(c.ch) - 0x2800).count("1") for c in dithered.cells if c.ch != " ")
    assert 0.3 * 512 < dots < 0.6 * 512


# -- half blocks -----------------------------------------------------------------


def test_half_exact_glyphs_and_colors():
    im = make(
        3, 4,
        {
            (0, 0): RED, (0, 1): RED, (0, 2): BLUE, (0, 3): CLEAR,
            (1, 0): GREEN, (1, 1): BLACK, (1, 2): CLEAR, (1, 3): YELLOW,
            (2, 0): CLEAR, (2, 1): CLEAR, (2, 2): CLEAR, (2, 3): CLEAR,
        },
    )  # fmt: skip
    grid = image_to_grid(im, 3, mode="half")
    assert (grid.width, grid.height) == (3, 2)
    assert grid.cells == [
        Cell("█", RED), Cell("▀", GREEN, BLACK), BLANK,  # same colors -> full block, fg only
        Cell("▀", BLUE), Cell("▄", YELLOW), BLANK,  # one transparent half -> fg only
    ]


def test_half_monochrome_thresholds_pixels():
    im = make(4, 2, {(0, 0): WHITE, (0, 1): WHITE, (1, 0): WHITE, (2, 1): WHITE})
    grid = image_to_grid(im, 4, mode="half", color=False)
    assert grid.cells == [Cell("█"), Cell("▀"), Cell("▄"), BLANK]
    tinted = image_to_grid(im, 4, mode="half", tint=GREEN, tint_shade=False)
    assert tinted.cells == [Cell("█", GREEN), Cell("▀", GREEN), Cell("▄", GREEN), BLANK]


def test_half_shaded_tint_scales_by_luminance():
    im = make(1, 2, {(0, 0): WHITE, (0, 1): (128, 128, 128)})
    grid = image_to_grid(im, 1, mode="half", tint=(0, 200, 100))
    assert grid.cells == [Cell("▀", (0, 200, 100), (0, 100, 50))]


# -- ramp ------------------------------------------------------------------------


def gradient() -> Image.Image:
    """10x1 px, gray levels at the center of each of the 10 ramp buckets."""
    im = Image.new("RGB", (10, 1))
    for i in range(10):
        v = round((i + 0.5) * 25.5)
        im.putpixel((i, 0), (v, v, v))
    return im


def test_ramp_gradient_maps_to_ramp_in_order():
    grid = image_to_grid(gradient(), 10, mode="ramp")
    assert (grid.width, grid.height) == (10, 1)
    assert grid.to_text() == DEFAULT_RAMP
    assert grid.get(0, 0) == BLANK  # darkest -> space -> transparent
    assert grid.get(9, 0) == Cell("@", (242, 242, 242))


def test_ramp_invert_reverses_and_custom_ramp():
    inverted = image_to_grid(gradient(), 10, mode="ramp", invert=True)
    assert inverted.to_text() == DEFAULT_RAMP[::-1].rstrip()
    custom = image_to_grid(gradient(), 10, mode="ramp", ramp=" ab", color=False)
    assert custom.to_text() == "   aaaabbb"


def test_ramp_tint():
    flat = image_to_grid(gradient(), 10, mode="ramp", tint=RED, tint_shade=False)
    assert {c.fg for c in flat.cells if c.ch != " "} == {RED}
    shaded = image_to_grid(gradient(), 10, mode="ramp", tint=(200, 100, 0))
    assert shaded.get(9, 0).fg == (190, 95, 0)  # 242/255 of the tint


# -- fitting ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "size,width,height,mode,expected",
    [
        ((100, 100), 40, None, "ramp", (40, 20)),  # cells are 2:1, so half as many rows
        ((100, 100), 40, None, "half", (40, 20)),  # 40x40 px, 2 px per cell vertically
        ((100, 100), 40, None, "braille", (40, 20)),  # 80x80 dots, 2x4 per cell
        ((200, 50), 40, None, "ramp", (40, 5)),
        ((200, 50), 40, None, "half", (40, 5)),
        ((200, 50), 40, None, "braille", (40, 5)),
        ((50, 200), 40, 20, "ramp", (10, 20)),  # height-limited: 10 wide x 40 tall (2:1 cells)
        ((50, 200), 40, 20, "half", (10, 20)),
        ((50, 200), 40, 20, "braille", (10, 20)),
        ((50, 200), 40, None, "braille", (40, 80)),  # height=None: width-limited only
        ((100, 100), 40, None, "glyph", (40, 20)),  # 200x200 px, 5x10 per cell
        ((200, 50), 40, None, "glyph", (40, 5)),
        ((50, 200), 40, 20, "glyph", (10, 20)),
        ((3, 3), 40, 20, "half", (40, 20)),  # small images are scaled up
    ],
)
def test_fit_preserves_aspect_ratio(size, width, height, mode, expected):
    im = Image.new("RGB", size, WHITE)
    grid = image_to_grid(im, width, height, mode=mode)
    assert (grid.width, grid.height) == expected


@pytest.mark.parametrize("mode", ["ramp", "half", "braille", "glyph"])
@pytest.mark.parametrize("size", [(1, 1), (7, 3), (3, 7), (640, 480), (17, 999)])
def test_fit_never_exceeds_box(mode, size):
    grid = image_to_grid(Image.new("RGB", size, WHITE), 23, 9, mode=mode)
    assert 1 <= grid.width <= 23 and 1 <= grid.height <= 9
    assert grid.width == 23 or grid.height == 9  # touches the box on one side


def test_empty_box_gives_empty_grid():
    assert image_to_grid(Image.new("RGB", (4, 4)), 0) == Grid(0, 0)
    assert image_to_grid(Image.new("RGB", (4, 4)), 5, 0) == Grid(0, 0)


def test_loads_from_path(tmp_path):
    path = tmp_path / "pattern.png"
    braille_pattern().save(path)
    assert image_to_grid(path, 2, mode="braille").to_text() == "⠁⢸\n⣀⣿"
    assert image_to_grid(str(path), 2, mode="braille").to_text() == "⠁⢸\n⣀⣿"


def test_invalid_options():
    with pytest.raises(ValueError):
        image_to_grid(Image.new("RGB", (4, 4)), 4, mode="sixel")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        image_to_grid(Image.new("RGB", (4, 4)), 4, threshold=1.5)


# -- transparency ----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["ramp", "half", "braille", "glyph"])
def test_alpha_becomes_blank(mode):
    im = Image.new("RGBA", (8, 8), (255, 255, 255, 0))  # transparent *white*
    grid = image_to_grid(im, 4, mode=mode)
    assert set(grid.cells) == {BLANK}


def ring_image() -> Image.Image:
    """20x20 white, with a 2px black square ring (4..15) enclosing a white interior."""
    im = Image.new("RGB", (20, 20), WHITE)
    for i in range(4, 16):
        for j in (4, 5, 14, 15):
            im.putpixel((i, j), BLACK)
            im.putpixel((j, i), BLACK)
    return im


def test_transparent_bg_flood_fill_keeps_enclosed_light_region():
    grid = image_to_grid(ring_image(), 20, mode="half", transparent_bg=True)
    assert (grid.width, grid.height) == (20, 10)
    assert grid.get(0, 0) == BLANK and grid.get(19, 9) == BLANK and grid.get(2, 5) == BLANK
    assert grid.get(4, 3) == Cell("█", BLACK)  # ring
    assert grid.get(10, 5) == Cell("█", WHITE)  # enclosed interior stays opaque
    # Without transparent_bg everything is opaque.
    assert image_to_grid(ring_image(), 20, mode="half").get(0, 0) == Cell("█", WHITE)


def test_transparent_bg_uses_tolerance():
    im = ring_image()
    im.putpixel((1, 1), (235, 235, 235))  # within the default tolerance of white
    im.putpixel((2, 1), (150, 150, 150))  # not background
    grid = image_to_grid(im, 20, mode="half", transparent_bg=True)  # 1:1 pixels, no resampling
    assert grid.get(1, 0) == BLANK
    assert grid.get(2, 0) == Cell("▄", (150, 150, 150))
    strict = image_to_grid(im, 20, mode="half", transparent_bg=True, bg_tolerance=0.05)
    assert strict.get(1, 0) == Cell("▄", (235, 235, 235))


def test_transparent_bg_inverted_for_dark_terminals():
    grid = image_to_grid(ring_image(), 20, mode="half", transparent_bg=True, invert=True)
    assert grid.get(0, 0) == BLANK
    assert grid.get(4, 3) == Cell("█", WHITE)  # black ink becomes bright
    assert grid.get(10, 5) == Cell("█", BLACK)


# -- autocrop / despeckle ----------------------------------------------------------


def square_on_white(size: int = 120) -> Image.Image:
    """White canvas with a 20x20 black square at (50, 40), a faint gray frame and a corner speck."""
    im = Image.new("RGB", (size, size), WHITE)
    for x in range(size):
        for y in (2, size - 3):
            im.putpixel((x, y), (235, 235, 235))  # faint frame lines
            im.putpixel((y, x), (235, 235, 235))
    for x in range(50, 70):
        for y in range(40, 60):
            im.putpixel((x, y), BLACK)
    im.putpixel((118, 118), (60, 60, 60))  # crop mark: tiny, dark, detached
    return im


def test_autocrop_ignores_faint_lines_and_specks():
    grid = image_to_grid(square_on_white(), 10, mode="half", autocrop=True)
    assert (grid.width, grid.height) == (10, 5)
    assert set(grid.cells) == {Cell("█", BLACK)}


def test_despeckle_erases_detached_specks():
    kw = {"mode": "ramp", "invert": True, "color": False}
    assert image_to_grid(square_on_white(), 120, **kw).get(118, 59) != BLANK
    clean = image_to_grid(square_on_white(), 120, despeckle=True, **kw)
    assert clean.get(118, 59) == BLANK
    assert clean.get(60, 25) == Cell("@")  # the square itself is kept


def test_autocrop_uses_alpha_when_background_is_transparent():
    im = Image.new("RGBA", (30, 30), (255, 255, 255, 0))
    for x in range(10, 14):
        for y in range(5, 25):
            im.putpixel((x, y), (255, 255, 255, 255))
    grid = image_to_grid(im, 4, mode="half", autocrop=True)
    assert (grid.width, grid.height) == (4, 10)
    assert set(grid.cells) == {Cell("█", WHITE)}


# -- ImageSource ------------------------------------------------------------------


def test_image_source_centers_static_frame():
    src = ImageSource(braille_pattern(), mode="braille", color=False)
    wide = src.frame(0.0, 6, 2)  # art is 2x2 cells: centered horizontally
    assert (wide.width, wide.height) == (6, 2)
    assert wide.to_text(rstrip=False) == "  ⠁⢸  \n  ⣀⣿  "
    tall = src.frame(0.0, 2, 4)  # centered vertically
    assert tall.to_text(rstrip=False) == "  \n⠁⢸\n⣀⣿\n  "
    assert src.frame(12.5, 6, 2) == wide  # static over t
    scaled = src.frame(0.0, 7, 4)  # scaled up 2x to 4x4 cells, then centered in 7 columns
    assert [x for x in range(7) if scaled.get(x, 3) != BLANK] == [1, 2, 3, 4]


def test_image_source_caches_per_size(monkeypatch):
    prepares, renders = [], []
    real_prepare, real_render = image_mod._prepare, ImageSource._render
    monkeypatch.setattr(image_mod, "_prepare", lambda *a: prepares.append(a) or real_prepare(*a))
    monkeypatch.setattr(
        ImageSource, "_render", lambda self, *a: renders.append(a) or real_render(self, *a)
    )

    src = ImageSource(ring_image(), transparent_bg=True)
    first = src.frame(0.0, 30, 12)
    first.set(0, 0, Cell("X"))  # callers may mutate frames without corrupting the cache
    assert src.frame(1.0, 30, 12) != first
    src.frame(2.0, 30, 12)
    src.frame(0.0, 50, 10)
    assert len(prepares) == 1  # decoded/flood-filled once
    assert len(renders) == 2  # once per size


def test_image_source_tiny_terminal():
    assert ImageSource(ring_image()).frame(0.0, 0, 0) == Grid(0, 0)


# -- lazy import -----------------------------------------------------------------


def test_import_does_not_load_numpy_or_pillow():
    code = (
        "import sys, asciifx, asciifx.sources;"
        "assert 'numpy' not in sys.modules and 'PIL' not in sys.modules, 'eager import';"
        "from asciifx.sources import image_to_grid;"
        "assert 'numpy' in sys.modules and 'PIL' in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
