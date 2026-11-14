"""tools/install.sh and tools/uninstall.sh, run hermetically.

Every run gets a temporary HOME, a copy of the repo's scripts (so the venv
method can never touch the real .venv) and a PATH made only of symlinked
system tools plus fakes for asciifx / pipx / uv / python that log what they
were asked to do. Nothing here touches the real HOME or the network; the two
integration tests at the end run the real CLI from src/ (no install).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None, reason="POSIX shell scripts"
)

# Everything install.sh / uninstall.sh and the fakes call. Missing ones (zsh,
# diff, ...) are simply left out; the scripts cope.
TOOLS = (
    "sh", "awk", "sed", "grep", "cat", "mktemp", "dirname", "basename", "uname", "date", "cp",
    "mv", "rm", "mkdir", "ln", "readlink", "cmp", "diff", "head", "id", "stty", "env", "zsh",
    "bash",
)
HAS_ZSH = shutil.which("zsh") is not None
HAS_BASH = shutil.which("bash") is not None
START = re.compile(r"^# >>> asciifx (.+?) >>>$", re.M)

FAKE_ASCIIFX = r"""#!/bin/sh
# Fake asciifx: logs its arguments, prints shell-init blocks like the real one.
echo "asciifx $*" >>"$FAKE_LOG"
case $1 in
--help) echo "usage: asciifx [-h] COMMAND ..."; exit 0 ;;
img) exit 0 ;;
startup) echo "startup ${COLUMNS}x${LINES}" >>"$FAKE_LOG"; exit 0 ;;
shell-init) ;;
*) echo "fake asciifx: unexpected command $1" >&2; exit 2 ;;
esac
shift; kind=$1; shift
logo=""; name=""; banner=1; idle=0
while [ $# -gt 0 ]; do
    case $1 in
    --logo) logo=$2; shift 2 ;;
    --name)
        if [ -n "${FAKE_LEGACY:-}" ]; then
            echo "asciifx: error: unrecognized arguments: --name $2" >&2; exit 2
        fi
        name=$2; shift 2 ;;
    --no-banner) banner=0; shift ;;
    --style) shift 2 ;;
    --screensaver-after) idle=$2; shift 2 ;;
    *) echo "fake asciifx: bad argument $1" >&2; exit 2 ;;
    esac
done
if [ "$kind" = tmux ]; then
    printf '# >>> asciifx screensaver >>>\nset -g lock-after-time %s\nset -g lock-command "asciifx screensaver"\n# <<< asciifx screensaver <<<\n' "$idle"
    exit 0
fi
if [ "$kind" = bash ] && [ -n "${FAKE_LEGACY:-}" ]; then
    echo "asciifx shell-init: error: argument shell: invalid choice: 'bash' (choose from zsh, tmux)" >&2; exit 2
fi
printf '# >>> asciifx logo >>>\nexport ASCIIFX_LOGO=%s\n# <<< asciifx logo <<<\n' "$logo"
if [ -n "$name" ]; then
    printf "\n# >>> asciifx name >>>\nexport ASCIIFX_NAME='%s'\n# <<< asciifx name <<<\n" "$name"
fi
if [ "$banner" = 1 ]; then
    printf '\n# >>> asciifx startup banner >>>\nif [ -t 1 ]; then asciifx startup; fi\n# <<< asciifx startup banner <<<\n'
fi
if [ "$kind" = zsh ] && [ "$idle" -gt 0 ]; then
    printf '\n# >>> asciifx screensaver >>>\nTMOUT=%s\nTRAPALRM() { asciifx screensaver; }\n# <<< asciifx screensaver <<<\n' "$idle"
fi
if [ -n "${FAKE_BROKEN:-}" ]; then
    printf '\n# >>> asciifx broken >>>\nif then fi (\n# <<< asciifx broken <<<\n'
fi
"""

# pipx: links ~/.local/bin/asciifx into its venv, but (like the real one) won't
# replace an existing file there.
FAKE_PIPX = r"""#!/bin/sh
echo "pipx $*" >>"$FAKE_LOG"
bin=$HOME/.local/bin
venvs=$HOME/.local/share/pipx/venvs
case $1 in
environment)
    case $3 in PIPX_BIN_DIR) echo "$bin" ;; PIPX_LOCAL_VENVS) echo "$venvs" ;; esac
    exit 0 ;;
