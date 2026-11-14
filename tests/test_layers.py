from asciifx.core import Cell, Grid, Layer, blend, compose, composite

RED, BLUE, GRAY = (255, 0, 0), (0, 0, 255), (40, 40, 40)


def test_blend_space_without_bg_is_transparent():
    bottom = Cell("b", RED, GRAY)
    assert blend(bottom, Cell(" ", BLUE)) == bottom


def test_blend_glyph_keeps_bottom_bg():
    assert blend(Cell("b", RED, GRAY), Cell("t", BLUE)) == Cell("t", BLUE, GRAY)


def test_blend_bg_makes_top_opaque():
    top = Cell(" ", None, BLUE)
    assert blend(Cell("b", RED, GRAY), top) == top


def test_composite_offset_and_clip():
    dst = Grid.filled(4, 3, Cell("."))
    sprite = Grid.from_text("ab\ncd")
    composite(dst, Layer(sprite, x=3, y=2))  # only "a" lands inside
    composite(dst, Layer(sprite, x=-1, y=-1))  # only "d" lands inside
    assert dst.to_text() == "d...\n....\n...a"


def test_composite_transparent_holes():
    dst = Grid.filled(3, 1, Cell("."))
    composite(dst, Layer(Grid.from_text("a c")))
    assert dst.to_text() == "a.c"


def test_opaque_layer_copies_spaces():
    dst = Grid.filled(3, 1, Cell("."))
    composite(dst, Layer(Grid.from_text("a c"), opaque=True))
    assert dst.to_text(rstrip=False) == "a c"


def test_invisible_layer_and_fully_offscreen_layer_do_nothing():
    dst = Grid.filled(2, 2, Cell("."))
    composite(dst, Layer(Grid.from_text("xx"), visible=False))
    composite(dst, Layer(Grid.from_text("xx"), x=5))
    assert dst.to_text() == "..\n.."


def test_compose_is_bottom_to_top():
    g = compose(3, 1, [Layer(Grid.from_text("aaa")), Layer(Grid.from_text("b"), x=1)], Cell("."))
    assert g.to_text() == "aba"
