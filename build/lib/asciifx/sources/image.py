"""Image -> ASCII/Unicode art: luminance ramp, half blocks, braille, glyph shape matching.

This is the only module that needs numpy and Pillow. ``asciifx.sources`` exports
it lazily, so ``import asciifx`` never imports them.

Brightness convention (made for dark terminals): a *bright* pixel is "ink". In
ramp mode it gets the densest glyph, in braille mode its dot is set, in
monochrome half-block mode its half is filled, and in glyph mode it is what the
glyph shapes are matched against. For dark-ink-on-white logos, pass
``invert=True``.

Named looks (``style="letters"`` etc.) are listed in :mod:`.styles`.
"""

from __future__ import annotations

import functools
import math
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from PIL import Image, ImageOps

from ..core.grid import BLANK, CELL_ASPECT, Cell, Color, Grid
from .glyph_masks import LEVELS, MASK_H, MASK_W, MASKS
from .styles import PRINTABLE, STYLES

type Mode = Literal["ramp", "half", "braille", "glyph"]
type ImageInput = str | os.PathLike[str] | Image.Image

DEFAULT_CHARSET = PRINTABLE
"""Glyph mode's default: every printable ASCII character (space is always a candidate)."""

DEFAULT_RAMP = " .:-=+*#%@"
"""Dark -> light. On a dark terminal the brightest pixels get the densest glyph."""

# Source pixels sampled per cell, per mode (public: video.py sizes its ffmpeg output with it).
SAMPLES: dict[str, tuple[int, int]] = {
    "ramp": (1, 1), "half": (1, 2), "braille": (2, 4), "glyph": (MASK_W, MASK_H),
}  # fmt: skip
"""Image pixels per cell for each mode: (horizontal, vertical)."""

# Bit of the braille dot at (row, col) of a cell's 4x2 dot matrix: dots 1-3 run down
# the left column, 4-6 down the right, and 7/8 are the bottom row (U+2800 + bits).
_BRAILLE_BITS = np.array([[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]], np.int32)
_BRAILLE_BASE = 0x2800

type _Colors = np.ndarray | None  # per-pixel fg colors (h x w x 3, 0..255), or none

_HALF_GLYPHS = (" ", "▄", "▀", "█")  # index: top_on * 2 + bottom_on

_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)  # Rec. 709; weights sum to 1
_ALPHA_CUTOFF = 0.5  # resampled alpha below this counts as transparent
_INK_CUTOFF = 0.25  # a pixel is ink if a channel differs this much from the background
_MAX_SIDE = 1024  # larger inputs are shrunk first so labeling stays fast
_CACHE_SIZE = 8  # rendered sizes ImageSource keeps (terminal resizes add new ones)


