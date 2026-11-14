"""Named looks for image conversion: ``image_to_grid(..., style="letters")``.

``STYLES`` maps a style name to the keyword arguments of
:func:`asciifx.sources.image.image_to_grid` that make the look: ``mode`` plus
``charset`` / ``ramp`` / ``threshold`` / ``fill`` / ``outline`` as needed. Those
keys override the caller's; the rest stay the caller's. A style never sets what
is drawn or in which colors (``invert``, ``transparent_bg``, ``autocrop``,
``despeckle``, ``color``, ``tint``).

Stdlib only and plain data, so the CLI can list the choices without importing
numpy or Pillow.
"""

from __future__ import annotations

from typing import Any

CONVERTER_VERSION = 2
"""Bump whenever image conversion output changes (modes, masks, matching), so
on-disk logo caches keyed on it (startup.load_logo) are regenerated."""

PRINTABLE = "".join(map(chr, range(0x21, 0x7F)))
"""Every printable ASCII character except space (space is always a candidate)."""
PUNCTUATION = "".join(c for c in map(chr, range(0x21, 0x7F)) if not c.isalnum())
"""Every printable ASCII character that is neither a letter nor a digit."""
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
DIGITS = "0123456789"

STYLES: dict[str, dict[str, Any]] = {
    # Braille, 2x4 dots per cell: the most detail per cell.
    "dots": {"mode": "braille"},
    # Half blocks: two chunky pixels per cell.
    "blocks": {"mode": "half"},
    # Shape matching (mode="glyph"): each cell becomes the glyph whose shape and
    # density best match it, so edges come out as | / \ _ and solid areas dense.
    "symbols": {"mode": "glyph", "charset": PUNCTUATION},
    "letters": {"mode": "glyph", "charset": LETTERS},
    "numbers": {"mode": "glyph", "charset": DIGITS},
    "ascii": {"mode": "glyph", "charset": PRINTABLE},
    # Every cell with at least 25% ink becomes 0 or 1 (whichever fits its shape
    # better), so the silhouette stays solid. Pure shape matching over "01"
    # leaves too many holes to read.
    "binary": {"mode": "glyph", "charset": "01", "fill": True, "threshold": 0.25},
    # Line art: only the rim of the ink is drawn. "sketch" traces it with ASCII
    # (/ \ | _ - ...), like hand-made ASCII art; "outline" with braille dots.
    "sketch": {"mode": "glyph", "charset": PRINTABLE, "outline": True},
    "outline": {"mode": "braille", "outline": True},
    # Brightness ramps, one sample per cell.
    "classic": {"mode": "ramp", "ramp": " .:-=+*#%@"},
    "shade": {"mode": "ramp", "ramp": " ░▒▓█"},
}


def scramble_charset(style: str | None) -> str | None:
    """Glyphs that fit a style, for decode/morph scrambles; None = effect default."""
    look = STYLES.get(style or "", {})
    if look.get("mode") == "glyph":
        return look.get("charset") or PRINTABLE
    if look.get("mode") == "ramp":
        return look.get("ramp", "").strip() or None
    if look.get("mode") == "half":
        return "▀▄█▌▐"
    return None


def preferred_size(style: str | None) -> tuple[int, int]:
    """Max (columns, rows) a logo should get in this style.

    Braille and half blocks pack 8 or 2 pixels per cell and look good small;
    character-shape styles (letters, numbers, symbols, ...) need roughly 1.6x
    more cells before the shapes read as an image instead of texture.
    """
    mode = STYLES.get(style or "", {}).get("mode", "braille")
    return (64, 32) if mode in ("glyph", "ramp") else (40, 20)
