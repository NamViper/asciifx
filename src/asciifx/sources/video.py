"""Video -> animated ASCII/Unicode art, decoded by an ffmpeg subprocess.

ffmpeg is looked up on PATH first, then in the ``imageio-ffmpeg`` package
(``pip install 'asciifx[video]'``), which bundles a static binary.

How frames are picked: ffmpeg resamples the video to a constant frame rate
``rate`` (the video's own, at most 60 fps), so decoded frame ``i`` shows video
time ``i / rate``. It also scales each frame to exactly the pixels the image
mode samples for the current cell size, so the Python side only maps pixels to
cells. ``frame(t, ...)`` shows frame ``floor(t * speed * rate)``.

A reader thread keeps a few decoded frames ahead of the player. Frames the
player has already passed are dropped, so a slow player skips frames instead of
falling behind. If the wanted frame is behind the decoder or more than
``_MAX_GAP`` seconds ahead of it, or the terminal size changed, ffmpeg is
restarted at the wanted time with ``-ss``. At the end of the file a looping
reader restarts ffmpeg at frame 0 while the player still shows the last
buffered frames, so the loop point doesn't stall.

Frames are numbered by *position* on the player's timeline:
``lap * frames_per_pass + index_in_file``. Positions only grow, which keeps the
buffer ordered across loop points.

Call :meth:`VideoSource.close` (or use ``with``) to stop ffmpeg and the reader
thread. A finalizer also does it when the source is garbage-collected and at
interpreter exit.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from PIL import Image

from ..core.grid import CELL_ASPECT, Grid
from .image import SAMPLES, Mode, image_to_grid
from .styles import STYLES

type VideoPath = str | os.PathLike[str]

INSTALL_HINT = (
    "asciifx needs ffmpeg to play video, and none was found. Install either:\n"
    "  * the imageio-ffmpeg package, which bundles a static ffmpeg binary:\n"
    "      pip install 'asciifx[video]'   (pipx: pipx inject asciifx imageio-ffmpeg)\n"
    "  * or a system ffmpeg on PATH (apt install ffmpeg, brew install ffmpeg, ...)"
)

_MAX_RATE = 60  # decode at most this many frames per video second
_DEFAULT_RATE = 25  # when ffmpeg reports no frame rate
_BUFFER = 8  # decoded frames the reader keeps ahead of the player
_MAX_GAP = 2.0  # seconds: seek instead of decoding through a gap longer than this
_WAIT = 5.0  # seconds frame() waits for a frame from a fresh/slow decoder
_EPS = 1e-6  # absorbs float error in t * rate (2.9 * 10 = 28.999999999999996)


class FFmpegNotFoundError(RuntimeError):
    """No ffmpeg executable on PATH and no imageio-ffmpeg package."""


def find_ffmpeg() -> str:
    """Path of an ffmpeg executable: ``ffmpeg`` on PATH, else imageio-ffmpeg's binary.

    Raises :class:`FFmpegNotFoundError`, whose message says how to install one.
    """
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
    except ImportError:
        pass
    else:
        try:
            return str(imageio_ffmpeg.get_ffmpeg_exe())
        except RuntimeError:  # package present but its binary is missing
            pass
    raise FFmpegNotFoundError(INSTALL_HINT)


# -- probing ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VideoInfo:
    """What ``ffmpeg -i`` says about a video's first video stream."""

    width: int  # pixels as decoded, after rotation
    height: int
    aspect: float  # display width / height (sample aspect ratio and rotation applied)
    fps: float  # 0.0 if unknown
    duration: float | None  # seconds; None if unknown


_STREAM = re.compile(r"^\s*Stream #\S+.*?: Video: (.*)$", re.M)
_NEXT_STREAM = re.compile(r"^\s*Stream #", re.M)
_OPENED = re.compile(r"^Input #\d+, ", re.M)  # ffmpeg read the file and lists its streams
_SIZE = re.compile(r"[\s,](\d+)x(\d+)(?=[\s,\[]|$)")
_DAR = re.compile(r"DAR (\d+):(\d+)")
_FPS = re.compile(r"(\d+(?:\.\d+)?)(k?) fps")
_TBR = re.compile(r"(\d+(?:\.\d+)?)(k?) tbr")
_HMS = r"(\d+):(\d\d):(\d\d(?:\.\d+)?)"
_DURATION = re.compile(r"Duration: " + _HMS)
_STREAM_DURATION = re.compile(r"^\s*DURATION\s*: " + _HMS, re.M)
_ROTATION = re.compile(r"rotation of (-?\d+(?:\.\d+)?) degrees")
_COPY_TIME = re.compile(r"time=" + _HMS)