def image_to_grid(
    image: ImageInput,
    width: int,
    height: int | None = None,
    *,
    mode: Mode = "half",
    color: bool = True,
    tint: Color | None = None,
    tint_shade: bool = True,
    invert: bool = False,
    threshold: float = 0.5,
    ramp: str = DEFAULT_RAMP,
    autocrop: bool = False,
    transparent_bg: bool = False,
    bg_tolerance: float = 0.12,
    despeckle: bool = False,
    gamma: float = 1.0,
    dither: bool = False,
    resample: Image.Resampling = Image.Resampling.LANCZOS,
    charset: str = DEFAULT_CHARSET,
    fill: bool = False,
    outline: bool = False,
    style: str | None = None,
) -> Grid:
    """Convert an image into a grid of at most ``width`` x ``height`` cells.

    The image is scaled (up or down) to fit, preserving its aspect ratio with
    terminal cells taken as ``CELL_ASPECT`` times taller than wide. The grid is
    tight around the image, so one side is usually smaller than requested.
    ``height=None`` limits only the width.

    Modes:
      * ``ramp``: one pixel per cell, glyph picked from ``ramp`` (dark -> light)
        by luminance.
      * ``half``: two pixels per cell (top/bottom) drawn with ▀/▄/█. In color,
        fg is the top pixel and bg the bottom one. Monochrome output
        (``color=False`` or a flat tint) fills a half when its luminance is at
        least ``threshold``.
      * ``braille``: 2x4 dots per cell. A dot is set when its luminance is at
        least ``threshold``, or by Floyd-Steinberg error diffusion if ``dither``.
      * ``glyph``: shape matching. Each cell is sampled at MASK_W x MASK_H
        (5x10) and becomes the character of ``charset`` (or a space) whose
        rendered shape and ink density best match it, using ink masks of the
        Cascadia Mono glyphs (see ``glyph_masks``). Edges come out as ``|``,
        ``_``, ``/`` and the like when the charset has them, solid ink as the
        charset's densest glyph. ``charset`` may only use printable ASCII.
        With ``fill``, every cell whose mean ink is at least ``threshold``
        gets a (non-space) glyph and every other cell is blank, which keeps
        silhouettes solid with tiny charsets like ``"01"``.

    Options:
      * ``color``: fg (and bg in half mode) come from the image. If False, fg
        is ``None`` (the terminal default).
      * ``tint``: draw every glyph in this color, overriding ``color``. If
        ``tint_shade`` is True, the tint is scaled by luminance (a one-hue
        image). If it is False, the tint is flat.
      * ``invert``: negate RGB, so luminance becomes 1 - luminance. Use it for
        dark-ink-on-white logos on dark terminals.
      * ``gamma``: applied to RGB in 0..1 after ``invert``. Values above 1 darken.
      * ``transparent_bg``: flood-fill from the image border over pixels within
        ``bg_tolerance`` (max channel difference, 0..1) of the border's
        dominant color and make them transparent. Light areas enclosed by ink
        stay opaque.
      * ``autocrop``: trim to the bounding box of content. Content means opaque
        pixels that clearly differ from the background color (a channel differs
        by more than 0.25), so faint lines don't count. Tiny detached specks
        don't count either.
      * ``despeckle``: erase those tiny detached specks, i.e. ink clusters under
        0.02% of the image area, such as crop marks. They're painted with the
        background color, so ``transparent_bg`` then clears them too.
      * ``outline``: draw only the rim of the ink (pixels at least
        ``threshold`` bright next to darker or transparent ones), at full
        strength; everything else is transparent. A line drawing, best with
        ``glyph`` or ``braille``.
      * ``style``: a named look from ``styles.STYLES`` (``"dots"``,
        ``"letters"``, ``"binary"``, ...). Its keys (``mode``, ``charset``,
        ``ramp``, ``threshold``, ``fill``, ``outline``) win over the arguments
        of the same name. Unknown names raise ``ValueError``.

    Transparent pixels (from the alpha channel or ``transparent_bg``) become
    ``BLANK`` cells, so the art composites over whatever is underneath. In half
    mode a cell with one transparent half draws only the other half's glyph,
    with fg set and bg ``None``. In glyph mode a glyph's fg is the ink-weighted
    mean color of its cell.
    """
    source = ImageSource(
        image, mode=mode, color=color, tint=tint, tint_shade=tint_shade, invert=invert,
        threshold=threshold, ramp=ramp, autocrop=autocrop, transparent_bg=transparent_bg,
        bg_tolerance=bg_tolerance, despeckle=despeckle, gamma=gamma, dither=dither,
        resample=resample, charset=charset, fill=fill, outline=outline, style=style,
    )  # fmt: skip
    return source.fit(width, height)


