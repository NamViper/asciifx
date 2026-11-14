#!/bin/sh
# Install asciifx from this checkout and hook it into your shell.
#
#   tools/install.sh [options]            (tools/install.sh --help lists them)
#
# Steps (safe to re-run; each run converges to the same state):
#   1. find Python >= 3.12
#   2. install asciifx *editable* from this repo, so `git pull` updates it:
#      pipx if present, else uv, else <repo>/.venv + a symlink ~/.local/bin/asciifx
#   3. check that `asciifx` runs and can render the logo
#   4. pre-render the logo cache, so the first new shell starts fast
#   5. rewrite the asciifx blocks in ~/.zshrc or ~/.bashrc: back the file up,
#      remove every "# >>> asciifx ... >>>" ... "# <<< asciifx ... <<<" block,
#      append fresh ones from `asciifx shell-init`, syntax-check the result and
#      restore the backup if the check fails. Nothing outside those blocks changes.
#   6. print a summary and how to test
#
# Never uses sudo, never overwrites a regular file at ~/.local/bin/asciifx, never
# deletes anything it did not create. --dry-run changes nothing at all.
# Exit status: 0 ok (warnings possible), 1 failed, 2 bad usage.
#
# Environment (mainly for tests):
#   ASCIIFX_INSTALL_METHOD   default for --method (auto|pipx|uv|venv|none)

set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd -P)

