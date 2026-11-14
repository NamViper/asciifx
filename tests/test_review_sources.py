"""Regression tests for bugs found reviewing sources/: image loading and labeling,
video file names and probing, ANSI parsing limits and SAUCE handling."""

import io
import struct
import subprocess
import time
from collections import deque

import numpy as np
import pytest
from PIL import Image, ImageDraw

from asciifx.sources import FFmpegNotFoundError, TextFramesSource, VideoSource, ansi_to_grid, find_ffmpeg
from asciifx.sources import image as image_mod
from asciifx.sources.image import image_to_grid
from asciifx.sources.video import parse_probe

ESC = "\x1b"


# -- image: connected components ---------------------------------------------------


def _reference_labels(mask: np.ndarray) -> np.ndarray:
    """4-connected components by BFS; label = 1 + row-major index of the first pixel."""
    h, w = mask.shape
    labels = np.zeros((h, w), np.int64)
    for start in range(h * w):
        y0, x0 = divmod(start, w)
        if not mask[y0, x0] or labels[y0, x0]:
            continue
        labels[y0, x0] = start + 1
        queue = deque([(y0, x0)])
        while queue:
            y, x = queue.popleft()
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = start + 1
                    queue.append((ny, nx))
    return labels


def test_label_matches_a_reference_flood_fill():
    rng = np.random.default_rng(7)
    for _ in range(60):
        h, w = (int(v) for v in rng.integers(1, 24, 2))
        mask = rng.random((h, w)) < rng.random()
        assert (image_mod._label(mask) == _reference_labels(mask)).all()
    assert image_mod._label(np.zeros((0, 5), bool)).shape == (0, 5)


def test_label_is_fast_on_tortuous_shapes():
    """Run-based label propagation needed one pass per turn: seconds for one long
    diagonal line, about a minute for noise near the percolation threshold."""
    rng = np.random.default_rng(0)
    noise = rng.random((512, 512)) < 0.6
    diagonal = Image.new("L", (1024, 1024))
    ImageDraw.Draw(diagonal).line([(0, 0), (1023, 1023)], fill=255, width=2)
    for mask in (noise, image_mod._dilate(np.asarray(diagonal) > 0)):
        start = time.perf_counter()
        image_mod._label(mask)
        assert time.perf_counter() - start < 1.5


def test_thin_line_logo_converts_quickly_with_all_cleanup_options():
    """The startup banner's options on a thin-lined logo took about 2 s per conversion."""
    im = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(im)
    draw.line([(100, 100), (900, 900)], fill="black", width=3)
    draw.line([(900, 100), (100, 900)], fill="black", width=3)
    draw.ellipse([50, 50, 950, 950], outline="black", width=3)
    start = time.perf_counter()
    image_to_grid(
        im, 40, 20, mode="braille", invert=True, transparent_bg=True, autocrop=True, despeckle=True
    )
    assert time.perf_counter() - start < 1.0


# -- image: loading ------------------------------------------------------------------


def test_16_bit_grayscale_png_keeps_its_tones(tmp_path):
    """Pillow opens these as I;16, and convert("RGBA") clipped them at 255: all white."""
    path = tmp_path / "gray16.png"
    Image.fromarray(np.linspace(0, 65535, 8).astype(np.uint16)[None, :]).save(path)
    assert Image.open(path).mode.startswith("I;16")
    grid = image_to_grid(path, 8, mode="ramp", color=True)
    grays = [c.fg[0] if c.fg else 0 for c in grid.cells]
    assert grays == sorted(grays) and grays[0] < 40 and grays[-1] > 215, grays
    assert len(set(grays)) >= 6


def test_32_bit_int_images_in_8_bit_range_are_not_rescaled():
    grid = image_to_grid(Image.new("I", (4, 4), 200), 4, 1, mode="ramp", color=True)
    assert grid.cells[0].fg == (200, 200, 200)


def test_big_jpeg_is_decoded_at_reduced_scale_and_still_rotated(monkeypatch):
    """draft() lets libjpeg skip most of the work and memory; EXIF rotation still applies."""
    im = Image.new("RGB", (4096, 2048), (200, 30, 30))
    exif = im.getexif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise when shown
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    buf.seek(0)
    seen = []
    real = image_mod.ImageOps.exif_transpose

    def spy(image, **kwargs):
        seen.append(image.size)
        return real(image, **kwargs)

    monkeypatch.setattr(image_mod.ImageOps, "exif_transpose", spy)
    loaded = image_mod._load(buf)
    assert seen and seen[0][0] < 4096  # decoded smaller than the file
    assert loaded.height > loaded.width  # portrait after rotation
    assert max(loaded.size) <= image_mod._MAX_SIDE


# -- video ---------------------------------------------------------------------------