@dataclass
class ImageSource:
    """A static image as a frame ``Source``, fitted and centered in each frame.

    The decoded and preprocessed image (``despeckle``, ``transparent_bg``,
    ``autocrop``) is computed once. The rendered grid is cached per frame size.
    Options are the same as for :func:`image_to_grid`; a ``style`` overwrites
    its fields when the source is created.
    """

    image: ImageInput
    mode: Mode = "half"
    color: bool = True
    tint: Color | None = None
    tint_shade: bool = True
    invert: bool = False
    threshold: float = 0.5
    ramp: str = DEFAULT_RAMP
    autocrop: bool = False
    transparent_bg: bool = False
    bg_tolerance: float = 0.12
    despeckle: bool = False
    gamma: float = 1.0
    dither: bool = False
    resample: Image.Resampling = Image.Resampling.LANCZOS
    charset: str = DEFAULT_CHARSET
    fill: bool = False
    outline: bool = False
    style: str | None = None
    _prepared: Image.Image | None = field(default=None, init=False, repr=False, compare=False)
    _frames: OrderedDict[tuple[int, int], Grid] = field(
        default_factory=OrderedDict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.style is not None:
            if self.style not in STYLES:
                raise ValueError(f"unknown style {self.style!r}; valid styles: {', '.join(STYLES)}")
            for key, value in STYLES[self.style].items():
                setattr(self, key, value)
        if self.mode not in SAMPLES:
            raise ValueError(f"mode must be one of {sorted(SAMPLES)}, got {self.mode!r}")
        if not self.ramp:
            raise ValueError("ramp must not be empty")
        if self.mode == "glyph":
            _glyph_table(self.charset)  # validates the charset
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be in 0..1, got {self.threshold}")
        if self.gamma <= 0:
            raise ValueError(f"gamma must be positive, got {self.gamma}")

    def frame(self, t: float, width: int, height: int) -> Grid:
        """The image fitted and centered in ``width`` x ``height``. It doesn't change with ``t``."""
        key = (width, height)
        grid = self._frames.get(key)
        if grid is None:
            grid = Grid(max(0, width), max(0, height))
            art = self.fit(width, height)
            x0, y0 = (grid.width - art.width) // 2, (grid.height - art.height) // 2
            for y, row in enumerate(art.rows()):
                start = (y0 + y) * grid.width + x0
                grid.cells[start : start + art.width] = row
            self._frames[key] = grid
            if len(self._frames) > _CACHE_SIZE:
                self._frames.popitem(last=False)
        else:
            self._frames.move_to_end(key)
        return grid.copy()

    def fit(self, width: int, height: int | None = None) -> Grid:
        """The image as a grid tight around it, at most ``width`` x ``height`` cells."""
        if width <= 0 or (height is not None and height <= 0):
            return Grid(0, 0)
        if self._prepared is None:
            self._prepared = _prepare(
                self.image, self.transparent_bg, self.bg_tolerance, self.autocrop, self.despeckle
            )
        return self._render(self._prepared, width, height)

    # -- rendering ---------------------------------------------------------------

    def _render(self, im: Image.Image, width: int, height: int | None) -> Grid:
        sx, sy = SAMPLES[self.mode]
        pw, ph = _fit_pixels(im.size, width, height, sx, sy)
        if im.size != (pw, ph):
            im = im.resize((pw, ph), self.resample)  # Pillow premultiplies alpha for RGBA
        cols, rows = -(-pw // sx), -(-ph // sy)

        # Pad to whole cells; the padding is transparent.
        px = np.zeros((rows * sy, cols * sx, 4), np.float32)
        px[:ph, :pw] = np.asarray(im, np.float32) / 255.0
        rgb, opaque = px[..., :3], px[..., 3] >= _ALPHA_CUTOFF
        if self.invert:
            rgb = 1.0 - rgb
        if self.gamma != 1.0:
            rgb = rgb**self.gamma
        lum = np.clip(rgb @ _LUMA, 0.0, 1.0)
        alpha = px[..., 3]
        if self.outline:  # keep only the rim of the ink, at full strength; the rest is transparent
            opaque = _boundary(opaque & (lum >= self.threshold))
            lum = alpha = opaque.astype(np.float32)
        colors = self._pixel_colors(rgb, lum)

        if self.mode == "ramp":
            cells = self._ramp_cells(lum, opaque, colors)
        elif self.mode == "half":
            cells = self._half_cells(lum, opaque, colors)
        elif self.mode == "braille":
            cells = self._braille_cells(lum, opaque, colors)
        else:
            cells = self._glyph_cells(lum * alpha, opaque, colors)
        return Grid(cols, rows, cells)

    def _pixel_colors(self, rgb: np.ndarray, lum: np.ndarray) -> _Colors:
        """Per-pixel fg color (float 0..255, shape h x w x 3), or None for "no color"."""
        if self.tint is not None:
            tint = np.array(self.tint, np.float32)
            if self.tint_shade:
                return lum[..., None] * tint
            return np.broadcast_to(tint, rgb.shape)
        return rgb * 255.0 if self.color else None

    @property
    def _monochrome(self) -> bool:
        """True if all glyphs share one fg (default or flat tint), so pixels are on/off."""
        if self.tint is None:
            return not self.color
        return not self.tint_shade

    def _lit(self, lum: np.ndarray, opaque: np.ndarray) -> np.ndarray:
        """Opaque pixels bright enough to draw: ``lum >= threshold``, or dithered."""
        if not self.dither:
            return opaque & (lum >= self.threshold)
        shifted = np.where(opaque, np.clip(lum + (0.5 - self.threshold), 0.0, 1.0), 0.0)
        img = Image.fromarray(np.rint(shifted * 255).astype(np.uint8), "L")
        return opaque & np.asarray(img.convert("1"), dtype=bool)  # Floyd-Steinberg

    def _ramp_cells(self, lum: np.ndarray, opaque: np.ndarray, colors: _Colors) -> list[Cell]:
        ramp = self.ramp
        idx = np.minimum((lum * len(ramp)).astype(np.int32), len(ramp) - 1)
        fgs = _rgb_list(colors)
        cells = []
        for i, (k, op) in enumerate(zip(idx.ravel().tolist(), opaque.ravel().tolist())):
            ch = ramp[k]
            cells.append(Cell(ch, fgs[i] if fgs else None) if op and ch != " " else BLANK)
        return cells

    def _half_cells(self, lum: np.ndarray, opaque: np.ndarray, colors: _Colors) -> list[Cell]:
        if self._monochrome:
            fg = self.tint if self.tint is not None and not self.tint_shade else None
            lit = self._lit(lum, opaque)
            codes = (lit[0::2] * 2 + lit[1::2]).ravel().tolist()
            return [Cell(_HALF_GLYPHS[c], fg) if c else BLANK for c in codes]
        assert colors is not None
        tops, bottoms = _rgb_list(colors[0::2]), _rgb_list(colors[1::2])
        top_op, bot_op = opaque[0::2].ravel().tolist(), opaque[1::2].ravel().tolist()
        cells = []
        for top, bottom, t_op, b_op in zip(tops, bottoms, top_op, bot_op):
            if t_op and b_op:
                cells.append(Cell("█", top) if top == bottom else Cell("▀", top, bottom))
            elif t_op:
                cells.append(Cell("▀", top))
            elif b_op:
                cells.append(Cell("▄", bottom))
            else:
                cells.append(BLANK)
        return cells

    def _braille_cells(self, lum: np.ndarray, opaque: np.ndarray, colors: _Colors) -> list[Cell]:
        rows, cols = lum.shape[0] // 4, lum.shape[1] // 2
        lit = self._lit(lum, opaque).reshape(rows, 4, cols, 2)
        bits = (lit * _BRAILLE_BITS[None, :, None, :]).sum(axis=(1, 3)).ravel().tolist()
        if colors is None:
            fgs = None
        else:
            # Each glyph is drawn in the mean color of its lit dots.
            per_dot = colors.reshape(rows, 4, cols, 2, 3) * lit[..., None]
            count = np.maximum(lit.sum(axis=(1, 3)), 1)[..., None]
            fgs = _rgb_list(per_dot.sum(axis=(1, 3)) / count)
        return [
            Cell(chr(_BRAILLE_BASE + b), fgs[i] if fgs else None) if b else BLANK
            for i, b in enumerate(bits)
        ]

    def _glyph_cells(self, ink: np.ndarray, opaque: np.ndarray, colors: _Colors) -> list[Cell]:
        rows, cols = ink.shape[0] // MASK_H, ink.shape[1] // MASK_W
        table = _glyph_table(self.charset)
        patches = _cell_patches(ink, rows, cols)  # (cells, MASK_H * MASK_W) ink coverage
        if self.fill:  # every cell with enough ink gets a glyph, never a space
            idx = table.match(patches, skip_space=True)
            idx[patches.mean(axis=1) < self.threshold] = 0
        else:
            idx = table.match(patches)
        idx[~_cell_patches(opaque, rows, cols).any(axis=1)] = 0
        if colors is None:
            fgs = None
        else:
            # Each glyph is drawn in the ink-weighted mean color of its cell.
            weights = patches[..., None]
            total = weights.sum(axis=1)
            cell_colors = _cell_patches(colors, rows, cols)  # (cells, samples, 3)
            mean = (cell_colors * weights).sum(axis=1) / np.maximum(total, 1e-6)
            fgs = _rgb_list(np.where(total > 1e-6, mean, cell_colors.mean(axis=1)))
        chars = table.chars
        return [
            Cell(chars[k], fgs[i] if fgs else None) if k else BLANK
            for i, k in enumerate(idx.tolist())
        ]


# -- glyph matching ------------------------------------------------------------
#
# A cell's patch p (ink coverage 0..1 on the MASK_H x MASK_W grid) is compared
# with every glyph mask g by
#
#     d(p, g) = |p - g|^2 + _GLYPH_DENSITY_WEIGHT * |_GLYPH_TONE * B(p) - B(g)|^2
#
# where B is a Gaussian blur inside the cell. The first term matches shape at
# full resolution, so a thin line gets the glyph whose stroke lies on it ('|',
# '_', '/'). The second matches density at a coarser scale, where a one-sample
# offset barely matters. It compresses the image's tone, because even the
# densest glyph covers only about 30% of its cell: without it, a half-inked cell
# is closer to '@' than to anything with ink on one side, so edges and holes
# all turn into '@'. Solid ink still lands on the densest glyph.

_GLYPH_BLUR = 1.0  # sigma of B, in mask samples
_GLYPH_DENSITY_WEIGHT = 8.0
_GLYPH_TONE = 0.4  # glyph coverage that solid ink corresponds to in the density term


def _blur_matrix(n: int, sigma: float) -> np.ndarray:
    """n x n matrix of a 1D Gaussian blur that treats everything outside 0..n-1 as zero."""
    offsets = np.arange(n)[:, None] - np.arange(n)[None, :]
    radius = math.ceil(2 * sigma)
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2) * (np.abs(offsets) <= radius)
    return kernel / np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2).sum()