def _seconds(match: re.Match[str]) -> float:
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def _snap_ntsc(fps: float) -> float:
    """29.97 -> 30000/1001 and the like: ffmpeg prints NTSC rates rounded."""
    base = round(fps * 1.001)
    if abs(fps - round(fps)) > 0.005 and abs(fps * 1.001 - base) < 0.005:
        return base / 1.001
    return fps


def parse_probe(text: str) -> VideoInfo:
    """Parse the stderr of ``ffmpeg -i FILE``. Raises ValueError if it has no video stream."""
    stream = _STREAM.search(text)
    if stream is None:
        opened = _OPENED.search(text) is not None  # e.g. audio only: no error line, just no video
        raise ValueError(("" if opened else _last_error(text)) or "no video stream found")
    line = stream.group(1)
    nxt = _NEXT_STREAM.search(text, stream.end())
    block = text[stream.start() : nxt.start() if nxt else len(text)]

    size = _SIZE.search(" " + line + " ")
    if size is None:
        raise ValueError(f"cannot read the frame size from: {line.strip()}")
    width, height = int(size.group(1)), int(size.group(2))
    dar = _DAR.search(line)
    aspect = int(dar.group(1)) / int(dar.group(2)) if dar and int(dar.group(2)) else 0.0
    if aspect <= 0:
        aspect = width / height if height else 1.0
    rotation = _ROTATION.search(block)
    if rotation and round(float(rotation.group(1))) % 180 == 90:
        width, height, aspect = height, width, 1.0 / aspect

    fps = 0.0
    rate = _FPS.search(line) or _TBR.search(line)
    if rate:
        fps = float(rate.group(1)) * (1000 if rate.group(2) else 1)
        fps = _snap_ntsc(fps)

    duration = None
    own = _STREAM_DURATION.search(block)  # matroska: the stream's own length
    whole = _DURATION.search(text)
    if own:
        duration = _seconds(own)
    elif whole:
        duration = _seconds(whole)
    if duration is not None and duration <= 0:
        duration = None
    return VideoInfo(width, height, aspect, fps, duration)


def _last_error(text: str) -> str:
    """The last line of ffmpeg's stderr that reads like an error message."""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
        and not line.startswith(" ")
        and "At least one output file must be specified" not in line
    ]
    return lines[-1] if lines else ""


def _is_url(path: VideoPath) -> bool:
    return "://" in os.fspath(path)


def _input(path: VideoPath) -> str:
    """``path`` as ffmpeg's ``-i`` argument. Local files get ffmpeg's ``file:`` prefix,
    so a name like ``12:30.mp4`` or ``concat:x.mp4`` isn't read as a protocol."""
    return os.fspath(path) if _is_url(path) else "file:" + os.fspath(path)


