"""Shared fixtures: a real pseudo-terminal for testing TTY behavior end to end."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import sys
import termios
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO, Any

import pytest


@dataclass
class FakeTTY:
    master: int
    slave: int
    out: IO[str]  # what the program writes to (isatty() is True)
    inp: IO[Any]  # what the program reads keys from
    _buf: bytearray = field(default_factory=bytearray)
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _pump(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([self.master], [], [], 0.02)
            if ready:
                with self._lock:  # read + append atomically w.r.t. output()
                    try:
                        self._buf.extend(os.read(self.master, 65536))
                    except OSError:
                        return

    def output(self, settle: float = 0.05) -> str:
        """Everything written to the terminal so far.

        Waits ``settle`` seconds (for the kernel to move the data to the master
        side), then until the pump thread has drained all pending bytes.
        """
        time.sleep(settle)
        deadline = time.monotonic() + 5
        while True:
            with self._lock:
                pending = struct.unpack("i", fcntl.ioctl(self.master, termios.FIONREAD, b"\0" * 4))[0]
                if pending == 0:
                    return self._buf.decode("utf-8", "replace")
            if time.monotonic() > deadline:
                raise AssertionError(f"pty output not drained ({pending} bytes pending)")
            time.sleep(0.01)

    def resize(self, cols: int, rows: int) -> None:
        """Change the pty's window size (doesn't send SIGWINCH by itself)."""
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def send(self, data: bytes) -> None:
        """Simulate typing on the keyboard."""
        os.write(self.master, data)

    def lflag(self) -> int:
        return termios.tcgetattr(self.slave)[3]


@pytest.fixture
def fake_tty() -> Iterator[FakeTTY]:
    master, slave = pty.openpty()
    out = os.fdopen(os.dup(slave), "w", encoding="utf-8")
    inp = os.fdopen(os.dup(slave), "rb", buffering=0)
    tty = FakeTTY(master, slave, out, inp)
    tty.resize(30, 8)
    pump = threading.Thread(target=tty._pump, daemon=True)
    pump.start()
    yield tty
    tty._stop.set()
    pump.join()
    out.close()
    inp.close()
    os.close(slave)
    os.close(master)


@pytest.fixture
def fast_switching() -> Iterator[None]:
    """Switch threads as often as possible, to shake out races."""
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    yield
    sys.setswitchinterval(old)
