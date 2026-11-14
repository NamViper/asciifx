# Setting up asciifx

This guide installs `asciifx` from a clone of this repository, then adds the animated startup banner and
the idle screensaver to your shell. It works on Linux, macOS and WSL. One script does the work,
`tools/install.sh`. It is safe to re-run, and `tools/uninstall.sh` undoes it.

## For Claude Code: read this first

You are probably installing this for a user in their terminal. Follow these steps in order:

1. **Prerequisites** ([section 1](#1-prerequisites)): check `git` and Python ≥ 3.12. Package installs need
   `sudo`. Show the user the exact command and ask before you run it.
2. **Clone** ([section 2](#2-clone)): ask the user for the repository URL if you don't have it. Clone into
   their home directory, on the Linux filesystem (on WSL that means not under `/mnt/c`).
3. **Dry run**: `tools/install.sh --dry-run` plus the flags the user wants
   ([section 3](#3-install)). It changes nothing. It prints the plan and a diff of the shell startup file
   (`~/.zshrc` or `~/.bashrc`). Show the user that diff. If they didn't clearly ask for shell changes, ask
   before applying them. Until then you can use `--no-shell`.
4. **Install**: run the same command without `--dry-run`. It worked if the exit status is 0 **and** the
   last line is `RESULT: OK (warnings: N)`. Read every `WARNING:` line and handle it with
   [section 6](#6-troubleshooting). On failure the output ends with `ERROR: <what to do>` (on stderr) and
   `RESULT: FAILED`. Follow the ERROR text. Exit status 2 means a bad flag.
5. **Verify** ([section 4](#4-verify)): run the checks there and compare with the expected output.
6. **Hand over**: tell the user to open a **new** terminal (the banner should play) and to try
   `asciifx summon` and `asciifx screensaver`. These are animations for a real terminal. In your
   non-interactive shell they print one static frame or nothing, so the user has to look at them.

What NOT to do:

- Don't edit the user's `~/.zshrc`, `~/.bashrc` or `~/.tmux.conf` by hand, and don't append
  `asciifx shell-init … >> ~/.zshrc`, which duplicates blocks on every run. The installer only touches lines
  between `# >>> asciifx … >>>` and `# <<< asciifx … <<<` markers. It backs up the file first
  (`<file>.bak-asciifx-<timestamp>`), syntax-checks the result and restores the backup if the check fails.
- Don't delete those backups, and don't move or delete a regular file at `~/.local/bin/asciifx`. The
  installer refuses to replace one. Tell the user about it instead (see troubleshooting).
- Don't use `sudo` without asking. Don't `pip install` into the system Python
  (`--break-system-packages`). Don't commit, push or `git init` in the clone.
- If the rc file is managed by something else (a dotfiles repo, chezmoi, home-manager/Nix, a read-only
  file), or you're unsure, ask the user before changing it. `--no-shell` installs only the command.
- Don't move or delete the clone after installing. The install is editable and the rc blocks point at the
  logo inside it. If it must move, re-run `tools/install.sh` from the new place.

## 1. Prerequisites

You need `git`, Python ≥ 3.12, and preferably **pipx** (or **uv**). Without either of them the installer
falls back to a `.venv` inside the clone plus a symlink in `~/.local/bin`, which needs Python's `venv`
module. Check what you have:

```bash
git --version
python3 --version              # needs 3.12 or newer (or python3.12 / python3.13 / python3.14)
pipx --version || uv --version
echo "$SHELL"                  # zsh or bash
```

**Ubuntu 24.04+ / Debian 13+ / WSL with one of those** (python3 is 3.12+):

```bash
sudo apt update && sudo apt install -y git python3 python3-venv pipx
```

**Ubuntu 22.04** (python3 is 3.10): get 3.12 from the deadsnakes PPA. The installer then uses the venv
fallback, so pipx isn't needed:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update && sudo apt install -y git python3.12 python3.12-venv
```

**Debian 12** (python3 is 3.11): install uv. If no Python ≥ 3.12 is on PATH, the installer lets uv download
Python 3.12 by itself, with no sudo. uv's install script also adds `~/.local/bin` to PATH in your shell files.

```bash
sudo apt install -y git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**macOS** (Homebrew from https://brew.sh; `git` comes with the Xcode command line tools,
`xcode-select --install`):

```bash
brew install python@3.12 pipx
```

**Fedora 39+**: `sudo dnf install -y git python3 pipx`

**Arch**: `sudo pacman -S --needed git python python-pipx`

Native Windows isn't supported; use WSL. You also need a terminal with Unicode: braille characters
(U+2800–U+28FF) and box/block glyphs. Truecolor is optional, since asciifx falls back to 256 colors.
Windows Terminal, iTerm2, GNOME Terminal, Konsole, kitty, WezTerm, Alacritty and Ghostty all work.

## 2. Clone

```bash
git clone https://github.com/NamViper/asciifx.git ~/asciiart
cd ~/asciiart
```

On WSL, clone inside Linux (`~/…`), not under `/mnt/c/…`. A clone made by Windows git can have CRLF line
endings, and then the scripts fail (see troubleshooting).

## 3. Install

```bash
tools/install.sh --dry-run      # look first: prints the plan and the rc-file diff, changes nothing
tools/install.sh                # do it
```

| Flag | Default | Meaning |
|---|---|---|
| `--logo PATH` | `<clone>/skull_logo.png` | logo image for the banner, screensaver and summon (stored as an absolute path); the skull is an example, use your own |
| `--name NAME` | `$USER` | the name `asciifx summon` reveals (exported as `ASCIIFX_NAME`) |
| `--style STYLE` | `dots` | logo style for the startup banner: `dots`, `sketch`, `outline`, `blocks`, `ascii`, `classic`, … |
| `--screensaver-after SECS` | `120` | zsh: screensaver after SECS idle at the prompt; `0` = no screensaver. Bash has none. |
| `--no-banner` | | no startup banner in new shells |
| `--video` | | also install the `[video]` extra (bundled ffmpeg for `asciifx video`) |
| `--shell zsh\|bash\|auto` | `auto` | which file to edit: `~/.zshrc` (or `$ZDOTDIR/.zshrc`) or `~/.bashrc`; auto = from `$SHELL` |
| `--tmux-lock-after SECS` | (leave alone) | also manage the screensaver block in `~/.tmux.conf`; `0` removes it |
| `--python PATH` | (search) | use this Python ≥ 3.12 |
| `--method auto\|pipx\|uv\|venv\|none` | `auto` | auto = pipx, else uv, else venv; `none` = use the `asciifx` already on PATH |
| `--no-shell` | | install only; no startup file is touched |
| `--dry-run` | | show everything, change nothing |

Examples:

```bash
tools/install.sh --name "NamViper"                       # zsh: logo, name, banner, 2-min screensaver
tools/install.sh --logo ~/Pictures/me.png --style sketch
tools/install.sh --screensaver-after 0                   # banner only
tools/install.sh --no-banner --screensaver-after 300     # screensaver only, after 5 minutes
tools/install.sh --tmux-lock-after 300                   # also lock tmux after 5 minutes idle
tools/install.sh --no-shell --video                      # just the command, with video support
```

Each run replaces all the asciifx blocks in the startup file with exactly what that run's flags ask for.
To change a setting, re-run with the new flags. `~/.tmux.conf` is only touched when `--tmux-lock-after`
is given.

What it does, step by step:

1. Finds Python ≥ 3.12: `--python`, else the first of `python3.14 python3.13 python3.12 python3 python` on
   PATH. With uv and no suitable Python, uv downloads 3.12.
2. Installs the package **editable**, so `git pull` updates the command:
   `pipx install --force --python <py> -e <clone>[video]`, or `uv tool install --force --python <py> -e …`,
   or `<clone>/.venv` + `pip install -e` + the symlink `~/.local/bin/asciifx`. An old *symlink* at
   `~/.local/bin/asciifx` (from an earlier install) is replaced. A *regular file* there stops the install.
3. Checks that `asciifx --help` runs and that the logo renders (numpy and Pillow work).
4. Pre-renders the logo cache (`~/.cache/asciifx`), so the first new shell starts fast.
5. Rewrites the startup file: backup, remove every `# >>> asciifx … >>>` block, append fresh blocks from
   `asciifx shell-init`, then a `zsh -n` / `bash -n` check (the backup is restored on failure). An unchanged
   file is left alone, with no new backup. Lines elsewhere that mention asciifx get a warning and are not
   touched.
6. Prints a summary.

Expected output (pipx, zsh; paths shortened, versions vary, diff lines left out):

```text
asciifx installer
    repo:  /home/alice/asciiart
    logo:  /home/alice/asciiart/skull_logo.png
    name:  NamViper
    shell: zsh (~/.zshrc)

==> [1/6] Find Python >= 3.12
    ok: /usr/bin/python3.12 (Python 3.12.3)

==> [2/6] Install the asciifx command (method: pipx)
    $ pipx install --force --python /usr/bin/python3.12 -e /home/alice/asciiart
  installed package asciifx 0.1.0, installed using Python 3.12.3
  These apps are now available
    - asciifx
    ok: installed: /home/alice/.local/bin/asciifx

==> [3/6] Check the installed command
    ok: asciifx --help runs
    ok: the logo renders (numpy and Pillow work)

==> [4/6] Pre-render the logo cache
    ok: cached for 80x24 200x50 in ~/.cache/asciifx

==> [5/6] Update ~/.zshrc
    changes to ~/.zshrc:
      +# >>> asciifx logo >>>
      ...
    ok: backup: ~/.zshrc.bak-asciifx-20261112-144759
    ok: ~/.zshrc: removed 0 old asciifx block(s), added 4

==> [6/6] Summary
    method:      pipx
    ...
RESULT: OK (warnings: 0)
```

A second run with the same flags ends with `ok: ~/.zshrc: already up to date` and creates no backup.

What gets added to `~/.zshrc`: four blocks, `logo` (`export ASCIIFX_LOGO=…`), `name`
(`export ASCIIFX_NAME=…`), `startup banner` and `screensaver`. Bash gets the first three. Bash has no
screensaver, because bash's `TMOUT` closes the shell instead of running a hook; use `--tmux-lock-after`.
The banner and screensaver blocks run only in interactive shells whose stdin and stdout are terminals, and
only if `asciifx` is on PATH. So `scp`, `rsync`, `git` over ssh, scripts and IDE tasks never see them.

## 4. Verify

Run these after the install. Expected output is shown after `→`:

```bash
command -v asciifx                         # → /home/alice/.local/bin/asciifx
asciifx --help | head -n 1                 # → usage: asciifx [-h] COMMAND ...
grep '^# >>> asciifx' ~/.zshrc             # → the 4 block headers: logo, name, startup banner, screensaver
                                           #   (~/.bashrc: logo, name, startup banner)
zsh -n ~/.zshrc && echo syntax ok          # → syntax ok          (bash: bash -n ~/.bashrc)
zsh -i -c 'echo "$ASCIIFX_NAME | $ASCIIFX_LOGO"' </dev/null
                                           # → NamViper | /home/alice/asciiart/skull_logo.png
                                           #   (no banner: it skips non-terminal output)
asciifx startup --duration 0 | grep -c 'ACCESS GRANTED'          # → 1   (piped: plain static text)
asciifx startup --duration 0 | grep -c "$(printf '\033')"        # → 0   (no escape codes in pipes)
ls ~/.cache/asciifx                        # → logo-<hash>.json files
```

If `command -v asciifx` prints nothing, `~/.local/bin` isn't on your PATH (see troubleshooting).

Then the user, in a **new** terminal window:

1. The banner plays: the skull decodes out of red rain, a boot log types out, then system info. Any key skips.
2. `asciifx summon`: a ~13 s show. A "SUMMONING 3 2 1" countdown, CRT power-on, the logo assembles out of
   sparks, a finale, the name revealed in big letters, blood drips, CRT power-off. Then the banner stays in
   the terminal. Every run is different.
3. `asciifx screensaver`: full screen, rotating scenes with matrix rain and a clock in between. Any key
   returns.
4. zsh: leave the prompt alone for the idle time (default 120 s) and the screensaver starts by itself.

## 5. Customizing

Re-run `tools/install.sh` with new flags for anything that lives in the startup file.

**Your own logo.** The bundled `skull_logo.png` is only an example; replace it with your own image. The README's
["Using your own logo"](README.md#using-your-own-logo) section has the full walkthrough, including how to measure the
eye and jaw positions for a new sidecar. In short: any PNG or JPEG works best with a dark subject on a plain white or transparent
background. The banner inverts it so the ink glows, removes the background around the subject and crops
it. Preview it first, then install it:

```bash
asciifx img ~/Pictures/logo.png --banner-look              # exactly as the banner will draw it
asciifx img ~/Pictures/logo.png --style all --banner-look  # every style
tools/install.sh --logo ~/Pictures/logo.png
```

Keep the file where it is; the blocks store its absolute path. For summon's `eyes` finale (glowing eyes,
chattering jaw), put a sidecar file next to the logo: `logo.png` → `logo.asciifx.json`. It holds the eye
ellipses and the jaw line as fractions (0–1) of the cropped logo's width and height. Copy
`skull_logo.asciifx.json` as a template; the format is in the README's "Summon" section. Without a
sidecar, `eyes` falls back to the shockwave finale.

**Styles.** `--style` picks the banner's style. `dots` (braille, the default), `sketch` (ASCII line art)
and `outline` (braille edges) look best. There are also `blocks`, `symbols`, `letters`, `numbers`,
`ascii`, `binary`, `classic` and `shade`. The other commands take `--style` too
(`asciifx summon --style outline`). The screensaver cycles through `--styles dots,sketch,outline`.

**Name.** `--name "Your Name"` sets `ASCIIFX_NAME`, which `asciifx summon` reveals. For one run:
`asciifx summon --name Neo`. To skip the reveal: `asciifx summon --name ''`.

**Summon variants.** `asciifx summon --assembly scatter|vortex|explosion --finale shockwave|lightning|eyes`
(random by default). `--seed N` repeats a show exactly.

**Screensaver.** The idle time comes from `--screensaver-after SECS` (zsh). To try the options by hand:

```bash
asciifx screensaver --scenes warp,sysmon       # scenes: skull, warp, inferno, hologram, sysmon
asciifx screensaver --palette green            # auto (default: blood red 07-19 h, green at night) | blood | green
asciifx screensaver --no-clock --no-idle       # no time in the rain, no idle line
```

**Turning things off.** For one shell: `ASCIIFX_NO_BANNER=1 zsh` or `ASCIIFX_NO_SCREENSAVER=1 zsh`
(`bash` the same way). For good: re-run with `--no-banner` or `--screensaver-after 0`. To skip the banner
in some places only, export the variable in the startup file **above** the asciifx blocks, for example
VS Code's integrated terminal:

```bash
[[ $TERM_PROGRAM == vscode ]] && export ASCIIFX_NO_BANNER=1
```

**tmux.** `tools/install.sh --tmux-lock-after 300` adds a `~/.tmux.conf` block, so tmux starts the
screensaver after 5 minutes idle, even while programs run in panes. Load it with
`tmux source-file ~/.tmux.conf`. If you always work in tmux, consider `--screensaver-after 0` so the shell
hook doesn't also fire. Remove the block with `--tmux-lock-after 0`.

**Video.** `tools/install.sh --video` adds bundled ffmpeg. Then run `asciifx video clip.mp4 --style dots`.
Without the extra, `asciifx video` uses an `ffmpeg` from PATH.

## 6. Troubleshooting

**Colors look wrong or flat (no truecolor).** asciifx uses 24-bit color when `COLORTERM` is `truecolor` or
`24bit`, otherwise 256 colors. Check with `echo $COLORTERM`. If your terminal supports truecolor but
doesn't set the variable, add `export COLORTERM=truecolor` to your startup file. To force a mode for one
command: `asciifx summon --color 256`. Inside tmux, add `set -as terminal-features ',*:RGB'` to
`~/.tmux.conf` (tmux ≥ 3.2) and make sure `COLORTERM` reaches the shell.

**Squares, question marks or gaps instead of the logo.** The terminal font has no braille or block
characters. Use a font that does, such as Cascadia Mono (Windows Terminal's default), JetBrains Mono,
DejaVu Sans Mono or any Nerd Font. Or switch to a pure-ASCII style:
`tools/install.sh --style sketch` (also `ascii`, `classic`). To test:
`asciifx img skull_logo.png --style dots --width 30`.

**`ERROR: Python >= 3.12 not found` / `python3.12: not found`.** The installer prints the fix for your OS;
see [section 1](#1-prerequisites). If a suitable Python lives outside PATH, use
`tools/install.sh --python /path/to/python3.12`.

**`… cannot create virtual environments (the venv/ensurepip module is missing)`.** Debian/Ubuntu ship
`venv` separately. Run `sudo apt install -y python3.12-venv` (with your version number), or install pipx or
uv, which the installer prefers.

**`WARNING: ~/.local/bin is not on PATH here`.** New shells may not find `asciifx`; the banner block then
skips itself silently. Check `echo $PATH` in a new terminal. If `~/.local/bin` is missing there, add this
line to the startup file **above** the asciifx blocks, or run `pipx ensurepath`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

**`ERROR: ~/.local/bin/asciifx exists and is a regular file`.** It is probably an old
`pip install --user asciifx`. The installer never overwrites it. Ask the user, then remove it with
`python3 -m pip uninstall asciifx`, or move it aside with
`mv ~/.local/bin/asciifx ~/.local/bin/asciifx.old`, and re-run.

**`WARNING: 'asciifx' on PATH is …, not …`.** Another install (a conda env, an old venv) comes first on
PATH. Remove it or reorder PATH.

**`ERROR: ~/.zshrc has unbalanced asciifx markers`.** A `# >>> asciifx … >>>` line has no matching
`# <<< asciifx … <<<` line (or the other way round), probably from a hand edit. The file was not changed.
The error names the line numbers. Fix them by hand (ask the user), then re-run.

**`WARNING: lines outside the managed blocks … mention asciifx`.** Older hand-made lines, for example a
second `asciifx startup`. They were left alone. If the banner now runs twice, delete the duplicates.

**`WARNING: ~/.zshrc already failed 'zsh -n' before this change`.** The file had a syntax error before the
install. The new blocks were still added; fix the old error.

**The first shell after installing (or after resizing the window) starts slowly.** Converting the logo for
a new terminal size takes about 0.3 s (numpy + Pillow). The result is cached in `~/.cache/asciifx`, and
later starts only read the cache. The installer pre-warms the common sizes. The banner animation itself
takes 3.5 s; any key skips it. If every start is slow, check that `~/.cache/asciifx` is writable, and
time it: `time asciifx startup --duration 0 >/dev/null`.

**The banner prints escape codes or garbage somewhere (scp, rsync, an IDE, a script).** It shouldn't. The
blocks run only in interactive shells with a terminal on stdin and stdout, and piped asciifx output is
plain text. Check that nothing outside the blocks calls `asciifx` (see the warning above). Make sure the
blocks are in `~/.zshrc` / `~/.bashrc`, not in `~/.zshenv` or `~/.profile`.

**`asciifx: startup banner failed: …`.** The logo can't be read (moved, deleted, not an image). Re-run
`tools/install.sh --logo <path>`. `ASCIIFX_DEBUG=1 asciifx startup` shows the full traceback.

**The screensaver never starts.** It is zsh only (5.3 or newer; `echo $ZSH_VERSION`), and it waits for idle time *at the prompt*. It doesn't
count while a command runs or while you type. Check `echo $TMOUT` in a new shell; it should show the idle
seconds. Prompt themes or plugins that set `TMOUT`/`TRAPALRM` themselves (prompt clocks do this) clash with
it. The one defined last wins. Use `--screensaver-after 0` plus the tmux lock instead.

**macOS + bash: no banner.** Terminal.app starts login shells, which read `~/.bash_profile`, not
`~/.bashrc`. Add `[ -r ~/.bashrc ] && . ~/.bashrc` to `~/.bash_profile`. The installer warns about this.

**WSL.**
- Clone into the Linux filesystem (`~/asciiart`). Under `/mnt/c` everything is slow.
- `set: Illegal option -`, `$'\r': command not found` or `bad interpreter: No such file or directory`
  means the scripts have Windows (CRLF) line endings. Re-clone inside WSL with
  `git clone -c core.autocrlf=false https://github.com/NamViper/asciifx.git`.
- Windows Terminal supports truecolor and braille out of the box.
- Windows Pythons on PATH (`/mnt/c/…/python.exe`) are ignored. Install a Linux Python (section 1).

## 7. Updating

```bash
cd ~/asciiart && git pull
tools/install.sh <same flags as before>     # refreshes dependencies and the blocks; a no-op if nothing changed
```

The install is editable, so code changes take effect right after `git pull`. Re-running the installer also
picks up new dependencies and any changes to the startup blocks.

## 8. Uninstall

```bash
tools/uninstall.sh --dry-run        # show what would change
tools/uninstall.sh                  # remove the blocks + the command
tools/uninstall.sh --purge-cache    # ... and the logo cache (~/.cache/asciifx)
tools/uninstall.sh --keep-command   # only remove the shell blocks
```

The uninstaller removes every asciifx block from `~/.zshrc`, `~/.bashrc` and `~/.tmux.conf`, backing each
file up first. It then runs `pipx uninstall asciifx` or `uv tool uninstall asciifx` (whichever has it) and
removes a `~/.local/bin/asciifx` symlink that points into a `.venv`. It never touches a regular file, the
clone or the clone's `.venv`; delete `~/asciiart` yourself if you're done with it. Expected ending:

```text
==> Uninstall the asciifx command
    $ pipx uninstall asciifx
uninstalled asciifx! ✨ 🌟 ✨

==> Summary
    backups: ~/.zshrc.bak-asciifx-20261112-143935
    open a new terminal: shells that are already open keep the old settings until then

RESULT: OK (warnings: 0)
```

## 9. Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # once (or reuse the installer's .venv)
.venv/bin/pytest -q                                              # full suite
.venv/bin/pytest -q tests/test_install_script.py                 # the installers, in a temp HOME
tools/check_py312.sh                                             # compile + import + vermin + pytest on Python 3.12
```

`tests/test_install_script.py` runs `install.sh` and `uninstall.sh` with a temporary `HOME`, a copy of the
scripts, and fake `pipx` / `uv` / `python` / `asciifx` commands. It never touches your real home directory
and never needs the network. The architecture rules are in `CLAUDE.md`.
