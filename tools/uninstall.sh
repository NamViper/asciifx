#!/bin/sh
# Undo tools/install.sh.
#
#   tools/uninstall.sh [--purge-cache] [--keep-command] [--dry-run]
#
#   1. removes every "# >>> asciifx ... >>>" ... "# <<< asciifx ... <<<" block
#      from ~/.zshrc, ~/.bashrc and ~/.tmux.conf (each file is backed up first
#      and syntax-checked afterwards; the backup is restored if the check fails).
#      Nothing outside those blocks changes.
#   2. uninstalls the command: `pipx uninstall asciifx` / `uv tool uninstall
#      asciifx` if one of them has it, and removes ~/.local/bin/asciifx if it is a
#      symlink into a .venv (what install.sh's venv method creates). A regular
#      file there is never touched. The repo and its .venv are left alone.
#   3. with --purge-cache, deletes the logo cache (~/.cache/asciifx).
#
# Safe to re-run. Exit status: 0 ok, 1 failed, 2 bad usage.

set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd -P)

usage() {
    cat <<'EOF'
Usage: tools/uninstall.sh [options]

Removes the asciifx blocks from ~/.zshrc, ~/.bashrc and ~/.tmux.conf (with
backups) and uninstalls the asciifx command. Safe to re-run.

Options:
  --purge-cache    also delete the logo cache (${XDG_CACHE_HOME:-~/.cache}/asciifx)
  --keep-command   only remove the shell blocks; keep the asciifx command
  --dry-run        show what would happen, change nothing
  -h, --help       show this help
EOF
}

DRY=0
PURGE=0
KEEP_CMD=0
while [ $# -gt 0 ]; do
    case $1 in
    --purge-cache) PURGE=1; shift ;;
    --keep-command) KEEP_CMD=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'uninstall.sh: unknown option: %s\n(run tools/uninstall.sh --help)\n' "$1" >&2; exit 2 ;;
    esac
done

WARNINGS=0
step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }
warn() { WARNINGS=$((WARNINGS + 1)); printf '    WARNING: %s\n' "$*"; }
die() {
    printf '\nERROR: %s\n' "$*" >&2
    printf 'RESULT: FAILED\n'
    exit 1
}
tilde() { case $1 in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }
run() { # show a command, then run it unless this is a dry run
    if [ "$DRY" -eq 1 ]; then printf '    would run: %s\n' "$*"; else printf '    $ %s\n' "$*"; "$@"; fi
}

TMPD=$(mktemp -d "${TMPDIR:-/tmp}/asciifx-uninstall.XXXXXX")
trap 'rm -rf "$TMPD"' EXIT
trap 'exit 130' INT TERM

if [ "$DRY" -eq 1 ]; then
    printf 'asciifx uninstaller: DRY RUN, nothing will be changed\n'
else
    printf 'asciifx uninstaller\n'
fi

# -- 1. shell startup files ---------------------------------------------------------------

# Same filter as install.sh: print $1 without the asciifx blocks (and without the
# blank lines after them or at the end); exit 3 on an unbalanced marker.
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
has_blocks() { grep -qs '^[[:space:]]*# [<>]\{3\} asciifx' "$1"; }
kind_of() { case $1 in *zshrc) echo zsh ;; *bashrc) echo bash ;; *) echo none ;; esac; }
syntax_ok() { # $1 shell kind, $2 file; checked only if that shell is installed
    case $1 in
    zsh) if command -v zsh >/dev/null 2>&1; then zsh -f -n "$2"; fi ;;
    bash) if command -v bash >/dev/null 2>&1; then bash -n "$2"; fi ;;
    esac
}

FILES="${ZDOTDIR:-$HOME}/.zshrc $HOME/.bashrc $HOME/.tmux.conf"
step "Remove the asciifx blocks from your shell files"
# Check every file first, so a damaged one stops the run before anything changes.
for f in $FILES; do
    if has_blocks "$f"; then
        strip_blocks "$f" >/dev/null 2>"$TMPD/err" ||
            die "$(tilde "$f") has unbalanced asciifx markers, so nothing was changed:
$(cat "$TMPD/err")
Fix or delete the broken '# >>> asciifx ... >>>' / '# <<< asciifx ... <<<' lines by hand, then re-run."
    fi