usage() {
    cat <<EOF
Usage: tools/install.sh [options]

Installs the asciifx command from this repo (editable) and adds the startup
banner / screensaver blocks to your shell startup file. Safe to re-run.

Options:
  --logo PATH               logo image (default: $REPO/skull_logo.png)
  --name NAME               name for \`asciifx summon\`'s reveal (default: \$USER)
  --style STYLE             logo style for the banner (dots, sketch, outline, ...;
                            default: the CLI's own, dots)
  --screensaver-after SECS  zsh: screensaver after SECS idle at the prompt
                            (default 120; 0 disables). Not available in bash.
  --no-banner               no startup banner in new shells
  --video                   also install the [video] extra (bundled ffmpeg)
  --shell zsh|bash|auto     which startup file to edit (default auto, from \$SHELL)
  --tmux-lock-after SECS    also manage the screensaver block in ~/.tmux.conf
                            (tmux locks after SECS idle; 0 removes the block;
                            default: leave ~/.tmux.conf alone)
  --python PATH             use this Python (>= 3.12) instead of searching PATH
  --method auto|pipx|uv|venv|none
                            how to install (default auto: pipx, else uv, else venv;
                            none: use the asciifx already on PATH)
  --no-shell                install only; don't touch any shell startup file
  --dry-run                 show what would happen, change nothing
  -h, --help                show this help
EOF
}

# -- output helpers ---------------------------------------------------------------

NSTEPS=6
WARNINGS=0
DRY=0
TMPD=""
step() { printf '\n==> [%s/%s] %s\n' "$1" "$NSTEPS" "$2"; }
info() { printf '    %s\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }
warn() {
    WARNINGS=$((WARNINGS + 1))
    printf '    WARNING: %s\n' "$*"
    if [ -n "$TMPD" ]; then printf -- '- %s\n' "$*" >>"$TMPD/warnings"; fi
}
die() {
    printf '\nERROR: %s\n' "$*" >&2
    printf 'RESULT: FAILED\n'
    exit 1
}
usage_error() {
    printf 'install.sh: %s\n(run tools/install.sh --help)\n' "$*" >&2
    exit 2
}
quote() { # print $1, single-quoted if it isn't a plain word
    case $1 in
    '' | *[!A-Za-z0-9_./:=@%+,~-]*) printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")" ;;
    *) printf '%s' "$1" ;;
    esac
}
show_cmd() { # show a command the way it would be typed
    if [ "$DRY" -eq 1 ]; then printf '    would run:'; else printf '    $'; fi
    for a in "$@"; do printf ' '; quote "$a"; done
    printf '\n'
}
tilde() { # $HOME/x -> ~/x, for display
    case $1 in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac
}
home_var() { # $HOME/x -> $HOME/x literally, for lines to paste into a shell file
    case $1 in "$HOME"/*) printf '$HOME/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac
}

# -- arguments ----------------------------------------------------------------------

LOGO=$REPO/skull_logo.png
NAME=${USER:-$(id -un 2>/dev/null || echo user)}
STYLE=""
IDLE=120
BANNER=1
VIDEO=0
SHELL_KIND=auto
TMUX_AFTER=""
PY_OVERRIDE=""
METHOD=${ASCIIFX_INSTALL_METHOD:-auto}
NO_SHELL=0

need_value() { [ "$1" -ge 2 ] || usage_error "$2 needs a value"; }
while [ $# -gt 0 ]; do
    case $1 in
    --logo) need_value $# "$1"; LOGO=$2; shift 2 ;;
    --logo=*) LOGO=${1#*=}; shift ;;
    --name) need_value $# "$1"; NAME=$2; shift 2 ;;
    --name=*) NAME=${1#*=}; shift ;;
    --style) need_value $# "$1"; STYLE=$2; shift 2 ;;
    --style=*) STYLE=${1#*=}; shift ;;
    --screensaver-after) need_value $# "$1"; IDLE=$2; shift 2 ;;
    --screensaver-after=*) IDLE=${1#*=}; shift ;;
    --no-banner) BANNER=0; shift ;;
    --video) VIDEO=1; shift ;;
    --shell) need_value $# "$1"; SHELL_KIND=$2; shift 2 ;;
    --shell=*) SHELL_KIND=${1#*=}; shift ;;
    --tmux-lock-after) need_value $# "$1"; TMUX_AFTER=$2; shift 2 ;;
    --tmux-lock-after=*) TMUX_AFTER=${1#*=}; shift ;;
    --python) need_value $# "$1"; PY_OVERRIDE=$2; shift 2 ;;
    --python=*) PY_OVERRIDE=${1#*=}; shift ;;
    --method) need_value $# "$1"; METHOD=$2; shift 2 ;;
    --method=*) METHOD=${1#*=}; shift ;;
    --no-shell) NO_SHELL=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage_error "unknown option: $1" ;;
    esac
done

is_uint() { case $1 in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac; }
is_uint "$IDLE" || usage_error "--screensaver-after must be a whole number of seconds, got '$IDLE'"
[ -z "$TMUX_AFTER" ] || is_uint "$TMUX_AFTER" || usage_error "--tmux-lock-after must be a whole number of seconds, got '$TMUX_AFTER'"
[ -n "$NAME" ] || usage_error "--name must not be empty"
case $METHOD in auto | pipx | uv | venv | none) ;; *) usage_error "--method must be auto, pipx, uv, venv or none, got '$METHOD'" ;; esac
case $SHELL_KIND in auto | zsh | bash) ;; *) usage_error "--shell must be zsh, bash or auto, got '$SHELL_KIND'" ;; esac
case $STYLE in *[!a-z]*) usage_error "--style: unknown style '$STYLE'" ;; esac

[ -f "$REPO/pyproject.toml" ] && grep -q '^name = "asciifx"' "$REPO/pyproject.toml" ||
    usage_error "$REPO does not look like the asciifx repo (run this script from a clone: tools/install.sh)"

case $LOGO in "~/"*) LOGO=$HOME/${LOGO#"~/"} ;; esac
[ -f "$LOGO" ] || usage_error "logo not found: $LOGO"
[ -r "$LOGO" ] || usage_error "logo not readable: $LOGO"
LOGO=$(cd "$(dirname "$LOGO")" && pwd -P)/$(basename "$LOGO")

if [ "$NO_SHELL" -eq 0 ] && [ "$SHELL_KIND" = auto ]; then
    case ${SHELL:-} in
    */zsh | zsh) SHELL_KIND=zsh ;;
    */bash | bash) SHELL_KIND=bash ;;
    '') usage_error "\$SHELL is not set: pass --shell zsh or --shell bash (or --no-shell)" ;;
    *) usage_error "your shell ($SHELL) is not supported for the banner; pass --shell zsh|bash to edit that shell's file anyway, or --no-shell to only install the command" ;;
    esac
fi
case $SHELL_KIND in
zsh) RC=${ZDOTDIR:-$HOME}/.zshrc ;;
bash) RC=$HOME/.bashrc ;;
*) RC="" ;;
esac

TMPD=$(mktemp -d "${TMPDIR:-/tmp}/asciifx-install.XXXXXX")
trap 'rm -rf "$TMPD"' EXIT
trap 'exit 130' INT TERM
: >"$TMPD/warnings"

if [ "$DRY" -eq 1 ]; then
    printf 'asciifx installer: DRY RUN, nothing will be changed\n'
else
    printf 'asciifx installer\n'
fi
info "repo:  $REPO"
info "logo:  $LOGO"
info "name:  $NAME"
if [ "$NO_SHELL" -eq 1 ]; then
    info "shell: (--no-shell: startup files untouched)"
else
    info "shell: $SHELL_KIND ($(tilde "$RC"))"
fi

# -- 1. Python ------------------------------------------------------------------------

step 1 "Find Python >= 3.12"

py_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) and sys.platform != "win32" else 1)' \
        >/dev/null 2>&1
}
py_version() { "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "?"; }

python_hints() {
    os=other
    if [ "$(uname -s 2>/dev/null)" = Darwin ]; then
        os=macos
    elif [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        os_ids=$(. /etc/os-release && printf '%s %s' "${ID:-}" "${ID_LIKE:-}")
        case " $os_ids " in
        *" fedora "* | *" rhel "*) os=fedora ;;
        *" arch "*) os=arch ;;
        *" debian "* | *" ubuntu "*) os=debian ;;
        esac
    fi
    cat <<EOF

How to get Python >= 3.12 (then re-run tools/install.sh):
EOF
    case $os in
    debian) cat <<'EOF'
  Ubuntu 24.04+ / Debian 13+ (incl. WSL):
      sudo apt update && sudo apt install -y python3 python3-venv pipx
  Ubuntu 22.04 (its python3 is 3.10):
      sudo add-apt-repository -y ppa:deadsnakes/ppa
      sudo apt update && sudo apt install -y python3.12 python3.12-venv
  Debian 12 (its python3 is 3.11): use uv (below).
EOF
        ;;
    macos) cat <<'EOF'
  macOS (Homebrew, https://brew.sh):
      brew install python@3.12 pipx
EOF
        ;;
    fedora) cat <<'EOF'
  Fedora 39+:
      sudo dnf install -y python3 pipx
EOF
        ;;
    arch) cat <<'EOF'
  Arch:
      sudo pacman -S --needed python python-pipx
EOF
        ;;
    *) cat <<'EOF'
  Install Python 3.12 or newer with your package manager.
EOF
        ;;
    esac
    cat <<'EOF'
  Any OS, no sudo: install uv, which downloads Python 3.12 by itself:
      curl -LsSf https://astral.sh/uv/install.sh | sh
  Or point the installer at an interpreter:  tools/install.sh --python /path/to/python3.12
EOF
}

has_venv() { "$1" -c 'import venv, ensurepip' >/dev/null 2>&1; }

PY=""      # the Python to install with
PY_VENV="" # the first one that can also create venvs (Debian splits that off)
checked=""
if [ -n "$PY_OVERRIDE" ]; then
    command -v "$PY_OVERRIDE" >/dev/null 2>&1 || die "--python $PY_OVERRIDE: not found"
    PY=$(command -v "$PY_OVERRIDE")
    py_ok "$PY" || die "--python $PY is Python $(py_version "$PY"); asciifx needs >= 3.12 (POSIX, not Windows)"
    if has_venv "$PY"; then PY_VENV=$PY; fi
else
    for cand in python3.14 python3.13 python3.12 python3 python; do
        p=$(command -v "$cand" 2>/dev/null) || continue
        if py_ok "$p"; then
            [ -n "$PY" ] || PY=$p
            if [ -z "$PY_VENV" ] && has_venv "$p"; then PY_VENV=$p; fi
            [ -z "$PY_VENV" ] || break
        else
            checked="$checked $cand=$(py_version "$p")"
        fi
    done
fi

if [ -n "$PY" ]; then
    ok "$PY (Python $(py_version "$PY"))"
elif [ "$METHOD" != none ] && [ "$METHOD" != pipx ] && [ "$METHOD" != venv ] && command -v uv >/dev/null 2>&1; then
    info "no Python >= 3.12 on PATH (found:${checked:- none}); uv will download Python 3.12"
    METHOD=uv
elif [ "$METHOD" = none ]; then
    info "no Python >= 3.12 on PATH; not needed with --method none"
else
    printf '\nERROR: Python >= 3.12 not found (checked python3.14 python3.13 python3.12 python3 python;%s).\n' \
        "${checked:- none of them exist}" >&2
    python_hints >&2
    printf 'RESULT: FAILED\n'
    exit 1
fi

# -- 2. install -----------------------------------------------------------------------

if [ "$METHOD" = auto ]; then
    if command -v pipx >/dev/null 2>&1; then
        METHOD=pipx
    elif command -v uv >/dev/null 2>&1; then
        METHOD=uv
    else
        METHOD=venv
    fi
fi
step 2 "Install the asciifx command (method: $METHOD)"

SPEC=$REPO
[ "$VIDEO" -eq 0 ] || SPEC="$REPO[video]"
LOCAL_BIN=$HOME/.local/bin

# Make way for the tool's own link at $1/asciifx. A symlink we may move aside (it
# is put back if the install fails); a regular file we never touch. $2: the
# directory the tool's links point into (a link there is already the tool's).
MOVED_LINK=""
prepare_link() {
    link=$1/asciifx
    if [ -L "$link" ]; then
        target=$(readlink "$link" 2>/dev/null || echo "?")
        if [ -n "$2" ] && [ -e "$link" ]; then
            case $target in "$2"/*) return 0 ;; esac
        fi
        if [ "$DRY" -eq 1 ]; then
            info "would replace the symlink $(tilde "$link") -> $target"
        else
            info "replacing the symlink $(tilde "$link") -> $target"
            mv "$link" "$TMPD/old-link"
            MOVED_LINK=$link
        fi
    elif [ -e "$link" ]; then
        die "$link exists and is a regular file, not a symlink, so it is left alone.
It is probably an older 'pip install --user' of asciifx. Remove it yourself, e.g.
    python3 -m pip uninstall asciifx      (or: mv '$link' '$link.old')
then re-run tools/install.sh."
    fi
}
restore_link() {
    if [ -n "$MOVED_LINK" ] && [ ! -e "$MOVED_LINK" ] && [ ! -L "$MOVED_LINK" ]; then
        mv "$TMPD/old-link" "$MOVED_LINK" && info "put back the old symlink $(tilde "$MOVED_LINK")"
    fi
}

ASCIIFX_CMD=""
case $METHOD in
pipx)
    command -v pipx >/dev/null 2>&1 || die "--method pipx: pipx is not on PATH"
    [ -n "$PY" ] || die "pipx needs a Python >= 3.12 (none found); see step 1"
    BIN=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null) || BIN=${PIPX_BIN_DIR:-$LOCAL_BIN}
    VENVS=$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null) || VENVS=""
    prepare_link "$BIN" "$VENVS"
    set -- pipx install --force --python "$PY" -e "$SPEC"
    show_cmd "$@"
    if [ "$DRY" -eq 0 ] && ! "$@"; then
        restore_link
        die "pipx install failed (see its output above). Try again with --method venv."
    fi
    ASCIIFX_CMD=$BIN/asciifx
    [ "$DRY" -eq 1 ] || [ -x "$ASCIIFX_CMD" ] ||
        die "pipx finished but did not create $ASCIIFX_CMD (see its output above)"
    ;;
uv)
    command -v uv >/dev/null 2>&1 || die "--method uv: uv is not on PATH"
    BIN=$(uv tool dir --bin 2>/dev/null) || BIN=$LOCAL_BIN
    TOOLS=$(uv tool dir 2>/dev/null) || TOOLS=""
    prepare_link "$BIN" "$TOOLS"
    set -- uv tool install --force --python "${PY:-3.12}" -e "$SPEC"
    show_cmd "$@"
    if [ "$DRY" -eq 0 ] && ! "$@"; then
        restore_link
        die "uv tool install failed (see its output above). Try again with --method venv."
    fi
    ASCIIFX_CMD=$BIN/asciifx
    [ "$DRY" -eq 1 ] || [ -x "$ASCIIFX_CMD" ] ||
        die "uv finished but did not create $ASCIIFX_CMD (see its output above)"
    ;;
venv)
    [ -n "$PY" ] || die "the venv method needs a Python >= 3.12 (none found); see step 1"
    VENV=$REPO/.venv
    BIN=$LOCAL_BIN
    prepare_link "$BIN" "$VENV/bin"
    if [ -e "$VENV" ] || [ -L "$VENV" ]; then
        if [ -x "$VENV/bin/python" ] && py_ok "$VENV/bin/python"; then
            ok "reusing $VENV (Python $(py_version "$VENV/bin/python"))"
        else
            die "$VENV exists but is not a working Python >= 3.12 venv. Move it away and re-run:
    mv '$VENV' '$VENV.old' && tools/install.sh"
        fi
    else
        if [ -z "$PY_VENV" ]; then
            pyv=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
            die "$PY cannot create virtual environments (the venv/ensurepip module is missing).
On Ubuntu/Debian/WSL install it, then re-run:   sudo apt install -y python$pyv-venv
Or install pipx (sudo apt install -y pipx) or uv; the installer prefers them."
        fi
        if [ "$PY_VENV" != "$PY" ]; then
            info "$PY has no venv module (python3-venv not installed); using $PY_VENV"
        fi
        show_cmd "$PY_VENV" -m venv "$VENV"
        if [ "$DRY" -eq 0 ] && ! "$PY_VENV" -m venv "$VENV"; then
            rm -rf "$VENV" # created (partly) by this run
            die "could not create $VENV"
        fi
    fi
    set -- "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -e "$SPEC"
    show_cmd "$@"
    if [ "$DRY" -eq 0 ]; then
        "$@" || { restore_link; die "pip install failed (see its output above)"; }
        [ -x "$VENV/bin/asciifx" ] || { restore_link; die "pip did not create $VENV/bin/asciifx"; }
        mkdir -p "$BIN"
        if [ -L "$BIN/asciifx" ] && [ "$(readlink "$BIN/asciifx")" = "$VENV/bin/asciifx" ]; then
            ok "$(tilde "$BIN/asciifx") already links to $VENV/bin/asciifx"
        else
            [ ! -L "$BIN/asciifx" ] || rm -f "$BIN/asciifx"
            ln -s "$VENV/bin/asciifx" "$BIN/asciifx"
            ok "linked $(tilde "$BIN/asciifx") -> $VENV/bin/asciifx"
        fi
    elif [ -L "$BIN/asciifx" ] && [ "$(readlink "$BIN/asciifx")" = "$VENV/bin/asciifx" ]; then
        ok "$(tilde "$BIN/asciifx") already links to $VENV/bin/asciifx"
    else
        info "would link $(tilde "$BIN/asciifx") -> $VENV/bin/asciifx"
    fi
    ASCIIFX_CMD=$BIN/asciifx
    ;;
none)
    ASCIIFX_CMD=$(command -v asciifx 2>/dev/null) ||
        die "--method none: no asciifx on PATH. Install it first or use another --method."
    BIN=$(dirname "$ASCIIFX_CMD")
    info "not installing (--method none); using $ASCIIFX_CMD"
    ;;
esac
rm -f "$TMPD/old-link"
[ "$DRY" -eq 1 ] || ok "installed: $ASCIIFX_CMD"

case ":$PATH:" in
*":$BIN:"* | *":$BIN/:"*) ;;
*) warn "$(tilde "$BIN") is not on PATH here, so new shells may not find asciifx (the banner block then skips itself).
      Check with 'echo \$PATH' in a new terminal. If it's missing there too, add this line to $(tilde "${RC:-your shell rc}"), above the asciifx blocks:
      export PATH=\"$(home_var "$BIN"):\$PATH\"" ;;
esac
if [ "$DRY" -eq 0 ]; then
    found=$(command -v asciifx 2>/dev/null || true)
    if [ -n "$found" ] && [ "$found" != "$ASCIIFX_CMD" ]; then
        warn "'asciifx' on PATH is $found, not $ASCIIFX_CMD (an older install?); that one runs first"
    fi
fi

# -- 3. check -------------------------------------------------------------------------

step 3 "Check the installed command"
if [ "$DRY" -eq 1 ]; then
    show_cmd "$(tilde "$ASCIIFX_CMD")" --help
    show_cmd "$(tilde "$ASCIIFX_CMD")" img "$LOGO" --width 24 --no-color
else
    "$ASCIIFX_CMD" --help >/dev/null 2>"$TMPD/err" ||
        die "'$ASCIIFX_CMD --help' failed:
$(cat "$TMPD/err")"
    ok "asciifx --help runs"
    "$ASCIIFX_CMD" img "$LOGO" --width 24 --no-color >/dev/null 2>"$TMPD/err" ||
        die "asciifx cannot render the logo $LOGO (is it a PNG/JPEG image?):
$(cat "$TMPD/err")"
    ok "the logo renders (numpy and Pillow work)"
fi

# -- 4. logo cache --------------------------------------------------------------------

step 4 "Pre-render the logo cache"
# The startup banner caches the converted logo per terminal size; warm it for this
# terminal (if there is one) and for small and large terminals.
SIZES="80x24 200x50"
tty_size=$( (stty size </dev/tty) 2>/dev/null || true)
case $tty_size in
[1-9]*" "[1-9]*) SIZES="${tty_size#* }x${tty_size% *} $SIZES" ;;
esac
CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/asciifx
if [ "$DRY" -eq 1 ]; then
    info "would run 'asciifx startup --duration 0' at $SIZES (cache: $(tilde "$CACHE"))"
else
    for size in $SIZES; do
        if [ -n "$STYLE" ]; then set -- --style "$STYLE"; else set --; fi
        COLUMNS=${size%x*} LINES=${size#*x} "$ASCIIFX_CMD" startup --duration 0 --logo "$LOGO" "$@" \
            >/dev/null 2>"$TMPD/err" </dev/null ||
            die "'asciifx startup' failed at ${size}:
$(cat "$TMPD/err")"
    done
    ok "cached for $SIZES in $(tilde "$CACHE")"
fi

# -- 5. shell startup file ------------------------------------------------------------

# Print $1 without any asciifx block (markers included) and without the blank
# lines right after a block or at the end of the file. Exit 3 on an unbalanced
# marker, so a damaged file is never truncated.
strip_blocks() {
    awk '
        function complain(msg) { print msg | "cat 1>&2"; bad = 1; exit 3 }
        /^[ \t]*# >>> asciifx.*>>>[ \t\r]*$/ {
            if (inblock) complain("line " NR ": new \"# >>> asciifx\" marker inside the block opened on line " start)
            inblock = 1; start = NR; next
        }
        /^[ \t]*# <<< asciifx.*<<<[ \t\r]*$/ {
            if (!inblock) complain("line " NR ": \"# <<< asciifx\" end marker without a start marker")
            inblock = 0; after = 1; next
        }
        inblock { next }
        /^[ \t\r]*$/ { if (!after) blank++; next }
        { while (blank > 0) { print ""; blank-- } after = 0; print }
        END {
            if (bad) exit 3
            if (inblock) { print "line " start ": \"# >>> asciifx\" block is never closed" | "cat 1>&2"; exit 3 }
        }' "$1"
}

syntax_ok() { # $1 shell kind, $2 file
    case $1 in
    zsh) if command -v zsh >/dev/null 2>&1; then zsh -f -n "$2"; else return 0; fi ;;
    bash) if command -v bash >/dev/null 2>&1; then bash -n "$2"; else return 0; fi ;;
    *) return 0 ;;
    esac
}

# Run the installed asciifx. A dry run hasn't installed anything, so it runs this
# checkout's source instead (read-only: no bytecode is written).
asciifx_run() {
    if [ "$DRY" -eq 1 ] && [ "$METHOD" != none ] && [ -n "$PY" ]; then
        PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$REPO/src "$PY" -m asciifx "$@"
    elif [ -x "$ASCIIFX_CMD" ]; then
        "$ASCIIFX_CMD" "$@"
    else
        echo "cannot run asciifx: $ASCIIFX_CMD is missing and there is no Python to run the source with" >&2
        return 1
    fi
}

USE_NAME=1
# Write `asciifx shell-init $1 ...` to $2.
render_blocks() {
    kind=$1
    out=$2
    set -- shell-init "$kind" --logo "$LOGO"
    if [ "$kind" = tmux ]; then
        set -- "$@" --screensaver-after "$TMUX_AFTER"
    else
        if [ "$BANNER" -eq 0 ]; then set -- "$@" --no-banner; fi
        if [ -n "$STYLE" ]; then set -- "$@" --style "$STYLE"; fi
        if [ "$kind" = zsh ] && [ "$IDLE" -gt 0 ]; then set -- "$@" --screensaver-after "$IDLE"; fi
        if [ "$USE_NAME" -eq 1 ]; then set -- "$@" --name "$NAME"; fi
    fi
    if asciifx_run "$@" >"$out" 2>"$TMPD/err"; then return 0; fi
    if [ "$USE_NAME" -eq 1 ] && grep -q 'unrecognized arguments: --name' "$TMPD/err"; then
        USE_NAME=0
        warn "this asciifx has no 'shell-init --name' yet, so ASCIIFX_NAME is not set (git pull, then re-run)"
        render_blocks "$kind" "$out"
        return
    fi
    if grep -q "invalid choice: '$kind'" "$TMPD/err"; then
        die "this asciifx version cannot write $kind blocks ('asciifx shell-init $kind' is unknown).
asciifx itself is installed; $(tilde "$RC") was not touched. Update the repo (git pull) and re-run,
or re-run with --no-shell."
    fi
    die "'asciifx $*' failed:
$(cat "$TMPD/err")"
}

# Replace the asciifx blocks in file $2 with the output of `shell-init $1`.
# Sets FILE_STATUS / FILE_BACKUP.
update_file() {
    kind=$1
    file=$2
    blocks=$TMPD/blocks.$kind
    new=$TMPD/new.$kind
    FILE_BACKUP=""
    if [ "$kind" = tmux ] && [ "$TMUX_AFTER" -eq 0 ]; then
        : >"$blocks" # --tmux-lock-after 0: just remove the block
    else
        render_blocks "$kind" "$blocks"
    fi
    if [ -s "$blocks" ] && ! head -n 1 "$blocks" | grep -q '^# >>> asciifx'; then
        die "unexpected output from 'asciifx shell-init $kind':
$(head -n 5 "$blocks")"
    fi
    if [ -s "$blocks" ] && ! syntax_ok "$kind" "$blocks" 2>"$TMPD/err"; then
        die "the generated $kind blocks fail '$kind -n'; $(tilde "$file") not touched:
$(cat "$TMPD/err")"
    fi

    if [ -e "$file" ]; then
        [ -r "$file" ] && [ -w "$file" ] || die "$file is not readable and writable"
        strip_blocks "$file" >"$TMPD/rest" 2>"$TMPD/err" ||
            die "$(tilde "$file") has unbalanced asciifx markers, so it was left untouched:
$(cat "$TMPD/err")
Fix or delete the broken '# >>> asciifx ... >>>' / '# <<< asciifx ... <<<' lines by hand, then re-run."
        nold=$(grep -c '^[[:space:]]*# >>> asciifx.*>>>' "$file" || true)
    else
        : >"$TMPD/rest"
        nold=0
    fi
    { cat "$TMPD/rest"; if [ -s "$TMPD/rest" ] && [ -s "$blocks" ]; then echo; fi; cat "$blocks"; } >"$new"
    nnew=$(grep -c '^# >>> asciifx.*>>>' "$blocks" || true)

    if grep -n 'asciifx' "$TMPD/rest" >"$TMPD/mentions"; then
        warn "lines outside the managed blocks of $(tilde "$file") mention asciifx and were left as they are
      (if the banner now runs twice, delete the duplicate yourself):
$(sed 's/^/        /' "$TMPD/mentions")"
    fi

    if [ ! -e "$file" ] && [ ! -s "$new" ]; then
        FILE_STATUS="no asciifx blocks, nothing to do"
        ok "$(tilde "$file"): $FILE_STATUS"
        return 0
    fi
    if [ -e "$file" ] && cmp -s "$file" "$new"; then
        FILE_STATUS="unchanged (already up to date, $nnew asciifx block(s))"
        ok "$(tilde "$file"): already up to date"
        return 0
    fi
    if command -v diff >/dev/null 2>&1; then
        if [ -e "$file" ]; then old=$file; else old=/dev/null; fi
        info "changes to $(tilde "$file"):"
        diff -u "$old" "$new" | sed -e '1,2d' -e 's/^/      /' || true
    fi
    if [ "$DRY" -eq 1 ]; then
        FILE_STATUS="would remove $nold old asciifx block(s) and add $nnew"
        info "would back up $(tilde "$file") and write the changes above"
        return 0
    fi

    if [ -e "$file" ]; then
        FILE_BACKUP=$file.bak-asciifx-$(date +%Y%m%d-%H%M%S)
        [ ! -e "$FILE_BACKUP" ] || FILE_BACKUP=$FILE_BACKUP-$$
        cp -p "$file" "$FILE_BACKUP"
        ok "backup: $(tilde "$FILE_BACKUP")"
        cat "$new" >"$file" # write through, so a symlinked dotfile stays a symlink
    else
        mkdir -p "$(dirname "$file")"
        cat "$new" >"$file"
    fi
    if ! syntax_ok "$kind" "$file" 2>"$TMPD/err"; then
        if [ -n "$FILE_BACKUP" ] && ! syntax_ok "$kind" "$FILE_BACKUP" 2>/dev/null; then
            warn "$(tilde "$file") already failed '$kind -n' before this change (not caused by asciifx):
$(sed 's/^/        /' "$TMPD/err")"
        else
            if [ -n "$FILE_BACKUP" ]; then cat "$FILE_BACKUP" >"$file"; else rm -f "$file"; fi
            die "$(tilde "$file") failed '$kind -n' after the change, so the original was restored:
$(cat "$TMPD/err")"
        fi
    fi
    FILE_STATUS="removed $nold old asciifx block(s), added $nnew"
    ok "$(tilde "$file"): $FILE_STATUS"
}

RC_STATUS="untouched (--no-shell)"
RC_BACKUP=""
TMUX_STATUS="untouched"
if [ "$NO_SHELL" -eq 1 ]; then
    step 5 "Shell startup file: skipped (--no-shell)"
else
    step 5 "Update $(tilde "$RC")"
    update_file "$SHELL_KIND" "$RC"
    RC_STATUS=$FILE_STATUS
    RC_BACKUP=$FILE_BACKUP
    if [ "$SHELL_KIND" = bash ] && [ "$IDLE" -gt 0 ]; then
        info "note: the idle screensaver is zsh-only (bash's TMOUT would close the shell);"
        info "      for bash use the tmux lock instead: --tmux-lock-after $IDLE"
    fi
    if [ "$SHELL_KIND" = bash ] && [ "$(uname -s 2>/dev/null)" = Darwin ] &&
        ! grep -qs 'bashrc' "$HOME/.bash_profile"; then
        warn "macOS bash login shells read ~/.bash_profile, which doesn't source ~/.bashrc; add:
      [ -r ~/.bashrc ] && . ~/.bashrc"
    fi
    if [ -n "$TMUX_AFTER" ]; then
        update_file tmux "$HOME/.tmux.conf"
        TMUX_STATUS=$FILE_STATUS
        if [ -n "$FILE_BACKUP" ]; then TMUX_STATUS="$TMUX_STATUS; backup $(tilde "$FILE_BACKUP")"; fi
        info "running tmux servers pick this up after: tmux source-file ~/.tmux.conf"
    fi
fi

# -- 6. summary -----------------------------------------------------------------------

step 6 "Summary"
info "method:      $METHOD"
info "command:     $ASCIIFX_CMD"
info "logo:        $LOGO"
if [ "$NO_SHELL" -eq 0 ]; then
    if [ "$USE_NAME" -eq 1 ]; then info "name:        $NAME (ASCIIFX_NAME)"; fi
    info "$(tilde "$RC"): $RC_STATUS"
    [ -z "$RC_BACKUP" ] || info "backup:      $(tilde "$RC_BACKUP")"
    if [ "$SHELL_KIND" = zsh ] && [ "$IDLE" -gt 0 ]; then
        info "screensaver: after $IDLE s idle at the zsh prompt"
    fi
    [ -z "$TMUX_AFTER" ] || info "~/.tmux.conf: $TMUX_STATUS"
fi
if [ "$WARNINGS" -gt 0 ]; then
    info ""
    info "warnings:"
    sed 's/^/    /' "$TMPD/warnings"
fi
if [ "$DRY" -eq 1 ]; then
    printf '\nRESULT: DRY RUN OK (nothing changed; warnings: %s)\n' "$WARNINGS"
    exit 0
fi
cat <<EOF

How to test:
  1. Open a NEW terminal: the banner plays (any key skips it).
  2. asciifx summon           # ~13 s logo show + name reveal
  3. asciifx screensaver      # full-screen screensaver; any key returns
EOF
if [ "$NO_SHELL" -eq 0 ] && [ "$SHELL_KIND" = zsh ] && [ "$IDLE" -gt 0 ]; then
    printf '  4. leave a new zsh prompt idle for %s s: the screensaver starts by itself\n' "$IDLE"
fi
printf '\nRESULT: OK (warnings: %s)\n' "$WARNINGS"
