"""Regressions from the final review (part A): shell snippets, terminal modes, Ctrl-C at startup."""

import os
import shutil
import signal
import subprocess
import sys
import termios
import textwrap
import time
from pathlib import Path

import pytest

from asciifx.cli import main
from asciifx.core import Grid
from asciifx.term import ColorMode, Terminal
from asciifx.term.ansi import ENTER, LEAVE

TC = ColorMode.TRUECOLOR
SRC = Path(__file__).resolve().parent.parent / "src"


def wait_for(condition, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not (result := condition()):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)
    return result


# -- shell snippets, run for real ------------------------------------------------------


def snippet(capsys, *args: str) -> str:
    assert main(["shell-init", *args]) == 0
    return capsys.readouterr().out


def run_shell(fake_tty, tmp_path, argv, script, env=None):
    """Run ``argv + [script]`` with the pty as stdin/stdout/stderr and a fake
    ``asciifx`` on PATH that logs its arguments. Returns (terminal output, log lines)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "asciifx.log"
    log.write_text("")
    fake = bin_dir / "asciifx"
    fake.write_text('#!/bin/sh\necho "$*" >> "$ASCIIFX_LOG"\n')
    fake.chmod(0o755)
    base = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "ASCIIFX_LOG": str(log),
            "TERM": "xterm-256color", "LANG": "C.UTF-8"}
    base.update(env or {})
    n = len(fake_tty.output(settle=0.0))
    proc = subprocess.run([*argv, script], stdin=fake_tty.slave, stdout=fake_tty.slave,
                          stderr=fake_tty.slave, env=base, cwd=tmp_path, timeout=30)
    assert proc.returncode == 0
    return fake_tty.output()[n:], log.read_text().splitlines()


ZSH = ["zsh", "-f", "-i", "-c"]  # -f: none of this machine's startup files; -i: interactive
needs_zsh = pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
needs_bash = pytest.mark.skipif(not shutil.which("bash"), reason="bash not installed")


@needs_zsh
def test_zsh_screensaver_with_a_readonly_tmout_leaves_the_rest_of_zshrc_alone(capsys, fake_tty, tmp_path):
    """Hardened systems export a readonly TMOUT. Assigning it failed ("read-only
    variable: TMOUT") on every new shell and aborted the rest of ~/.zshrc."""
    rc = snippet(capsys, "zsh", "--screensaver-after", "120")
    out, _ = run_shell(fake_tty, tmp_path, ZSH, "typeset -rx TMOUT=900\n" + rc + "print -r -- END TMOUT=$TMOUT\n")
    assert "read-only" not in out
    assert "END TMOUT=900" in out  # reached, and the admin's timeout is untouched


@needs_zsh
@needs_bash
def test_zsh_screensaver_never_exports_its_short_timeouts(capsys, fake_tty, tmp_path):
    """With TMOUT already in the environment the snippet's TMOUT (re-armed to a
    few seconds while waiting) was exported: a nested bash logged out after that."""
    rc = snippet(capsys, "zsh", "--screensaver-after", "120")
    script = rc + "print -r -- ZSH TMOUT=$TMOUT; bash --norc --noprofile -c 'echo CHILD TMOUT=${TMOUT-unset}'\n"
    out, _ = run_shell(fake_tty, tmp_path, ZSH, script, env={"TMOUT": "900"})
    assert "ZSH TMOUT=120" in out  # the screensaver is armed
    assert "CHILD TMOUT=unset" in out


@needs_zsh
@needs_bash
@pytest.mark.parametrize("term", ["dumb", ""])
def test_snippets_stay_quiet_on_dumb_terminals(capsys, fake_tty, tmp_path, term):
    """Emacs M-x shell & co.: a pty, but TERM=dumb shows escape sequences as text
    (and can't send a lone key to end a screensaver). Unset TERM is as bad."""
    for shell, argv in (("zsh", ZSH), ("bash", ["bash", "--norc", "--noprofile", "-i", "-c"])):
        rc = snippet(capsys, shell, "--screensaver-after", "120")
        probe = "print -r -- TRAP=${+functions[TRAPALRM]}\n" if shell == "zsh" else "echo TRAP=none\n"
        _, log = run_shell(fake_tty, tmp_path, argv, rc + probe, env={"TERM": term})
        assert log == [], shell
        out, log = run_shell(fake_tty, tmp_path, argv, rc + probe)  # a real terminal: still on
        assert [line.split()[0] for line in log] == ["startup"], shell
        assert shell == "bash" or "TRAP=1" in out


# -- terminal modes: Ctrl-S ----------------------------------------------------------------


def test_session_turns_off_xon_xoff_and_restores_it(fake_tty):
    """With IXON on, Ctrl-S stops the tty's output: the next frame's write blocks,
    no key is read any more, and the screensaver/banner looks hung."""
    before = termios.tcgetattr(fake_tty.slave)
    assert before[0] & termios.IXON  # a fresh pty has flow control on, like a real terminal
    with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC) as term:
        assert not termios.tcgetattr(fake_tty.slave)[0] & termios.IXON
        term.render(Grid.from_text("x"))
    assert termios.tcgetattr(fake_tty.slave) == before


def test_flow_control_stays_off_after_ctrl_z(fake_tty):
    old = signal.signal(signal.SIGTSTP, signal.SIG_DFL)  # the handler is only installed over SIG_DFL
    try:
        with Terminal(fake_tty.out, fake_tty.inp, color_mode=TC) as term:
            seen = []
            term._suspend_process = lambda: seen.append(termios.tcgetattr(fake_tty.slave)[0])
            signal.raise_signal(signal.SIGTSTP)
            assert seen and seen[0] & termios.IXON  # the shell's modes while stopped
            assert not termios.tcgetattr(fake_tty.slave)[0] & termios.IXON  # and off again after `fg`
    finally:
        signal.signal(signal.SIGTSTP, old)


def test_ctrl_s_ends_the_animation_end_to_end(fake_tty):
    env = {k: v for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")}
    env.update(PYTHONPATH=str(SRC), COLORTERM="truecolor")
    proc = subprocess.Popen([sys.executable, "-m", "asciifx", "play", "bounce", "--duration", "30"],
                            stdin=fake_tty.slave, stdout=fake_tty.slave, stderr=subprocess.PIPE,
                            env=env, process_group=0)
    try:
        wait_for(lambda: len(fake_tty.output(settle=0.01)) > 300, "the first frames")
        fake_tty.send(b"\x13")  # Ctrl-S: a key like any other
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            fake_tty.send(b"\x11")  # Ctrl-Q, so the kill below can't hang on stopped output
            proc.kill()
            proc.wait()
        proc.stderr.close()
    out = fake_tty.output()
    assert out.startswith(ENTER) and out.endswith(LEAVE)


# -- Ctrl-C while asciifx starts (a new shell's banner) -------------------------------------


def test_ctrl_c_while_parsing_the_command_line_is_not_a_traceback(monkeypatch):
    import asciifx.cli as cli

    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "build_parser", interrupted)
    assert main(["startup"]) == 130


def test_ctrl_c_while_importing_the_cli_exits_130_quietly():
    """A KeyboardInterrupt traceback, and death by SIGINT, which makes zsh abort the rest of ~/.zshrc."""
    code = textwrap.dedent("""
        import sys
        class Interrupt:
            def find_spec(self, name, path=None, target=None):
                if name == "asciifx.api":
                    raise KeyboardInterrupt
        sys.meta_path.insert(0, Interrupt())
        from asciifx.cli import main
    """)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, PYTHONPATH=str(SRC)))
    assert (proc.returncode, proc.stderr) == (130, "")