# Blur of a flattened (row-major) mask: x @ _BLUR.T.
_BLUR = np.kron(_blur_matrix(MASK_H, _GLYPH_BLUR), _blur_matrix(MASK_W, _GLYPH_BLUR)).astype(
    np.float32
)


@dataclass(frozen=True)
class _GlyphTable:
    """The candidate glyphs of a charset, space first, with their matching features."""

    chars: str
    features: np.ndarray  # (glyphs, 2 * MASK_H * MASK_W): [g, sqrt(w) * B(g)]
    norms: np.ndarray  # (glyphs,) squared length of each feature row

    def match(self, patches: np.ndarray, skip_space: bool = False) -> np.ndarray:
        """Index (into ``chars``) of the closest glyph for each patch: argmin of d(p, g).

        |p - g|^2 = |p|^2 - 2 p.g + |g|^2, and |p|^2 is the same for every glyph,
        so one matrix product gives all the distances.
        """
        first = 1 if skip_space else 0
        p = _glyph_features(patches.astype(np.float32), _GLYPH_TONE)
        d = self.norms[first:] - 2.0 * (p @ self.features[first:].T)
        return np.argmin(d, axis=1) + first


def _glyph_features(x: np.ndarray, tone: float) -> np.ndarray:
    """Rows of [x, sqrt(w) * tone * B(x)], so squared distances between rows are d(p, g)."""
    blurred = (math.sqrt(_GLYPH_DENSITY_WEIGHT) * tone) * (x @ _BLUR.T)
    return np.concatenate([x, blurred], axis=-1)


