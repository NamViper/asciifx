#!/usr/bin/env bash
# Check that asciifx still works on Python 3.12, its minimum supported version.
#
# Development usually happens on a newer Python, which accepts syntax and
# stdlib APIs that 3.12 rejects. This script gets a 3.12 interpreter through uv
# (no sudo, nothing installed system-wide), keeps a 3.12 venv with the project
# installed editable, and then runs:
#   1. a compile of every .py file in src/, tests/ and tools/ (newer syntax),
#   2. an import of every asciifx module (import-time errors in untested code),
#   3. vermin, a static scan for stdlib APIs newer than 3.12 (skipped offline),
#   4. the full pytest suite.
# All four steps always run; the exit status is non-zero if any failed.
#
# Usage, from anywhere:
#   tools/check_py312.sh [pytest args...]        # e.g. -x, -k image
#
# Environment:
#   ASCIIFX_PY312_DIR   work dir for uv, the 3.12 interpreter and the venv
#                       (default: ${XDG_CACHE_HOME:-~/.cache}/asciifx-py312).
#                       Reused between runs; delete it to start from scratch.
#   ASCIIFX_PY312_FRESH=1   recreate the venv even if it exists.
#   ASCIIFX_SKIP_VERMIN=1   skip step 3.
#   UV                  path to a uv binary (default: uv on PATH, else a copy
#                       bootstrapped into the work dir with the repo's .venv pip).

set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORK=${ASCIIFX_PY312_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/asciifx-py312}
VENV=$WORK/venv
PY=$VENV/bin/python

# Keep everything uv downloads inside WORK, and never touch ~/.local/bin.
export UV_PYTHON_INSTALL_DIR=$WORK/python
export UV_CACHE_DIR=${UV_CACHE_DIR:-$WORK/cache}
export UV_PYTHON_BIN_DIR=$WORK/python-bin
export UV_TOOL_DIR=$WORK/tools
export UV_TOOL_BIN_DIR=$WORK/tools/bin
export PYTHONDONTWRITEBYTECODE=1

die() { echo "check_py312: $*" >&2; exit 2; }
step() { printf '\n== %s\n' "$*"; }

mkdir -p "$WORK" || die "cannot create $WORK"

# --- uv -----------------------------------------------------------------------
if [[ -z ${UV:-} ]]; then
    if command -v uv >/dev/null 2>&1; then
        UV=$(command -v uv)
    elif [[ -x $WORK/uv/bin/uv ]]; then
        UV=$WORK/uv/bin/uv
    else
        step "bootstrapping uv into $WORK/uv"
        BOOT_PY=$REPO/.venv/bin/python
        [[ -x $BOOT_PY ]] || BOOT_PY=$(command -v python3) || die "no python to bootstrap uv with"
        "$BOOT_PY" -m pip install --quiet --disable-pip-version-check --target "$WORK/uv" uv \
            || die "could not pip-install uv; install uv yourself or set UV=/path/to/uv"
        UV=$WORK/uv/bin/uv
    fi
fi
[[ -x $UV ]] || die "uv not executable: $UV"

# --- Python 3.12 + venv ---------------------------------------------------------
if [[ ${ASCIIFX_PY312_FRESH:-0} == 1 ]]; then
    rm -rf "$VENV"
fi
if ! "$PY" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
    step "creating Python 3.12 venv in $VENV"
    "$UV" python find 3.12 >/dev/null 2>&1 || "$UV" python install --no-bin 3.12 \
        || die "uv could not install Python 3.12"
    rm -rf "$VENV"
    "$UV" venv --quiet --python 3.12 "$VENV" || die "uv venv failed"
fi
step "installing asciifx[dev] (editable) into the 3.12 venv"
# Re-run every time so dependency changes in pyproject.toml are picked up.
VIRTUAL_ENV=$VENV "$UV" pip install --quiet -e "$REPO[dev]" || die "install failed"
"$PY" -c 'import sys; print("python", sys.version.split()[0], "at", sys.executable)'

cd "$REPO" || die "cannot cd to $REPO"
failed=()

# --- 1. compile ---------------------------------------------------------------
step "1/4 compile src/ tests/ tools/ with Python 3.12"
"$PY" -W error::SyntaxWarning - <<'EOF' || failed+=(compile)
import pathlib, sys
bad = 0
files = [p for d in ("src", "tests", "tools") for p in sorted(pathlib.Path(d).rglob("*.py"))]
for p in files:
    try:
        compile(p.read_bytes(), str(p), "exec", dont_inherit=True)
    except (SyntaxError, SyntaxWarning) as e:
        bad += 1
        print(f"{p}:{getattr(e, 'lineno', '?')}: {type(e).__name__}: {e}")
print(f"{len(files)} files, {bad} failed")
sys.exit(bad > 0)
EOF

# --- 2. import ----------------------------------------------------------------
step "2/4 import every asciifx module"
"$PY" - <<'EOF' || failed+=(import)
import importlib, pkgutil, sys, traceback
import asciifx
bad = 0
names = [m.name for m in pkgutil.walk_packages(asciifx.__path__, "asciifx.")
         if not m.name.endswith(".__main__")]  # __main__ would run the CLI
for name in names:
    try:
        importlib.import_module(name)
    except Exception:
        bad += 1
        print(f"--- {name}", flush=True)
        traceback.print_exc(limit=-3, file=sys.stdout)
print(f"{len(names)} modules, {bad} failed")
sys.exit(bad > 0)
EOF

# --- 3. vermin ----------------------------------------------------------------
step "3/4 vermin: stdlib APIs newer than 3.12"
if [[ ${ASCIIFX_SKIP_VERMIN:-0} == 1 ]]; then
    echo "skipped (ASCIIFX_SKIP_VERMIN=1)"
elif ! "$UV" tool run --quiet --python 3.12 vermin --version >/dev/null 2>&1; then
    echo "skipped: could not run vermin through uv (offline?)"
else
    # Exits 1 when something needs > 3.12. Syntax errors are step 1's job.
    "$UV" tool run --quiet --python 3.12 vermin --no-tips --eval-annotations \
        --violations -t=3.12- src tests tools || failed+=(vermin)
fi

# --- 4. pytest ----------------------------------------------------------------
step "4/4 pytest under Python 3.12"
# no:cacheprovider leaves .pytest_cache to the main dev venv.
"$PY" -m pytest -q -p no:cacheprovider "$@" || failed+=(pytest)

echo
if ((${#failed[@]})); then
    echo "check_py312: FAILED: ${failed[*]}"
    exit 1
fi
echo "check_py312: OK (Python 3.12)"
