import pytest

from asciifx.core import BLANK, Cell, Grid

RED = (255, 0, 0)


def test_new_grid_is_blank():
    g = Grid(3, 2)
    assert g.cells == [BLANK] * 6
    assert g.to_text(rstrip=False) == "   \n   "


@pytest.mark.parametrize("w,h", [(-1, 2), (2, -1)])
def test_negative_size_rejected(w, h):
    with pytest.raises(ValueError):
        Grid(w, h)


def test_cell_count_must_match():
    with pytest.raises(ValueError):
        Grid(2, 2, [BLANK] * 3)


def test_set_get_and_clipping():
    g = Grid(3, 2)
    g.set(2, 1, Cell("x", RED))
    g.set(3, 0, Cell("!"))  # out of bounds: ignored
    g.set(-1, 0, Cell("!"))
    assert g.get(2, 1) == Cell("x", RED)
    assert g.to_text() == "\n  x"
    with pytest.raises(IndexError):
        g.get(3, 0)


def test_put_text_multiline_clipped_on_all_sides():
    g = Grid(4, 3)
    g.put_text(-1, -1, "abcde\nfghij\nklmno\npqrst", fg=RED)
    assert g.to_text() == "ghij\nlmno\nqrst"
    assert g.get(0, 0) == Cell("g", RED)


def test_put_text_replaces_control_chars():
    g = Grid(4, 1)
    g.put_text(0, 0, "a\x1bb\t")
    assert g.to_text(rstrip=False) == "a b "


def test_from_text_pads_short_lines_with_blank():
    g = Grid.from_text("ab\nc", fg=RED, bg=(0, 0, 0))
    assert (g.width, g.height) == (2, 2)
    assert g.get(1, 1) == BLANK
    assert g.get(0, 1) == Cell("c", RED, (0, 0, 0))


def test_copy_is_independent():
    g = Grid(2, 1)
    c = g.copy()
    c.set(0, 0, Cell("x"))
    assert g.get(0, 0) == BLANK
    assert c != g


def test_rows_and_fill():
    g = Grid.filled(2, 2, Cell("#"))
    assert [len(r) for r in g.rows()] == [2, 2]
    g.fill(Cell("."))
    assert g.to_text() == "..\n.."


def test_dict_round_trip_is_json_safe():
    import json

    g = Grid(3, 2)
    g.put_text(0, 0, "a▀", fg=RED, bg=(0, 0, 1))
    g.set(2, 1, Cell("x", None, RED))
    data = json.loads(json.dumps(g.to_dict()))
    assert Grid.from_dict(data) == g
    assert len(data["palette"]) == 4  # blank, "a", "▀", "x"