@functools.cache
def _glyph_table(charset: str) -> _GlyphTable:
    chars = " " + "".join(dict.fromkeys(c for c in charset if c != " "))
    if len(chars) == 1:
        raise ValueError("charset must contain at least one non-space character")
    missing = "".join(c for c in chars if c not in MASKS)
    if missing:
        raise ValueError(f"charset has characters without a glyph mask (ASCII only): {missing!r}")
    masks = np.array([[int(d, 16) for d in MASKS[c] if d != " "] for c in chars], np.float32)
    masks /= LEVELS
    features = _glyph_features(masks, 1.0)
    return _GlyphTable(chars, features, (features * features).sum(axis=1))


def _cell_patches(a: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """(rows*MASK_H, cols*MASK_W, ...) -> (rows*cols, MASK_H*MASK_W, ...), cells row-major."""
    extra = a.shape[2:]
    a = a.reshape(rows, MASK_H, cols, MASK_W, *extra).swapaxes(1, 2)
    return a.reshape(rows * cols, MASK_H * MASK_W, *extra)


# -- fitting -------------------------------------------------------------------


def _fit_pixels(
    size: tuple[int, int], width: int, height: int | None, sx: int, sy: int
) -> tuple[int, int]:
    """Pixel size to resample to: fits ``width*sx`` x ``height*sy``, keeps the aspect ratio."""
    w, h = size
    pixel_aspect = CELL_ASPECT * sx / sy  # how much taller than wide one sample is on screen
    pw = float(width * sx)
    ph = pw * h / (w * pixel_aspect)
    if height is not None and ph > height * sy:
        ph = float(height * sy)
        pw = min(pw, ph * pixel_aspect * w / h)
    return max(1, math.floor(pw + 0.5)), max(1, math.floor(ph + 0.5))


def _rgb_list(colors: np.ndarray | None) -> list[Color] | None:
    """Float colors (..., 3) -> flat list of int RGB tuples."""
    if colors is None:
        return None
    flat = np.clip(np.rint(colors), 0, 255).astype(np.int32).reshape(-1, 3)
    return [tuple(c) for c in flat.tolist()]


# -- loading and preprocessing (at source resolution) ---------------------------


def _load(image: ImageInput) -> Image.Image:
    if isinstance(image, Image.Image):
        im = ImageOps.exif_transpose(image)
    else:
        with Image.open(image) as f:
            f.draft(None, (_MAX_SIDE, _MAX_SIDE))  # JPEG: decode big photos at 1/2..1/8 scale
            im = ImageOps.exif_transpose(f)
    if im.mode.startswith("I;16") or (im.mode == "I" and (im.getextrema()[1] or 0) > 255):
        # 16-bit grayscale (PNG, TIFF): convert() would clip it at 255, all white.
        gray = np.clip(np.rint(np.asarray(im, np.float32) / 257), 0, 255)
        im = Image.fromarray(gray.astype(np.uint8), "L")
    im = im.convert("RGBA")
    if max(im.size) > _MAX_SIDE:
        im.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.Resampling.LANCZOS)
    return im