def probe(path: VideoPath, ffmpeg: str | None = None) -> VideoInfo:
    """Size, aspect, frame rate and duration of a video, from ``ffmpeg -i``.

    If the container doesn't record a duration (e.g. browser-recorded WebM), it
    is measured by reading the whole file once without decoding.
    """
    exe = ffmpeg or find_ffmpeg()
    if not _is_url(path) and not Path(path).is_file():
        raise FileNotFoundError(f"no such video file: {os.fspath(path)}")
    proc = subprocess.run(
        [exe, "-nostdin", "-hide_banner", "-i", _input(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    info = parse_probe(proc.stderr.decode("utf-8", "replace"))
    if info.duration is None and not _is_url(path):
        info = VideoInfo(info.width, info.height, info.aspect, info.fps, _measure(exe, path))
    return info


def _measure(exe: str, path: VideoPath) -> float | None:
    """Duration of the first video stream, by copying its packets to nowhere."""
    proc = subprocess.run(
        [exe, "-nostdin", "-hide_banner", "-i", _input(path), "-map", "0:v:0", "-c", "copy",
         "-f", "null", "-"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=600,
    )  # fmt: skip
    times = list(_COPY_TIME.finditer(proc.stderr.decode("utf-8", "replace")))
    return (_seconds(times[-1]) or None) if times else None


# -- geometry ----------------------------------------------------------------------


def fit_video(aspect: float, width: int, height: int, mode: Mode) -> tuple[int, tuple[int, int]]:
    """Columns the video gets in ``width`` x ``height`` cells, and ffmpeg's output size.

    The output size has the video's display ``aspect`` in square pixels and is
    exactly ``columns * sx`` pixels wide (``sx`` x ``sy`` = pixels per cell of
    ``mode``), so ``image_to_grid(frame, columns)`` samples it as is. Only ramp
    mode, whose samples are twice as tall as wide, still halves its height.
    """
    sx, sy = SAMPLES[mode]
    sample_aspect = CELL_ASPECT * sx / sy  # on-screen height / width of one sample
    cols = float(width)
    if cols * sx / (aspect * sample_aspect) > height * sy:  # too tall: fit the height
        cols = height * sy * sample_aspect * aspect / sx
    cols = max(1, min(width, math.floor(cols + 0.5)))
    pw = cols * sx
    ph = max(1, min(math.floor(pw / aspect + 0.5), math.floor(height * sy * sample_aspect)))
    return cols, (pw, ph)


def _center(art: Grid, width: int, height: int) -> Grid:
    grid = Grid(width, height)
    x0, y0 = (width - art.width) // 2, (height - art.height) // 2
    for y, row in enumerate(art.rows()):
        start = (y0 + y) * width + x0
        grid.cells[start : start + art.width] = row
    return grid


# -- decoding ----------------------------------------------------------------------


type _Frame = tuple[int, bytes]  # (position, rgb24 pixels)


class _Decoder:
    """Decodes frames of one pixel ``size`` from ``start`` on, in a reader thread.

    One ffmpeg process per pass through the file. Frames go into a buffer of at
    most ``_BUFFER``; the reader waits while it is full. ``count`` is the number
    of frames per pass: positions ``>= count`` within a pass are never produced
    (extra frames are cut), and missing ones are skipped (the last frame holds).
    """

    def __init__(
        self,
        exe: str,
        path: VideoPath,
        size: tuple[int, int],
        rate: Fraction,
        start: int,
        count: int | None,
        loop: bool,
        upscale: bool,
    ) -> None:
        self.size = size
        self.count = count  # learned at the end of the file if unknown
        self.error: str | None = None
        self._exe, self._path, self._rate, self._loop = exe, path, rate, loop
        self._flags = "bicubic" if upscale else "area"
        self._nbytes = size[0] * size[1] * 3
        self._cond = threading.Condition()
        self._frames: deque[_Frame] = deque()
        self._next = start  # position of the next frame the reader adds
        self._done = False  # reader finished: end of a non-looping video, error, or closed
        self._closed = False
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr: IO[bytes] | None = None
        self._thread = threading.Thread(
            target=self._run, args=(start,), name="asciifx-video", daemon=True
        )
        self._thread.start()

    # -- player side --

    def can_reach(self, pos: int) -> bool:
        """True if ``pos`` is buffered or coming soon (not behind, not too far ahead)."""
        with self._cond:
            first = self._frames[0][0] if self._frames else self._next
            if pos < first:
                return False
            return self._done or pos - self._next <= _MAX_GAP * self._rate

    def get(self, pos: int, timeout: float) -> _Frame | None:
        """The latest frame at or before ``pos``, dropping older ones.

        Waits up to ``timeout`` for the decoder to get there. Returns the first
        frame if the decoder started after ``pos``, and None if there is none.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            frames = self._frames
            while True:
                if len(frames) >= 2 and frames[1][0] <= pos:
                    while len(frames) >= 2 and frames[1][0] <= pos:
                        frames.popleft()
                    self._cond.notify_all()  # room for the reader
                if frames and (frames[0][0] >= pos or len(frames) >= 2 or self._done):
                    return frames[0]
                if self._done:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return frames[0] if frames else None
                self._cond.wait(remaining)

    def close(self) -> None:
        """Kill ffmpeg and wait for it and the reader thread to finish."""
        with self._cond:
            self._closed = True
            self._frames.clear()
            proc = self._proc
            self._cond.notify_all()
        if proc is not None:
            _kill(proc)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)
        if proc is not None:
            proc.wait()

    # -- reader thread --

    def _run(self, pos: int) -> None:
        try:
            while True:
                count = self.count
                index = pos % count if self._loop and count else pos
                base = pos - index  # position of the file's frame 0 in this pass
                proc = self._spawn(index)
                if proc is None:
                    return
                n = index
                try:
                    assert proc.stdout is not None
                    while count is None or n < count:
                        data = proc.stdout.read(self._nbytes)
                        if len(data) < self._nbytes:
                            break
                        if not self._put(base + n, data):
                            return
                        n += 1
                finally:
                    stderr = self._reap(proc)
                if self._closed:
                    return
                if n == index:  # nothing decoded
                    if index == 0:
                        detail = _last_error(stderr)
                        self.error = "ffmpeg decoded no frames" + (f": {detail}" if detail else "")
                        return
                    if count is None:  # sought past the end of a video of unknown length
                        return
                if count is None:
                    self.count = count = n
                if not self._loop:
                    return
                pos = base + count
        except Exception as exc:  # report it to the player instead of dying silently
            self.error = f"video decoding failed: {exc!r}"
        finally:
            with self._cond:
                self._done = True
                self._cond.notify_all()

    def _spawn(self, index: int) -> subprocess.Popen[bytes] | None:
        w, h = self.size
        cmd = [self._exe, "-nostdin", "-hide_banner", "-loglevel", "error"]
        if index > 0:  # a quarter frame early, so rounding never skips frame `index`
            cmd += ["-ss", f"{float((index - Fraction(1, 4)) / self._rate):.6f}"]
        cmd += [
            "-i", _input(self._path), "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", f"fps={self._rate},scale={w}:{h}:flags={self._flags}",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ]  # fmt: skip
        with self._cond:
            if self._closed:
                return None
            self._stderr = tempfile.TemporaryFile()
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                start_new_session=True,  # terminal signals (^C, ^Z) go to us only
            )
            return self._proc

    def _reap(self, proc: subprocess.Popen[bytes]) -> str:
        """Stop ``proc`` if needed, wait for it, and return its stderr."""
        _kill(proc)
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
        text = ""
        with self._cond:
            stderr, self._stderr = self._stderr, None
        if stderr is not None:
            try:
                stderr.seek(0)
                text = stderr.read().decode("utf-8", "replace")
            finally:
                stderr.close()
        return text

    def _put(self, pos: int, data: bytes) -> bool:
        with self._cond:
            while len(self._frames) >= _BUFFER and not self._closed:
                self._cond.wait()
            if self._closed:
                return False
            self._frames.append((pos, data))
            self._next = pos + 1
            self._cond.notify_all()
            return True


def _kill(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _close_decoder(holder: list[_Decoder | None]) -> None:
    decoder, holder[0] = holder[0], None
    if decoder is not None:
        decoder.close()


# -- the source --------------------------------------------------------------------


class VideoSource:
    """A video file as a frame ``Source``: frame ``t`` shows video time ``t * speed``.

    The video is fitted and centered in each frame (cells taken as
    ``CELL_ASPECT`` times taller than wide) and drawn in ``mode`` / ``style``
    like :func:`~asciifx.sources.image.image_to_grid`; ``image_options`` are
    passed to it (``color``, ``tint``, ``invert``, ``gamma``, ``dither``,
    ``threshold``, ``charset``, ...). ``autocrop`` is refused: it would crop
    every frame differently. Audio is ignored.

    With ``loop`` the video repeats; without, the last frame holds once the
    video is over, and :attr:`duration` tells how long that takes.

    Owns an ffmpeg process and a thread: use ``with VideoSource(...) as v:`` or
    call :meth:`close`.
    """

    def __init__(
        self,
        path: VideoPath,
        *,
        style: str | None = None,
        mode: Mode = "half",
        loop: bool = True,
        speed: float = 1.0,
        ffmpeg: str | None = None,
        **image_options: Any,
    ) -> None:
        if not (math.isfinite(speed) and speed > 0):
            raise ValueError(f"speed must be a positive number, got {speed}")
        if image_options.get("autocrop"):
            raise ValueError("autocrop is not supported for video: each frame would crop differently")
        # Fail now, not mid-playback, on an unknown style or bad image option.
        image_to_grid(Image.new("RGB", (1, 1)), 1, 1, mode=mode, style=style, **image_options)
        self.path = path
        self.style = style
        self.mode: Mode = STYLES[style].get("mode", mode) if style else mode
        self.loop = loop
        self.speed = float(speed)
        self.image_options = image_options
        self.ffmpeg = ffmpeg or find_ffmpeg()
        self.info = probe(path, self.ffmpeg)
        fps = self.info.fps if self.info.fps > 0 else _DEFAULT_RATE
        self.rate = Fraction(min(fps, _MAX_RATE)).limit_denominator(1001)
        """Frames per video second as decoded."""
        duration = self.info.duration
        self._count = max(1, round(duration * self.rate)) if duration else None
        self._closed = False
        self._holder: list[_Decoder | None] = [None]
        self._finalizer = weakref.finalize(self, _close_decoder, self._holder)
        self._cache: tuple[tuple[int, int, int], Grid] | None = None
        self._last: tuple[tuple[int, int], bytes] | None = None  # last decoded frame

    # -- timing --

    @property
    def frame_count(self) -> int | None:
        """Frames per pass through the video at ``rate``; None while unknown."""
        decoder = self._holder[0]
        if self._count is None and decoder is not None and decoder.count is not None:
            self._count = decoder.count
        return self._count

    @property
    def video_duration(self) -> float | None:
        """Length of the video in seconds; None if unknown."""
        count = self.frame_count
        return None if count is None else count / float(self.rate)

    @property
    def duration(self) -> float | None:
        """Seconds of ``t`` one pass takes (video length / speed); None if unknown.

        With ``loop=False``, frames from ``t = duration`` on show the last frame.
        """
        length = self.video_duration
        return None if length is None else length / self.speed

    def position(self, t: float) -> int:
        """Frame number for time ``t``: counts on across loops, held at the end without."""
        pos = math.floor(max(t, 0.0) * self.speed * float(self.rate) + _EPS)
        count = self.frame_count
        if not self.loop and count is not None:
            pos = min(pos, count - 1)
        return pos

    # -- Source --

    def frame(self, t: float, width: int, height: int) -> Grid:
        """The video frame at ``t * speed`` seconds, fitted and centered in ``width`` x ``height``."""
        if self._closed:
            raise ValueError("frame() called on a closed VideoSource")
        if width <= 0 or height <= 0:
            return Grid(max(0, width), max(0, height))
        cols, size = fit_video(self.info.aspect, width, height, self.mode)
        pos = self.position(t)
        decoder = self._holder[0]
        if decoder is None or decoder.size != size or not decoder.can_reach(pos):
            decoder = self._restart(size, pos)
        got = decoder.get(pos, _WAIT)
        if got is None:
            if decoder.error and self._last is None:
                raise RuntimeError(f"{os.fspath(self.path)}: {decoder.error}")
            if self._last is None:
                return Grid(width, height)
            frame_size, data = self._last  # e.g. resized after a non-looping video ended
            key = (-1, width, height)
        else:
            frame_size, data = size, got[1]
            self._last = (size, data)
            key = (got[0], width, height)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1].copy()
        image = Image.frombuffer("RGB", frame_size, data, "raw", "RGB", 0, 1)
        art = image_to_grid(
            image, cols, height, mode=self.mode, style=self.style, **self.image_options
        )
        grid = _center(art, width, height)
        self._cache = (key, grid)
        return grid.copy()

    def _restart(self, size: tuple[int, int], pos: int) -> _Decoder:
        _close_decoder(self._holder)
        upscale = size[0] * size[1] > self.info.width * self.info.height
        decoder = _Decoder(
            self.ffmpeg, self.path, size, self.rate, pos, self.frame_count, self.loop, upscale
        )
        self._holder[0] = decoder
        return decoder

    # -- cleanup --

    def close(self) -> None:
        """Stop ffmpeg and the reader thread. Safe to call more than once."""
        self._closed = True
        self._finalizer()

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