install)
    mkdir -p "$venvs/asciifx/bin" "$bin"
    cp "$FAKE_ASCIIFX" "$venvs/asciifx/bin/asciifx"
    if [ -e "$bin/asciifx" ] || [ -L "$bin/asciifx" ]; then
        echo "File exists at $bin/asciifx, not modifying"
    else
        ln -s "$venvs/asciifx/bin/asciifx" "$bin/asciifx"
    fi
    exit 0 ;;
list) if [ -d "$venvs/asciifx" ]; then echo "asciifx 0.1.0"; fi; exit 0 ;;
uninstall) rm -rf "$venvs/asciifx"; rm -f "$bin/asciifx"; exit 0 ;;
esac
exit 1
"""

# uv: `tool install --force` overwrites whatever is at ~/.local/bin/asciifx.
FAKE_UV = r"""#!/bin/sh
echo "uv $*" >>"$FAKE_LOG"
bin=$HOME/.local/bin
tools=$HOME/.local/share/uv/tools
case "$1 $2" in
"tool dir") if [ "${3:-}" = --bin ]; then echo "$bin"; else echo "$tools"; fi; exit 0 ;;
"tool install")
    mkdir -p "$tools/asciifx/bin" "$bin"
    cp "$FAKE_ASCIIFX" "$tools/asciifx/bin/asciifx"
    rm -f "$bin/asciifx"
    ln -s "$tools/asciifx/bin/asciifx" "$bin/asciifx"
    exit 0 ;;
"tool list") if [ -d "$tools/asciifx" ]; then echo "asciifx v0.1.0"; fi; exit 0 ;;
"tool uninstall") rm -rf "$tools/asciifx"; rm -f "$bin/asciifx"; exit 0 ;;
esac
exit 1
"""

# python: the real interpreter for version checks; `-m venv` / `-m pip` faked.
FAKE_PYTHON = r"""#!/bin/sh
if [ "${1:-}" = -m ] && [ "${2:-}" = venv ]; then
    echo "python -m venv $3" >>"$FAKE_LOG"
    mkdir -p "$3/bin"
    cp "$FAKE_PYTHON" "$3/bin/python"
    cp "$FAKE_ASCIIFX" "$3/bin/asciifx"
    exit 0