def _prepare(
    image: ImageInput, transparent_bg: bool, bg_tolerance: float, autocrop: bool, despeckle: bool
) -> Image.Image:
    """Decode to RGBA, then apply ``despeckle``, ``transparent_bg`` and ``autocrop``."""
    im = _load(image)
    if not (transparent_bg or autocrop or despeckle):
        return im
    px = np.array(im)  # writable h x w x 4 uint8
    rgb = px[..., :3].astype(np.float32) / 255.0
    opaque = px[..., 3] >= _ALPHA_CUTOFF * 255
    bg = _background_color(rgb, opaque)
    ink = opaque if bg is None else opaque & (_distance(rgb, bg) > _INK_CUTOFF)
    specks = _specks(ink)
    if despeckle and specks.any():
        erase = _dilate(specks) & ~(ink & ~specks)  # the speck plus its anti-aliased rim
        if bg is None:
            px[erase, 3] = 0
            opaque &= ~erase
        else:
            px[erase, :3] = np.rint(bg * 255).astype(np.uint8)
            rgb[erase] = bg
    if transparent_bg and bg is not None:
        passable = ~opaque | (_distance(rgb, bg) <= bg_tolerance)
        labels = _label(passable)
        outside = np.unique(labels[_border(labels.shape)])
        px[np.isin(labels, outside[outside > 0]), 3] = 0
    if autocrop:
        box = _bbox(ink & ~specks) or _bbox(ink)
        if box is not None:
            left, top, right, bottom = box
            px = px[top:bottom, left:right]
    return Image.fromarray(np.ascontiguousarray(px), "RGBA")


