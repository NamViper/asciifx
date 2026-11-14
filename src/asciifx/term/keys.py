"""Non-blocking keyboard input (select + os.read) and key decoding."""

from __future__ import annotations

import codecs
import os
import re
import select
import time

_KEY_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1bO.|\x1b[^\x1b]?|.", re.DOTALL)
_PARTIAL_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*|O)?\Z")  # input ending in an unfinished escape sequence

ESC_DELAY = 0.05
"""Seconds to wait for the rest of an escape sequence split across reads (so a lone Esc is this late)."""

_NAMED = {
    "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
    "\x1bOA": "up", "\x1bOB": "down", "\x1bOC": "right", "\x1bOD": "left",
    "\x1b[H": "home", "\x1b[F": "end", "\x1b[5~": "pageup", "\x1b[6~": "pagedown",
    "\x1b": "esc", "\r": "enter", "\n": "enter", "\t": "tab", " ": "space",
    "\x7f": "backspace", "\x08": "backspace",
}


def parse_keys(text: str) -> list[str]:
    """Split decoded input into keys: named keys ("up", "esc", "ctrl+a", ...) or raw chars."""
    return [_name(m.group()) for m in _KEY_RE.finditer(text)]


def _name(s: str) -> str:
    if s in _NAMED:
        return _NAMED[s]
    if len(s) == 1 and ord(s) < 32:
        return f"ctrl+{chr(ord(s) + 96)}"
    if len(s) == 2 and s[0] == "\x1b":
        return f"alt+{_name(s[1])}"  # terminals send Alt+key as ESC + key
    return s


class KeyReader:
    """Reads whatever is available on ``fd`` without blocking past ``timeout``."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._eof = False

    def poll(self, timeout: float) -> list[str]:
        """Wait up to ``timeout`` seconds for input; return the keys read (maybe none)."""
        timeout = max(0.0, timeout)
        if self._eof:
            time.sleep(timeout)
            return []
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return []
        data = os.read(self.fd, 4096)
        if not data:
            self._eof = True  # don't spin on a closed input
            return []
        text = self._decoder.decode(data)
        # A key's sequence (e.g. "\x1b[A") may arrive in pieces: an "\x1b" now and
        # "[A" a moment later would otherwise decode as Esc, "[" and "A".
        while _PARTIAL_RE.search(text) and select.select([self.fd], [], [], ESC_DELAY)[0]:
            more = os.read(self.fd, 4096)
            if not more:
                self._eof = True
                break
            text += self._decoder.decode(more)
        return parse_keys(text)
