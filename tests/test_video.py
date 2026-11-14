"""VideoSource: ffmpeg lookup, probing, fitting, time-based frames, resize, cleanup.

Test videos are generated with ffmpeg's lavfi into a temporary directory. They
use the lossless FFV1 codec in RGB, so decoded colors are exact. Tests that
need ffmpeg are skipped when none can be found.
"""

import gc
import io
import os
import subprocess
import sys
import threading
import types

import pytest
from PIL import Image

from asciifx import play
from asciifx.core import BLANK, Cell, Grid
from asciifx.sources import FFmpegNotFoundError, VideoSource, find_ffmpeg
from asciifx.sources import video as video_mod
from asciifx.sources.video import VideoInfo, fit_video, parse_probe
from asciifx.term import Terminal

RED, BLUE, WHITE = (255, 0, 0), (0, 0, 255), (255, 255, 255)

try:
    FFMPEG: str | None = find_ffmpeg()
except FFmpegNotFoundError:
    FFMPEG = None

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg (pip install imageio-ffmpeg)")


def lavfi(path, *sources: str) -> str:
    """Encode lavfi ``sources`` (concatenated) as lossless RGB FFV1 at ``path``."""
    assert FFMPEG is not None
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for src in sources:
        cmd += ["-f", "lavfi", "-i", src]
    inputs = "".join(f"[{i}:v]" for i in range(len(sources)))
    cmd += ["-filter_complex", f"{inputs}concat=n={len(sources)}:v=1[v]", "-map", "[v]"]
    cmd += ["-c:v", "ffv1", str(path)]
    subprocess.run(cmd, check=True)
    return str(path)


def solid(color: str, seconds: float = 1, size: str = "32x16", fps: int = 10) -> str:
    return f"color=c={color}:s={size}:r={fps}:d={seconds},format=gbrp"


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    """red_blue: 1 s red then 1 s blue. counter: frame i is gray 10*i. All 10 fps, 2:1."""
    if FFMPEG is None:
        pytest.skip("no ffmpeg")
    d = tmp_path_factory.mktemp("videos")
    counter = "color=c=black:s=16x8:r=10:d=2,format=gbrp,geq=r='10*N':g='10*N':b='10*N'"
    return types.SimpleNamespace(
        red_blue=lavfi(d / "red_blue.mkv", solid("red"), solid("blue")),
        counter=lavfi(d / "counter.mkv", counter),
        white=lavfi(d / "white.mkv", solid("white")),
        dir=d,
    )


@pytest.fixture
def spawned(monkeypatch):
    """Every subprocess started during the test."""
    procs: list[subprocess.Popen] = []
    real = subprocess.Popen

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            procs.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    return procs


def assert_no_leftovers(procs) -> None:
    for proc in procs:
        assert proc.returncode is not None, f"ffmpeg {proc.pid} still running"
        assert not os.path.exists(f"/proc/{proc.pid}"), f"ffmpeg {proc.pid} not reaped (zombie)"
    assert not [t for t in threading.enumerate() if t.name == "asciifx-video"]


def gray(grid: Grid, x: int = 0, y: int = 0) -> int:
    fg = grid.get(x, y).fg
    assert fg is not None and fg[0] == fg[1] == fg[2], fg
    return fg[0]


def bbox(grid: Grid) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the non-blank cells."""
    xs = [i % grid.width for i, c in enumerate(grid.cells) if c != BLANK]
    ys = [i // grid.width for i, c in enumerate(grid.cells) if c != BLANK]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


# -- finding ffmpeg --------------------------------------------------------------


def test_find_ffmpeg_prefers_path(monkeypatch):
    monkeypatch.setattr(video_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert find_ffmpeg() == "/usr/bin/ffmpeg"


def test_find_ffmpeg_falls_back_to_imageio_ffmpeg(monkeypatch):
    monkeypatch.setattr(video_mod.shutil, "which", lambda name: None)
    fake = types.ModuleType("imageio_ffmpeg")
    fake.get_ffmpeg_exe = lambda: "/site-packages/imageio_ffmpeg/binaries/ffmpeg"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    assert find_ffmpeg() == "/site-packages/imageio_ffmpeg/binaries/ffmpeg"


def test_find_ffmpeg_explains_how_to_install(monkeypatch):
    monkeypatch.setattr(video_mod.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)  # import fails
    with pytest.raises(FFmpegNotFoundError, match="imageio-ffmpeg") as err:
        find_ffmpeg()
    assert "PATH" in str(err.value)

    broken = types.ModuleType("imageio_ffmpeg")

    def no_binary():
        raise RuntimeError("No ffmpeg exe could be found")

    broken.get_ffmpeg_exe = no_binary  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", broken)
    with pytest.raises(FFmpegNotFoundError):
        find_ffmpeg()


# -- probing ---------------------------------------------------------------------

MKV = """\
Input #0, matroska,webm, from 'x.mkv':
  Duration: 00:00:02.00, start: 0.000000, bitrate: 10 kb/s
  Stream #0:0: Video: ffv1, bgr0(pc, gbr/unknown/unknown, progressive), 32x16, SAR 1:1 DAR 2:1, 10 fps, 10 tbr, 1k tbn
    Metadata:
      DURATION        : 00:00:01.900000000
