"""Import cost: the lazy package namespace, and term/ not importing shutil."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from asciifx.term.terminal import _fallback_size

SRC = Path(__file__).resolve().parent.parent / "src"


def run_python(code: str) -> None:
    env = dict(os.environ, PYTHONPATH=str(SRC))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_import_asciifx_is_lazy_and_public_names_still_work():
    run_python("""
import sys
import asciifx
assert asciifx.__version__ == "0.1.0"
loaded = sorted(m for m in sys.modules if m.startswith("asciifx."))
assert not loaded, loaded

from asciifx import Cell, Grid, Layer, Terminal, compose, play
import asciifx.api, asciifx.core, asciifx.term
assert play is asciifx.api.play and Terminal is asciifx.term.Terminal
assert (Cell, Grid, Layer, compose) == (asciifx.core.Cell, asciifx.core.Grid, asciifx.core.Layer, asciifx.core.compose)
assert set(asciifx.__all__) <= set(dir(asciifx))
try:
    asciifx.nope
except AttributeError as e:
    assert "nope" in str(e)
else:
    raise AssertionError("expected AttributeError")
ns = {}
exec("from asciifx import *", ns)
assert {k for k in ns if not k.startswith("__")} == set(asciifx.__all__)
""")


def test_term_does_not_import_shutil():
    run_python("""
import sys
import asciifx.term
assert "shutil" not in sys.modules
assert "asciifx.api" not in sys.modules
""")


@pytest.mark.parametrize("columns,lines", [
    (None, None), ("100", "40"), ("120", None), (None, "30"), ("0", "-3"), ("abc", "50"), ("", " 7 "),
])
@pytest.mark.parametrize("stdout", ["captured", "none", "tty"])
def test_fallback_size_matches_shutil(monkeypatch, fake_tty, columns, lines, stdout):
    for name, value in (("COLUMNS", columns), ("LINES", lines)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    if stdout == "none":
        monkeypatch.setattr(sys, "__stdout__", None)
    elif stdout == "tty":
        monkeypatch.setattr(sys, "__stdout__", fake_tty.out)  # 30x8
    for fallback in ((80, 24), (13, 7)):
        assert _fallback_size(fallback) == tuple(shutil.get_terminal_size(fallback))