AUDIO_ONLY = """\
[aist#0:0/pcm_s16le @ 0x5bd88b070080] Guessed Channel Layout: mono
Input #0, wav, from 'song.wav':
  Metadata:
    encoder         : Lavf63.1.102
  Duration: 00:00:01.00, bitrate: 706 kb/s
  Stream #0:0: Audio: pcm_s16le ([1][0][0][0] / 0x0001), 44100 Hz, mono, s16, 705 kb/s
At least one output file must be specified
"""


def test_audio_only_file_says_there_is_no_video():
    with pytest.raises(ValueError, match="^no video stream found$"):
        parse_probe(AUDIO_ONLY)
    with pytest.raises(ValueError, match="Invalid data found"):  # unreadable: ffmpeg's own error
        parse_probe("x.mp4: Invalid data found when processing input\n")


try:
    FFMPEG: str | None = find_ffmpeg()
except FFmpegNotFoundError:
    FFMPEG = None


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg (pip install imageio-ffmpeg)")
def test_local_file_names_are_never_read_as_ffmpeg_protocols(tmp_path, monkeypatch):
    """"12:30.mkv" failed ("Protocol not found"); "concat:blue.mkv" played blue.mkv."""
    assert FFMPEG is not None

    def encode(name: str, color: str) -> None:
        subprocess.run(
            [FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", f"color=c={color}:s=32x16:r=10:d=1,format=gbrp", "-c:v", "ffv1",
             str(tmp_path / name)],
            check=True,
        )  # fmt: skip

    encode("12:30.mkv", "red")
    encode("blue.mkv", "blue")
    encode("concat:blue.mkv", "red")
    encode("-dash.mkv", "red")
    monkeypatch.chdir(tmp_path)
    for name in ("12:30.mkv", "concat:blue.mkv", "-dash.mkv", str(tmp_path / "12:30.mkv")):
        with VideoSource(name) as video:
            assert video.frame(0.5, 8, 4).get(4, 2).fg == (255, 0, 0), name


# -- text frames ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "seq",
    [
        f"{ESC}[999999999C",  # forward: tried to allocate a 1e9-cell row
        f"{ESC}[99999999B",  # down: 1e8 row lists
        f"{ESC}[99999999;99999999H",
        f"{ESC}[99999999G",
        f"{ESC}[{'9' * 5000}C",  # int() refuses more than 4300 digits: ValueError
        f"{ESC}[{'9' * 5000}m",
        f"{ESC}[38;5;{'9' * 5000}m",
    ],
)
def test_huge_cursor_parameters_are_bounded(seq):
    start = time.perf_counter()
    grid = ansi_to_grid(f"a{seq}x")
    assert time.perf_counter() - start < 2.0
    assert grid.width <= 1000 and grid.height <= 1000
    assert sum(c.ch == "x" for c in grid.cells) == 1


def test_cursor_moves_still_work_far_down_long_files():
    """Rows reached by newlines stay reachable by cursor-down beyond the clamp."""
    grid = ansi_to_grid("\n" * 1500 + f"a{ESC}[5Ab{ESC}[3Bc{ESC}[2;3Hd{ESC}[10Ce")
    assert grid.get(0, 1500).ch == "a"
    assert grid.get(1, 1495).ch == "b"
    assert grid.get(2, 1498).ch == "c"
    assert grid.get(2, 1).ch == "d" and grid.get(13, 1).ch == "e"


def _sauce(width: int) -> bytes:
    record = (
        b"SAUCE00" + b"Title".ljust(35) + b"Me".ljust(20) + b"Grp".ljust(20) + b"20240101"
        + struct.pack("<I", 200) + bytes([1, 1]) + struct.pack("<HHHH", width, 25, 0, 0)
        + bytes([0, 0]) + b"IBM VGA".ljust(22, b"\0")
    )  # fmt: skip
    assert len(record) == 128
    return b"\x1a" + record


def test_sauce_record_does_not_force_cp437_on_utf8_art(tmp_path):
    """SAUCE's binary fields (here width 160 = 0xA0) aren't UTF-8 and made the whole file CP437."""
    path = tmp_path / "art.ans"
    path.write_bytes(f"{ESC}[31m█▓▒░ héllo{ESC}[0m\n".encode() + _sauce(160))
    frame = TextFramesSource(path).frames[0]
    assert "".join(c.ch for c in frame.cells) == "█▓▒░ héllo"


def test_cp437_art_with_sauce_is_still_cp437(tmp_path):
    path = tmp_path / "dos.ans"
    path.write_bytes(b"\xdb\xb2\xb1\xb0 \x82\r\n" + _sauce(80))
    frame = TextFramesSource(path).frames[0]
    assert "".join(c.ch for c in frame.cells) == "█▓▒░ é"