At least one output file must be specified
"""

MP4 = """\
  Duration: 00:01:02.50, start: 0.000000, bitrate: 8 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, progressive), 1920x1080 [SAR 1:1 DAR 16:9], 3 kb/s, 29.97 fps, 29.97 tbr, 30k tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s (default)
      DURATION        : 00:09:00.000000000
"""

PHONE = """\
  Duration: 00:00:05.00, start: 0.000000, bitrate: 9000 kb/s
  Stream #0:0[0x1](eng): Video: hevc (Main) (hvc1 / 0x31637668), yuv420p(tv, bt709), 1920x1080, 9000 kb/s, 59.94 fps, 60 tbr, 600 tbn (default)
    Side data:
      displaymatrix: rotation of -90.00 degrees
"""


def test_parse_probe_mkv_prefers_the_streams_own_duration():
    assert parse_probe(MKV) == VideoInfo(32, 16, 2.0, 10.0, 1.9)


def test_parse_probe_mp4_ignores_codec_tags_and_other_streams():
    info = parse_probe(MP4)
    assert (info.width, info.height, info.duration) == (1920, 1080, 62.5)
    assert info.aspect == pytest.approx(16 / 9)
    assert info.fps == pytest.approx(30000 / 1001)  # printed rounded as 29.97


def test_parse_probe_rotation_anamorphic_and_unknown_duration():
    info = parse_probe(PHONE)
    assert (info.width, info.height) == (1080, 1920)
    assert info.aspect == pytest.approx(9 / 16)
    assert info.fps == pytest.approx(60000 / 1001)
    dvd = "  Duration: N/A, bitrate: N/A\n  Stream #0:0: Video: mpeg2video, yuv420p, 720x480 [SAR 32:27 DAR 16:9], 25 tbr\n"
    info = parse_probe(dvd)
    assert info.aspect == pytest.approx(16 / 9)  # display aspect, not 720/480
    assert (info.fps, info.duration) == (25.0, None)


def test_parse_probe_without_video_reports_ffmpegs_error():
    text = "notes.txt: Invalid data found when processing input\n"
    with pytest.raises(ValueError, match="Invalid data found"):
        parse_probe(text)


# -- fitting ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,aspect,width,height,expected",
    [
        # 2:1 video in 80x10 cells is height-limited: 40x10 cells
        ("half", 2.0, 80, 10, (40, (40, 20))),
        ("braille", 2.0, 80, 10, (40, (80, 40))),
        ("glyph", 2.0, 80, 10, (40, (200, 100))),
        ("ramp", 2.0, 80, 10, (40, (40, 20))),  # square pixels; image_to_grid halves them
        # 16:9 in 120x35 is width-limited: 120 columns, 34 rows
        ("half", 16 / 9, 120, 35, (120, (120, 68))),
        ("braille", 16 / 9, 120, 35, (120, (240, 135))),
        # portrait video in a wide terminal
        ("half", 9 / 16, 120, 35, (39, (39, 69))),
    ],
)
def test_fit_video(mode, aspect, width, height, expected):
    assert fit_video(aspect, width, height, mode) == expected


@pytest.mark.parametrize("mode", ["half", "braille", "glyph"])
def test_fitted_frames_are_sampled_without_resizing(monkeypatch, mode):
    from asciifx.sources import image_to_grid

    cols, size = fit_video(16 / 9, 120, 35, mode)

    def no_resize(*args, **kwargs):
        raise AssertionError("frame was resized in Python")

    monkeypatch.setattr(Image.Image, "resize", no_resize)
    grid = image_to_grid(Image.new("RGB", size, WHITE), cols, 35, mode=mode)
    assert (grid.width, grid.height) == (120, 34)


# -- playback --------------------------------------------------------------------


@needs_ffmpeg
def test_frames_are_chosen_by_time_and_loop(videos):
    with VideoSource(videos.red_blue) as video:
        assert (video.duration, video.frame_count, video.rate) == (2.0, 20, 10)
        for t, color in [(0.5, RED), (1.5, BLUE), (2.5, RED), (3.5, BLUE), (0.0, RED)]:
            grid = video.frame(t, 40, 10)
            assert (grid.width, grid.height) == (40, 10)
            assert set(grid.cells) == {Cell("█", color)}, t


@needs_ffmpeg
def test_exact_frame_index_sequential_and_random_order(videos):
    with VideoSource(videos.counter) as video:
        for i in range(80):  # four loops at 20 steps per second
            t = i * 0.05
            assert gray(video.frame(t, 20, 10), 10, 5) == 10 * (int(t * 10 + 1e-9) % 20), t
        for t in [1.0, 0.95, 0.3, 1.9, 0.0, 2.05, 7.4]:  # backward and forward jumps
            assert gray(video.frame(t, 20, 10), 10, 5) == 10 * (int(t * 10 + 1e-9) % 20), t


@needs_ffmpeg
def test_no_loop_holds_the_last_frame_and_reports_duration(videos):
    with VideoSource(videos.counter, loop=False) as video:
        assert video.duration == 2.0
        assert gray(video.frame(1.95, 20, 10), 10, 5) == 190
        assert gray(video.frame(2.0, 20, 10), 10, 5) == 190
        assert gray(video.frame(50.0, 20, 10), 10, 5) == 190
        assert gray(video.frame(60.0, 30, 12), 15, 6) == 190  # resized after the end


@needs_ffmpeg
def test_speed_scales_time_and_duration(videos):
    with VideoSource(videos.red_blue, loop=False, speed=2.0) as video:
        assert video.duration == 1.0
        assert video.frame(0.45, 40, 10).get(0, 0).fg == RED
        assert video.frame(0.5, 40, 10).get(0, 0).fg == BLUE  # video time 1.0
    with pytest.raises(ValueError, match="speed"):
        VideoSource(videos.red_blue, speed=0)


@needs_ffmpeg
@pytest.mark.parametrize(
    "options,glyph",
    [
        ({"mode": "half"}, "█"),
        ({"mode": "braille"}, "⣿"),
        ({"mode": "ramp"}, "@"),
        ({"style": "dots"}, "⣿"),
        ({"style": "shade"}, "█"),
        ({"style": "letters"}, None),
    ],
)
def test_sizes_per_mode_and_style(videos, options, glyph):
    with VideoSource(videos.white, **options) as video:
        wide = video.frame(0.0, 80, 10)  # height-limited: 40x10 cells, centered
        assert (wide.width, wide.height) == (80, 10)
        assert bbox(wide) == (20, 0, 60, 10)
        tall = video.frame(0.0, 40, 30)  # width-limited: 40x10 cells, centered
        assert (tall.width, tall.height) == (40, 30)
        assert bbox(tall) == (0, 10, 40, 20)
        cells = {c for c in tall.cells if c != BLANK}
        assert {c.fg for c in cells} == {WHITE}
        if glyph:
            assert {c.ch for c in cells} == {glyph}


@needs_ffmpeg
def test_image_options_are_passed_on(videos):
    with VideoSource(videos.white, color=False) as video:
        assert set(video.frame(0.0, 40, 10).cells) == {Cell("█")}
    with VideoSource(videos.red_blue, mode="braille", threshold=0.1, tint=(0, 255, 0), tint_shade=False) as video:
        assert set(video.frame(0.5, 40, 10).cells) == {Cell("⣿", (0, 255, 0))}
    with pytest.raises(ValueError, match="autocrop"):
        VideoSource(videos.white, autocrop=True)
    with pytest.raises(ValueError, match="unknown style"):
        VideoSource(videos.white, style="nope")


@needs_ffmpeg
def test_resize_restarts_decoding_at_the_current_time(videos, spawned):
    with VideoSource(videos.counter) as video:
        assert gray(video.frame(0.5, 20, 10), 10, 5) == 50
        first = video._holder[0]
        grid = video.frame(1.0, 30, 12)  # new size: ffmpeg restarts with -ss
        assert (grid.width, grid.height) == (30, 12)
        assert gray(grid, 15, 6) == 100
        assert video._holder[0] is not first
        assert gray(video.frame(1.1, 30, 12), 15, 6) == 110
        seeks = {p.args[p.args.index("-ss") + 1]: p for p in spawned if "-ss" in p.args}
        assert sorted(seeks) == ["0.475000", "0.975000"]  # a quarter frame before 5 and 10
        assert seeks["0.475000"].returncode is not None  # the old decoder was stopped


@needs_ffmpeg
def test_player_behind_drops_frames_instead_of_restarting(videos):
    with VideoSource(videos.counter) as video:
        assert gray(video.frame(0.0, 20, 10), 10, 5) == 0
        decoder = video._holder[0]
        assert gray(video.frame(1.5, 20, 10), 10, 5) == 150  # skipped 14 frames
        assert video._holder[0] is decoder
        assert len(decoder._frames) <= video_mod._BUFFER
        assert gray(video.frame(9.0, 20, 10), 10, 5) == 100  # far ahead: seek instead
        assert video._holder[0] is not decoder


@needs_ffmpeg
def test_close_leaves_no_process_or_thread(videos, spawned):
    video = VideoSource(videos.counter)
    video.frame(0.5, 20, 10)
    video.frame(0.6, 30, 10)  # resize: a second decoder, blocked on a full pipe
    assert any(p.poll() is None for p in spawned)
    video.close()
    video.close()  # idempotent
    assert video.closed
    assert_no_leftovers(spawned)
    with pytest.raises(ValueError, match="closed"):
        video.frame(0.0, 20, 10)


@needs_ffmpeg
def test_loop_point_and_garbage_collection_leave_no_process(videos, spawned):
    def watch():
        video = VideoSource(videos.red_blue)
        for i in range(50):  # crosses the loop point twice
            video.frame(i * 0.1, 40, 10)

    watch()
    gc.collect()
    assert_no_leftovers(spawned)


@needs_ffmpeg
def test_play_with_context_manager(videos, spawned, monkeypatch):
    monkeypatch.setenv("COLUMNS", "8")
    monkeypatch.setenv("LINES", "2")
    out = io.StringIO()
    with VideoSource(videos.red_blue) as video:
        play(video, terminal=Terminal(out=out))  # not a TTY: one static frame
    assert out.getvalue() == "████████\n████████\n"
    assert_no_leftovers(spawned)


@needs_ffmpeg
def test_lossy_h264_mp4(videos):
    path = videos.dir / "red_blue.mp4"
    subprocess.run(
        [FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-i", videos.red_blue,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    with VideoSource(path) as video:
        assert video.duration == 2.0
        for t, (r, g, b) in [(0.5, RED), (1.5, BLUE), (2.5, RED)]:
            fg = video.frame(t, 40, 10).get(20, 5).fg
            assert fg is not None and max(abs(fg[0] - r), abs(fg[1] - g), abs(fg[2] - b)) <= 6, fg


@needs_ffmpeg
def test_bad_inputs(videos, tmp_path):
    with pytest.raises(FileNotFoundError):
        VideoSource(tmp_path / "missing.mp4")
    text = tmp_path / "notes.txt"
    text.write_text("not a video\n")
    with pytest.raises(ValueError, match="Invalid data"):
        VideoSource(text)


def test_bad_options_fail_before_looking_for_ffmpeg(monkeypatch):
    def no_ffmpeg():
        raise FFmpegNotFoundError("none")

    monkeypatch.setattr(video_mod, "find_ffmpeg", no_ffmpeg)
    with pytest.raises(ValueError, match="speed"):
        VideoSource("x.mp4", speed=-1)
    with pytest.raises(ValueError, match="unknown style"):
        VideoSource("x.mp4", style="nope")
    with pytest.raises(FFmpegNotFoundError):
        VideoSource("x.mp4")