done
BACKUPS=""
for f in $FILES; do
    if ! has_blocks "$f"; then
        info "$(tilde "$f"): no asciifx blocks"
        continue
    fi
    [ -w "$f" ] || die "$f is not writable"
    n=$(grep -c '^[[:space:]]*# >>> asciifx.*>>>' "$f" || true)
    strip_blocks "$f" >"$TMPD/new"
    if command -v diff >/dev/null 2>&1; then
        info "changes to $(tilde "$f"):"
        diff -u "$f" "$TMPD/new" | sed -e '1,2d' -e 's/^/      /' || true
    fi
    if [ "$DRY" -eq 1 ]; then
        info "would back up $(tilde "$f") and remove $n asciifx blocks"
        continue
    fi
    backup=$f.bak-asciifx-$(date +%Y%m%d-%H%M%S)
    [ ! -e "$backup" ] || backup=$backup-$$
    cp -p "$f" "$backup"
    ok "backup: $(tilde "$backup")"
    cat "$TMPD/new" >"$f" # write through, so a symlinked dotfile stays a symlink
    kind=$(kind_of "$f")
    if ! syntax_ok "$kind" "$f" 2>"$TMPD/err"; then
        if syntax_ok "$kind" "$backup" 2>/dev/null; then
            cat "$backup" >"$f"
            die "$(tilde "$f") failed its syntax check after removing the blocks, so the original was restored:
$(cat "$TMPD/err")"
        fi
        warn "$(tilde "$f") already failed its syntax check before (not caused by asciifx)"
    fi
    BACKUPS="$BACKUPS $(tilde "$backup")"
    ok "$(tilde "$f"): removed $n asciifx blocks"
    if grep -n 'asciifx' "$f" >"$TMPD/mentions"; then
        warn "other lines in $(tilde "$f") still mention asciifx (not added by install.sh; left as they are):
$(sed 's/^/        /' "$TMPD/mentions")"
    fi
done

# -- 2. the command -----------------------------------------------------------------------

if [ "$KEEP_CMD" -eq 1 ]; then
    step "Uninstall the asciifx command: skipped (--keep-command)"
else
    step "Uninstall the asciifx command"
    removed=0
    if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q '^asciifx '; then
        run pipx uninstall asciifx
        removed=1
    fi
    if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^asciifx '; then
        run uv tool uninstall asciifx
        removed=1
    fi
    link=$HOME/.local/bin/asciifx
    if [ -L "$link" ]; then
        target=$(readlink "$link" 2>/dev/null || echo "?")
        case $target in
        */.venv/bin/asciifx)
            run rm -f "$link"
            removed=1
            ;;
        *)
            if [ ! -e "$link" ]; then
                run rm -f "$link" # dangling, e.g. left over from a pipx/uv uninstall
                removed=1
            elif [ "$DRY" -eq 1 ] && [ "$removed" -eq 1 ]; then
                info "($(tilde "$link") -> $target goes away with the uninstall above)"
            else
                warn "$(tilde "$link") -> $target is not from pipx, uv or install.sh; left alone"
            fi
            ;;
        esac
    elif [ -e "$link" ]; then
        warn "$(tilde "$link") is a regular file (an old 'pip install --user'?); left alone.
      Remove it with: python3 -m pip uninstall asciifx"
    fi
    if [ "$removed" -eq 0 ]; then info "no installed asciifx command found"; fi
    if [ -d "$REPO/.venv" ]; then
        info "the repo's own venv $REPO/.venv was kept (delete it if you don't develop asciifx)"
    fi
    if [ "$DRY" -eq 0 ] && found=$(command -v asciifx 2>/dev/null); then
        warn "an asciifx command is still on PATH: $found"
    fi
fi

# -- 3. cache -----------------------------------------------------------------------------

CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/asciifx
if [ "$PURGE" -eq 1 ]; then
    step "Delete the logo cache"
    if [ -d "$CACHE" ]; then run rm -rf "$CACHE"; else info "$(tilde "$CACHE"): not there"; fi
fi

# -- summary ------------------------------------------------------------------------------

step "Summary"
[ -z "$BACKUPS" ] || info "backups:$BACKUPS"
[ "$PURGE" -eq 1 ] || [ ! -d "$CACHE" ] || info "logo cache kept: $(tilde "$CACHE") (--purge-cache deletes it)"
info "open a new terminal: shells that are already open keep the old settings until then"
if [ "$DRY" -eq 1 ]; then
    printf '\nRESULT: DRY RUN OK (nothing changed; warnings: %s)\n' "$WARNINGS"
else
    printf '\nRESULT: OK (warnings: %s)\n' "$WARNINGS"
fi
