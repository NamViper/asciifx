"""Startup banner pieces: sysinfo, text block, layout, logo cache (no effects yet)."""

import json
import os

import pytest

from asciifx.core import Cell, Grid
from asciifx.startup import (
    BLOOD, GRANTED, MARGIN, _os_name, arrange, block_size, build, choose_arrangement,
    format_uptime, load_logo, recolor, sysinfo, text_lines,
)

INFO = [("user", "neo@nebuchadnezzar"), ("os", "Ubuntu 26.04.1 LTS"), ("kernel", "6.18.0"),
        ("uptime", "3h 12m"), ("date", "Thu 12 Nov 2026 10:45")]


@pytest.mark.parametrize("secs,text", [(59, "0m"), (61, "1m"), (3 * 3600 + 12 * 60, "3h 12m"),
                                       (2 * 86400 + 3600 + 5 * 60, "2d 1h 5m")])
def test_format_uptime(secs, text):
    assert format_uptime(secs) == text


def test_os_name_from_os_release(tmp_path):
    f = tmp_path / "os-release"
    f.write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 26.04.1 LTS"\n')
    assert _os_name(f) == "Ubuntu 26.04.1 LTS"


def test_sysinfo_labels():
    assert [label for label, _ in sysinfo()] == ["user", "os", "kernel", "uptime", "date"]


def test_text_block_content_and_colors():
    lines = text_lines(INFO, BLOOD)
    block = Grid(*block_size(lines))
    for line in lines:
        block.put_text(0, line.row, "")  # rows exist
        for x, c in enumerate(line.grid.cells):
            block.set(x, line.row, c)
    assert block.to_text().splitlines() == [
        "> establishing uplink ........ [ OK ]",
        "> bypassing firewall ......... [ OK ]",
        "> decrypting payload ......... [ OK ]",
        GRANTED,
        "",
        "user    neo@nebuchadnezzar",
        "os      Ubuntu 26.04.1 LTS",
        "kernel  6.18.0",
        "uptime  3h 12m",
        "date    Thu 12 Nov 2026 10:45",
    ]
    assert block.get(0, 0).fg == BLOOD.accent
    assert block.get(33, 0) == Cell("O", BLOOD.ok)
    assert block.get(0, 3).fg == BLOOD.accent


def test_long_values_are_truncated():
    lines = text_lines([("kernel", "x" * 100)], BLOOD, max_width=30)
    assert lines[-1].grid.width == 30
    assert lines[-1].grid.to_text().endswith("…")


def test_arrangements():
    assert choose_arrangement(120, 40, 37, 10) == ("side", (40, 20))
    assert choose_arrangement(60, 40, 37, 10) == ("stacked", (40, 20))
    assert choose_arrangement(66, 20, 37, 10) == ("side", (22, 16))  # no room to stack
    assert choose_arrangement(45, 14, 37, 10) == ("text", None)
    assert choose_arrangement(30, 40, 37, 10) is None  # text doesn't fit
    assert choose_arrangement(120, 12, 37, 10) is None


def test_arrange_side_by_side_centers_vertically():
    layout = arrange("side", Grid(30, 16), 37, 10)
    assert layout.logo_xy == (MARGIN, 0)
    assert layout.text_xy == (MARGIN + 30 + 4, 3)
    assert (layout.width, layout.height) == (MARGIN + 30 + 4 + 37, 17)


def test_logo_cache_hits_and_invalidates(tmp_path):
    img = tmp_path / "logo.png"
    img.write_bytes(b"fake")
    calls = []

    def convert(path, w, h, params):
        calls.append((w, h))
        return Grid.from_text("#" * w)

    a = load_logo(img, 4, 2, {"mode": "braille"}, cache=tmp_path / "c", convert=convert)
    b = load_logo(img, 4, 2, {"mode": "braille"}, cache=tmp_path / "c", convert=convert)
    assert a == b and calls == [(4, 2)]
    load_logo(img, 5, 2, {"mode": "braille"}, cache=tmp_path / "c", convert=convert)  # new size
    os.utime(img, ns=(1, 1))  # file changed
    load_logo(img, 4, 2, {"mode": "braille"}, cache=tmp_path / "c", convert=convert)
    assert calls == [(4, 2), (5, 2), (4, 2)]
    for f in (tmp_path / "c").iterdir():
        json.loads(f.read_text())


def test_corrupt_cache_is_regenerated(tmp_path):
    img = tmp_path / "logo.png"
    img.write_bytes(b"fake")
    convert = lambda p, w, h, params: Grid.from_text("ok")  # noqa: E731
    load_logo(img, 2, 1, {}, cache=tmp_path, convert=convert)
    (cached,) = tmp_path.glob("logo-*.json")
    cached.write_text("{not json")
    assert load_logo(img, 2, 1, {}, cache=tmp_path, convert=convert).to_text() == "ok"