fi
if [ "${1:-}" = -m ] && [ "${2:-}" = pip ]; then echo "python $*" >>"$FAKE_LOG"; exit 0; fi
case "$*" in *ensurepip*) exit 0 ;; esac  # venv support is faked, so claim it
exec "$REAL_PYTHON" "$@"
"""

OLD_PYTHON = r"""#!/bin/sh
case "$*" in *print*) echo 3.11.9; exit 0 ;; esac
exit 1
"""

USER_RC = "# my rc\nexport EDITOR=vim\nalias ll='ls -l'\n"


def _script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.log = root / "fake.log"
        self.log.touch()
        # A repo copy: the venv method then works on <copy>/.venv, never the real one.
        self.repo = root / "repo"
        (self.repo / "tools").mkdir(parents=True)
        for name in ("install.sh", "uninstall.sh"):
            shutil.copy(REPO / "tools" / name, self.repo / "tools" / name)
        shutil.copy(REPO / "pyproject.toml", self.repo)
        shutil.copy(REPO / "skull_logo.png", self.repo)
        (self.repo / "src").symlink_to(REPO / "src")  # read-only use (dry runs)
        self.tools = root / "toolbin"
        self.tools.mkdir()
        for tool in TOOLS:
            if found := shutil.which(tool):
                (self.tools / tool).symlink_to(found)
        fakes = root / "fakes"
        self.asciifx = _script(fakes / "asciifx-bin" / "asciifx", FAKE_ASCIIFX)
        _script(fakes / "pipx-bin" / "pipx", FAKE_PIPX)
        _script(fakes / "uv-bin" / "uv", FAKE_UV)
        _script(fakes / "old-python-bin" / "python3", OLD_PYTHON)
        self.python = _script(fakes / "python", FAKE_PYTHON)
        self.fakes = fakes
        (root / "tmp").mkdir()

    @property
    def rc(self) -> Path:
        return self.home / ".zshrc"

    def env(self, path: tuple[str, ...], shell: str, **extra: str) -> dict[str, str]:
        dirs = [str(self.fakes / p) for p in path] + [str(self.tools)]
        return {
            "HOME": str(self.home), "USER": "tester", "SHELL": shell, "PATH": ":".join(dirs),
            "TMPDIR": str(self.root / "tmp"), "LANG": "C.UTF-8", "TERM": "dumb",
            "PYTHONDONTWRITEBYTECODE": "1", "FAKE_LOG": str(self.log),
            "FAKE_ASCIIFX": str(self.asciifx), "FAKE_PYTHON": str(self.python),
            "REAL_PYTHON": sys.executable, **extra,
        }

    def run(self, script: str, *args: str, path: tuple[str, ...] = (), shell: str = "/bin/zsh",
            method: str | None = None, interp: str = "sh", **extra: str) -> subprocess.CompletedProcess[str]:
        if method is not None:
            extra["ASCIIFX_INSTALL_METHOD"] = method
        cmd = [shutil.which(interp) or interp, str(self.repo / "tools" / script), *args]
        return subprocess.run(cmd, env=self.env(path, shell, **extra), cwd=self.root, text=True,
                              capture_output=True, timeout=120)

    def install(self, *args: str, path: tuple[str, ...] = ("asciifx-bin",), method: str = "none",
                **kw) -> subprocess.CompletedProcess[str]:
        return self.run("install.sh", "--python", str(self.python), *args, path=path, method=method, **kw)

    def uninstall(self, *args: str, path: tuple[str, ...] = (), **kw) -> subprocess.CompletedProcess[str]:
        return self.run("uninstall.sh", *args, path=path, **kw)

    def backups(self, name: str = ".zshrc") -> list[Path]:
        return sorted(self.home.glob(f"{name}.bak-asciifx-*"))

    def logged(self) -> str:
        return self.log.read_text()

    def files(self) -> dict[str, bytes | str]:
        """Everything under HOME and the repo copy (to prove a dry run changed nothing)."""
        out: dict[str, bytes | str] = {}
        for base in (self.home, self.repo):
            for top, dirs, names in os.walk(base):  # doesn't descend into symlinked dirs
                for name in dirs + names:
                    p = Path(top, name)
                    if p.is_symlink():
                        out[str(p)] = "-> " + os.readlink(p)
                    elif p.is_file():
                        out[str(p)] = p.read_bytes()
                    else:
                        out[str(p)] = "dir"
        return out


def blocks(text: str) -> list[str]:
    return START.findall(text)


def ok(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: " in result.stdout and "FAILED" not in result.stdout
    return result.stdout


def syntax_check(shell: str, path: Path) -> None:
    flags = ["-f", "-n"] if shell == "zsh" else ["-n"]
    r = subprocess.run([shell, *flags, str(path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


@pytest.fixture
def sb(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


# -- install.sh --------------------------------------------------------------------------


def test_help_and_usage_errors(sb: Sandbox) -> None:
    r = sb.run("install.sh", "--help")
    assert r.returncode == 0 and "--screensaver-after" in r.stdout and "--dry-run" in r.stdout
    for bad in (["--bogus"], ["--screensaver-after", "soon"], ["--shell", "fish"],
                ["--method", "brew"], ["--logo", str(sb.root / "missing.png")], ["--name", ""]):
        r = sb.install(*bad)
        assert r.returncode == 2, (bad, r.stdout, r.stderr)
    assert not sb.rc.exists()


@pytest.mark.parametrize("interp", ["sh", "bash"])
def test_blocks_added_once_when_run_twice(sb: Sandbox, interp: str) -> None:
    if shutil.which(interp) is None:
        pytest.skip(f"no {interp}")
    sb.rc.write_text(USER_RC)
    out = ok(sb.install("--name", "Nam Viper", interp=interp))
    text = sb.rc.read_text()
    assert text.startswith(USER_RC + "\n# >>> asciifx logo >>>\n")
    assert blocks(text) == ["logo", "name", "startup banner", "screensaver"]
    assert "export ASCIIFX_NAME='Nam Viper'" in text
    assert "TMOUT=120" in text
    assert [b.read_text() for b in sb.backups()] == [USER_RC]
    assert "--name Nam Viper" in sb.logged()
    assert "startup 80x24" in sb.logged() and "startup 200x50" in sb.logged()  # cache pre-warm
    assert "RESULT: OK" in out

    out = ok(sb.install("--name", "Nam Viper", interp=interp))
    assert sb.rc.read_text() == text
    assert "already up to date" in out
    assert len(sb.backups()) == 1  # an unchanged file gets no new backup
    if HAS_ZSH:
        syntax_check("zsh", sb.rc)


def test_old_blocks_replaced_and_everything_else_kept(sb: Sandbox) -> None:
    original = (
        "# top\nexport A=1\n\n"
        "# >>> asciifx logo >>>\nexport ASCIIFX_LOGO=/old/logo.png\n# <<< asciifx logo <<<\n\n"
        "# >>> asciifx startup banner >>>\nasciifx startup --logo /old/logo.png\n# <<< asciifx startup banner <<<\n\n"
        "alias ll='ls -l'\n"
        "# >>> conda initialize >>>\nconda stuff\n# <<< conda initialize <<<\n"
        "  # >>> asciifx something-older >>>\n  echo old\n  # <<< asciifx something-older <<<\n\n\n"
    )
    sb.rc.write_text(original)
    out = ok(sb.install("--no-banner", "--screensaver-after", "0"))
    text = sb.rc.read_text()
    assert "/old/logo.png" not in text and "echo old" not in text
    assert text.startswith("# top\nexport A=1\n\nalias ll='ls -l'\n# >>> conda initialize >>>\n"
                           "conda stuff\n# <<< conda initialize <<<\n\n# >>> asciifx logo >>>\n")
    assert blocks(text) == ["logo", "name"]
    assert "removed 3 old asciifx block(s), added 2" in out
    assert [b.read_text() for b in sb.backups()] == [original]


def test_symlinked_rc_stays_a_symlink(sb: Sandbox) -> None:
    target = sb.home / "dotfiles" / "zshrc"
    target.parent.mkdir()
    target.write_text(USER_RC)
    sb.rc.symlink_to(target)
    ok(sb.install())
    assert sb.rc.is_symlink() and sb.rc.resolve() == target.resolve()
    assert "# >>> asciifx logo >>>" in target.read_text()


@pytest.mark.parametrize("damage", [
    "# >>> asciifx logo >>>\nexport ASCIIFX_LOGO=/x\n",            # never closed
    "export X=1\n# <<< asciifx logo <<<\n",                         # end without start
    "# >>> asciifx a >>>\n# >>> asciifx b >>>\n# <<< asciifx b <<<\n",  # nested
])
def test_unbalanced_markers_leave_rc_untouched(sb: Sandbox, damage: str) -> None:
    original = USER_RC + damage + "alias after='kept'\n"
    sb.rc.write_text(original)
    r = sb.install()
    assert r.returncode == 1
    assert "unbalanced asciifx markers" in r.stderr
    assert sb.rc.read_text() == original
    assert sb.backups() == []
    r = sb.uninstall()
    assert r.returncode == 1 and sb.rc.read_text() == original


def test_broken_generated_blocks_never_written(sb: Sandbox) -> None:
    if not HAS_ZSH:
        pytest.skip("no zsh")
    sb.rc.write_text(USER_RC)
    r = sb.install(FAKE_BROKEN="1")
    assert r.returncode == 1 and "fail 'zsh -n'" in r.stderr
    assert sb.rc.read_text() == USER_RC and sb.backups() == []


def test_preexisting_syntax_error_is_reported_not_blamed(sb: Sandbox) -> None:
    if not HAS_ZSH:
        pytest.skip("no zsh")
    broken = USER_RC + "if [[ -n $X ]]; then\n  echo unterminated\n"
    sb.rc.write_text(broken)
    out = ok(sb.install())
    assert "already failed 'zsh -n' before this change" in out
    assert sb.rc.read_text().startswith(broken)
    assert [b.read_text() for b in sb.backups()] == [broken]


def test_dry_run_changes_nothing(sb: Sandbox) -> None:
    sb.rc.write_text(USER_RC)
    link = sb.home / ".local" / "bin" / "asciifx"
    link.parent.mkdir(parents=True)
    link.symlink_to(sb.root / "old" / ".venv" / "bin" / "asciifx")
    before = sb.files()
    # Real source (src/ is a symlink to the checkout) renders the blocks, read-only.
    out = ok(sb.install("--dry-run", "--name", "Ann", "--tmux-lock-after", "300", method="venv"))
    assert sb.files() == before
    assert not (sb.repo / ".venv").exists()
    assert "would run: " in out and "-m venv" in out and "-m pip install" in out
    assert "would replace the symlink ~/.local/bin/asciifx" in out
    assert "+# >>> asciifx logo >>>" in out and "+export ASCIIFX_NAME=Ann" in out
    assert "+set -g lock-after-time 300" in out
    assert "RESULT: DRY RUN OK" in out
    assert sb.logged() == ""


def test_no_shell_leaves_rc_alone(sb: Sandbox) -> None:
    sb.rc.write_text(USER_RC)
    out = ok(sb.install("--no-shell"))
    assert sb.rc.read_text() == USER_RC and sb.backups() == []
    assert "skipped (--no-shell)" in out


def test_bash_gets_no_screensaver(sb: Sandbox) -> None:
    out = ok(sb.install("--name", "Bo", shell="/bin/bash"))
    rc = sb.home / ".bashrc"
    assert blocks(rc.read_text()) == ["logo", "name", "startup banner"]
    assert "--screensaver-after" not in sb.logged()
    assert "zsh-only" in out
    assert not sb.rc.exists()
    if HAS_BASH:
        syntax_check("bash", rc)


def test_old_cli_without_name_or_bash(sb: Sandbox) -> None:
    sb.rc.write_text(USER_RC)
    out = ok(sb.install("--name", "Ann", FAKE_LEGACY="1"))
    assert "no 'shell-init --name'" in out
    assert blocks(sb.rc.read_text()) == ["logo", "startup banner", "screensaver"]
    r = sb.install(shell="/bin/bash", FAKE_LEGACY="1")
    assert r.returncode == 1 and "cannot write bash blocks" in r.stderr
    assert not (sb.home / ".bashrc").exists()


def test_tmux_block_added_then_removed(sb: Sandbox) -> None:
    tmux = sb.home / ".tmux.conf"
    tmux.write_text("set -g mouse on\n")
    ok(sb.install("--tmux-lock-after", "300"))
    assert blocks(tmux.read_text()) == ["screensaver"]
    assert "lock-after-time 300" in tmux.read_text()
    ok(sb.install("--tmux-lock-after", "300"))
    assert blocks(tmux.read_text()) == ["screensaver"]
    ok(sb.install())  # no flag: ~/.tmux.conf is left alone
    assert blocks(tmux.read_text()) == ["screensaver"]
    ok(sb.install("--tmux-lock-after", "0"))
    assert tmux.read_text() == "set -g mouse on\n"


def test_no_python_prints_install_hints(sb: Sandbox) -> None:
    r = sb.run("install.sh", path=("old-python-bin",), method="auto")
    assert r.returncode == 1
    assert "Python >= 3.12 not found" in r.stderr and "python3=3.11.9" in r.stderr
    assert "How to get Python >= 3.12" in r.stderr and "uv" in r.stderr
    r = sb.install("--python", str(sb.fakes / "old-python-bin" / "python3"))
    assert r.returncode == 1 and "needs >= 3.12" in r.stderr


@pytest.mark.parametrize("method", ["venv", "pipx", "uv"])
def test_regular_file_in_local_bin_is_never_clobbered(sb: Sandbox, method: str) -> None:
    mine = sb.home / ".local" / "bin" / "asciifx"
    mine.parent.mkdir(parents=True)
    mine.write_text("#!/bin/sh\necho mine\n")
    sb.rc.write_text(USER_RC)
    r = sb.install(path=("pipx-bin", "uv-bin"), method=method)
    assert r.returncode == 1
    assert "regular file, not a symlink" in r.stderr
    assert mine.read_text() == "#!/bin/sh\necho mine\n" and not mine.is_symlink()
    assert " install" not in sb.logged() and "venv" not in sb.logged()
    assert sb.rc.read_text() == USER_RC
    r = sb.uninstall(path=("pipx-bin", "uv-bin"))
    assert r.returncode == 0 and mine.read_text() == "#!/bin/sh\necho mine\n"
    assert "regular file" in r.stdout


def test_auto_prefers_pipx_and_replaces_an_old_venv_symlink(sb: Sandbox) -> None:
    link = sb.home / ".local" / "bin" / "asciifx"
    link.parent.mkdir(parents=True)
    link.symlink_to(sb.root / "elsewhere" / ".venv" / "bin" / "asciifx")
    out = ok(sb.install("--video", path=("pipx-bin", "uv-bin"), method="auto"))
    assert "method: pipx" in out
    assert f"pipx install --force --python {sb.python} -e {sb.repo}[video]" in sb.logged()
    assert os.readlink(link) == str(sb.home / ".local/share/pipx/venvs/asciifx/bin/asciifx")
    assert blocks(sb.rc.read_text()) == ["logo", "name", "startup banner", "screensaver"]


def test_uv_method(sb: Sandbox) -> None:
    ok(sb.install(path=("uv-bin",), method="auto"))
    assert f"uv tool install --force --python {sb.python} -e {sb.repo}\n" in sb.logged()
    link = sb.home / ".local" / "bin" / "asciifx"
    assert os.readlink(link) == str(sb.home / ".local/share/uv/tools/asciifx/bin/asciifx")


def test_venv_method_creates_venv_and_links(sb: Sandbox) -> None:
    out = ok(sb.install(method="venv", path=()))
    venv = sb.repo / ".venv"
    assert f"python -m venv {venv}" in sb.logged()
    assert f"python -m pip install --quiet --disable-pip-version-check -e {sb.repo}" in sb.logged()
    link = sb.home / ".local" / "bin" / "asciifx"
    assert os.readlink(link) == str(venv / "bin" / "asciifx")
    assert "is not on PATH" in out  # ~/.local/bin isn't on this PATH
    out = ok(sb.install(method="venv", path=()))
    assert "reusing" in out and "already links" in out
    assert sb.logged().count("-m venv") == 1


# -- uninstall.sh ------------------------------------------------------------------------


def test_uninstall_removes_blocks_symlink_and_cache(sb: Sandbox) -> None:
    sb.rc.write_text(USER_RC)
    ok(sb.install(method="venv", path=()))
    (sb.home / ".tmux.conf").write_text("# >>> asciifx screensaver >>>\nset -g x 1\n# <<< asciifx screensaver <<<\n")
    cache = sb.home / ".cache" / "asciifx"
    cache.mkdir(parents=True)
    (cache / "logo-x.json").write_text("{}")
    before = sb.files()
    ok(sb.uninstall("--dry-run"))
    assert sb.files() == before

    out = ok(sb.uninstall("--purge-cache"))
    assert sb.rc.read_text() == USER_RC
    assert (sb.home / ".tmux.conf").read_text() == ""
    assert not (sb.home / ".local" / "bin" / "asciifx").exists()
    assert not cache.exists()
    assert len(sb.backups()) == 2  # one from install, one from uninstall
    assert (sb.repo / ".venv").is_dir()  # the repo's venv is kept
    out = ok(sb.uninstall())  # again: nothing left to do
    assert "no asciifx blocks" in out and "no installed asciifx command found" in out
    assert len(sb.backups()) == 2


def test_uninstall_uses_pipx(sb: Sandbox) -> None:
    ok(sb.install(path=("pipx-bin",), method="pipx"))
    ok(sb.uninstall(path=("pipx-bin",)))
    assert "pipx uninstall asciifx" in sb.logged()
    assert not (sb.home / ".local" / "bin" / "asciifx").exists()
    assert blocks(sb.rc.read_text()) == []


# -- with the real CLI (from src/, nothing installed) -------------------------------------


def _real_asciifx(sb: Sandbox) -> None:
    _script(sb.asciifx, f'#!/bin/sh\nPYTHONPATH="{REPO / "src"}" exec "{sys.executable}" -m asciifx "$@"\n')


@pytest.mark.skipif(not HAS_ZSH, reason="no zsh")
def test_real_cli_zsh_blocks(sb: Sandbox) -> None:
    _real_asciifx(sb)
    sb.rc.write_text(USER_RC)
    ok(sb.install("--name", "Nam Viper"))
    text = sb.rc.read_text()
    assert blocks(text) == ["logo", "name", "startup banner", "screensaver"]
    assert "export ASCIIFX_NAME='Nam Viper'" in text
    syntax_check("zsh", sb.rc)
    assert list((sb.home / ".cache" / "asciifx").glob("logo-*.json"))  # pre-warmed
    out = ok(sb.install("--name", "Nam Viper"))
    assert "already up to date" in out and sb.rc.read_text() == text


@pytest.mark.skipif(not HAS_BASH, reason="no bash")
def test_real_cli_bash_blocks(sb: Sandbox) -> None:
    _real_asciifx(sb)
    ok(sb.install("--name", "Ann", shell="/bin/bash"))
    rc = sb.home / ".bashrc"
    assert blocks(rc.read_text()) == ["logo", "name", "startup banner"]
    syntax_check("bash", rc)