def _distance(rgb: np.ndarray, color: np.ndarray) -> np.ndarray:
    """Per-pixel max channel difference to ``color`` (0..1)."""
    return np.abs(rgb - color).max(axis=2)


def _border(shape: tuple[int, ...]) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=bool)
    mask[0, :] = mask[-1, :] = mask[:, 0] = mask[:, -1] = True
    return mask


def _background_color(rgb: np.ndarray, opaque: np.ndarray) -> np.ndarray | None:
    """Median color of the opaque border pixels, or None if most of the border is transparent."""
    border = _border(rgb.shape)
    opaque_border = border & opaque
    if np.count_nonzero(opaque_border) * 2 < np.count_nonzero(border):
        return None
    return np.median(rgb[opaque_border], axis=0)


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the True pixels, or None if there are none."""
    ys, xs = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if ys.size == 0:
        return None
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def _specks(ink: np.ndarray) -> np.ndarray:
    """Ink in tiny detached clusters (under 0.02% of the image area, never the largest one).

    Pixels within 2px of each other count as one cluster, so anti-aliased
    diagonal strokes don't break apart.
    """
    min_area = 0.0002 * ink.size
    if min_area <= 1 or not ink.any():
        return np.zeros_like(ink)
    labels = _label(_dilate(ink))
    areas = np.bincount(labels[ink], minlength=labels.max() + 1)
    small = areas < min_area
    small[0] = False
    small[np.argmax(areas)] = False
    return ink & small[labels]


def _boundary(mask: np.ndarray) -> np.ndarray:
    """Pixels of ``mask`` with a pixel outside it (or the image edge) among their 8 neighbours."""
    return mask & _dilate(~np.pad(mask, 1))[1:-1, 1:-1]


def _dilate(mask: np.ndarray) -> np.ndarray:
    """3x3 binary dilation."""
    h, w = mask.shape
    padded = np.pad(mask, 1)
    out = np.zeros_like(mask)
    for dy in range(3):
        for dx in range(3):
            out |= padded[dy : dy + h, dx : dx + w]
    return out


def _label(mask: np.ndarray) -> np.ndarray:
    """Label the 4-connected components of ``mask`` (0 = not in mask).

    A component's label is 1 + the row-major index of its first pixel. Union-find
    over the edges between neighbouring mask pixels, vectorized: each round
    hooks every root onto the smallest root it shares an edge with, then
    flattens the trees. The number of rounds stays small whatever the shapes
    (long diagonals, spirals, noise); propagating labels along runs instead
    needs one pass per turn of a component, which takes seconds to minutes.
    """
    h, w = mask.shape
    index = np.arange(h * w, dtype=np.int64).reshape(h, w)
    across, down = mask[:, :-1] & mask[:, 1:], mask[:-1, :] & mask[1:, :]
    a = np.concatenate([index[:, :-1][across], index[:-1, :][down]])
    b = np.concatenate([index[:, 1:][across], index[1:, :][down]])
    parent = np.arange(h * w, dtype=np.int64)  # always points to a smaller index or itself
    while True:
        pa, pb = parent[a], parent[b]  # roots: parent is flat after every round
        joined = pa != pb
        if not joined.any():
            return np.where(mask, parent.reshape(h, w) + 1, 0)
        a, b, pa, pb = a[joined], b[joined], pa[joined], pb[joined]
        np.minimum.at(parent, np.maximum(pa, pb), np.minimum(pa, pb))
        while True:
            grand = parent[parent]
            if np.array_equal(grand, parent):
                break
            parent = grand