def test_recolor_gradient_keeps_blanks():
    g = recolor(Grid.from_text("a b\nc\nd"), (200, 0, 100), (0, 100, 100))
    assert g.get(0, 0) == Cell("a", (200, 0, 100))
    assert g.get(1, 0) == Cell(" ")
    assert g.get(0, 1) == Cell("c", (100, 50, 100))
    assert g.get(0, 2) == Cell("d", (0, 100, 100))


def test_build_without_logo_file_falls_back_to_text(tmp_path):
    banner = build(120, 40, tmp_path / "missing.png", info=INFO)
    assert banner is not None and banner.logo is None
    assert banner.final().to_text().splitlines()[0] == "  > establishing uplink ........ [ OK ]"


def _banner_with_logo(duration=3.5):
    from asciifx.startup import StartupBanner, arrange

    logo = recolor(Grid.from_text("⣿⣿⣿⣿\n⣿⠀⠀⣿\n⣿⣿⣿⣿"), BLOOD.logo, BLOOD.logo_bottom)
    lines = text_lines(INFO, BLOOD)
    layout = arrange("side", logo, *block_size(lines))
    return StartupBanner(layout, logo, lines, BLOOD, duration)


def test_banner_frames_at_fixed_t():
    b = _banner_with_logo()
    final = b.final()
    text = final.to_text()
    assert "ACCESS GRANTED" in text and "⣿⣿⣿⣿" in text
    assert b.frame(3.5) == final and b.frame(99) == final
    assert b.frame(3.3) == final  # every effect has finished by 3.3 s
    assert b.frame(0.0) != final
    # before the boot log starts typing, no text is visible yet
    assert "establishing" not in b.frame(1.0).to_text()
    # halfway through the boot log: first line done, ACCESS GRANTED not yet shown
    mid = b.frame(1.9).to_text()
    assert "establishing uplink" in mid and "ACCESS GRANTED" not in mid
    # the info block is still typing at 2.6 s but the boot log is complete
    late = b.frame(2.6).to_text()
    assert "decrypting payload ......... [ OK ]" in late and "today" not in late


def test_banner_timeline_scales_with_duration():
    fast = _banner_with_logo(duration=1.0)
    assert fast.frame(1.0 * 3.3 / 3.5) == fast.final()
    assert fast.frame(0.2) != fast.final()


def test_banner_frames_have_constant_size():
    b = _banner_with_logo()
    sizes = {(g.width, g.height) for g in (b.frame(t / 10) for t in range(36))}
    assert sizes == {(b.layout.width, b.layout.height)}


def test_build_passes_style_and_scramble_charset(tmp_path, monkeypatch):
    from asciifx.sources.styles import STYLES, scramble_charset

    img = tmp_path / "logo.png"
    img.write_bytes(b"fake")
    seen = []

    def fake_load_logo(path, w, h, params, cache=None):
        seen.append(params)
        return Grid.from_text("#" * 10)

    monkeypatch.setattr("asciifx.startup.load_logo", fake_load_logo)
    banner = build(120, 40, img, info=INFO, style="letters")
    assert seen[0]["style"] == "letters" and seen[0]["invert"] is True
    assert banner.logo_charset == STYLES["letters"]["charset"]
    assert scramble_charset("dots") is None and scramble_charset("binary") == "01"


def test_glyph_styles_get_a_bigger_logo_box():
    from asciifx.sources.styles import preferred_size

    assert preferred_size("dots") == (40, 20) and preferred_size("outline") == (40, 20)
    assert preferred_size("letters") == (64, 32) and preferred_size("sketch") == (64, 32)
    assert choose_arrangement(140, 45, 37, 10, preferred_size("letters")) == ("side", (64, 32))
    assert choose_arrangement(140, 45, 37, 10, preferred_size("dots")) == ("side", (40, 20))


def test_parse_logo_size():
    from asciifx.sources.styles import parse_logo_size

    assert parse_logo_size("small") == (40, 20) and parse_logo_size("LARGE") == (64, 32)
    assert parse_logo_size("50x25") == (50, 25)
    for bad in ("big", "64", "0x10", "64x-1", "axb", "2000x10"):
        with pytest.raises(ValueError):
            parse_logo_size(bad)


def test_build_size_overrides_the_style_box(tmp_path, monkeypatch):
    img = tmp_path / "logo.png"
    img.write_bytes(b"fake")
    boxes = []

    def fake_load_logo(path, w, h, params, cache=None):
        boxes.append((w, h))
        return Grid.from_text("#" * 10)

    monkeypatch.setattr("asciifx.startup.load_logo", fake_load_logo)
    build(140, 45, img, info=INFO, style="dots")
    build(140, 45, img, info=INFO, style="dots", size=(64, 32))
    build(140, 45, img, info=INFO, style="sketch", size=(40, 20))
    assert boxes == [(40, 20), (64, 32), (40, 20)]
