# asciifx

Terminal ASCII/Unicode art and animation: a Python library and the `asciifx` CLI.
Animated shell startup banners, an idle screensaver, image → text art in a dozen
styles, effects (rain, decode, glitch, fire, starfield, CRT, particles, lightning,
shockwave, blood drips, hologram…), a summoning show with a name reveal, big-letter
fonts, spinners for your own scripts, and video/ANSI-art playback. Terminal only.

Truecolor with automatic 256-color fallback, diff-based flicker-free redraws,
resize handling, and the terminal is always restored (Ctrl-C, kill, Ctrl-Z).
Piped output gets plain text with no escape codes.

## Install

Requires Python ≥ 3.12 on Linux, macOS or WSL (a POSIX terminal, not native Windows).

**[SETUP.md](SETUP.md) is the step-by-step guide.** It is written so that Claude Code can
follow it to install asciifx on a new machine. The short version, from a clone of this repo:

```bash
tools/install.sh --dry-run   # show what would change, change nothing
tools/install.sh             # install + banner/screensaver blocks in ~/.zshrc or ~/.bashrc
tools/uninstall.sh           # undo
```

`tools/install.sh` installs the command editable (pipx, else uv, else `.venv` + a
`~/.local/bin/asciifx` symlink), so `git pull` updates it. It is safe to re-run, and it backs up
your rc file before replacing only its own marked blocks. Options: `--logo PATH`,
`--name NAME`, `--style S`, `--screensaver-after SECS`, `--no-banner`, `--video`,
`--shell zsh|bash`, `--tmux-lock-after SECS`, `--no-shell`; see `tools/install.sh --help`.

By hand: `pipx install -e .` (`pipx install -e '.[video]'` adds bundled ffmpeg for
`asciifx video`), or `pip install -e '.[dev]'` into a venv.

## Quick start

```bash
asciifx startup --logo logo.png                 # hacker-style startup banner (any key skips)
asciifx summon --logo logo.png --name Neo      # ~13 s show: logo assembles, name reveal
asciifx screensaver --logo logo.png             # full-screen screensaver (any key returns)
asciifx img logo.png --style all --banner-look  # the logo in every style
asciifx banner 'ACCESS GRANTED' --effect decode # big letters, animated in place
asciifx fx matrix --palette blood               # demo any effect
asciifx spin --style pulse                      # spinner demo
```

## Shell integration (zsh, bash, tmux)

`tools/install.sh` writes these blocks for you and replaces them on every re-run. By hand:

```bash
asciifx shell-init zsh  --logo ~/logo.png --name Neo --screensaver-after 120   # for ~/.zshrc
asciifx shell-init bash --logo ~/logo.png --name Neo                           # for ~/.bashrc
asciifx shell-init tmux --logo ~/logo.png --screensaver-after 300              # for ~/.tmux.conf
```

The output is a set of marked blocks (`# >>> asciifx NAME >>>` … `# <<< asciifx NAME <<<`). Appending it
twice gives you two copies, so prefer `tools/install.sh`, which swaps the old blocks for the new ones.

- **logo**: exports `ASCIIFX_LOGO`, so `startup`, `screensaver` and `summon` work without `--logo`.
- **name** (`--name`): exports `ASCIIFX_NAME`, the name `asciifx summon` reveals.
- **startup banner** (zsh and bash): runs in every new interactive shell. The logo decodes in out of rain,
  a boot log types out, then system info (user@host, OS, kernel, uptime, date). It stays above your prompt.
  Skip it with any key. Disable it for one shell with `ASCIIFX_NO_BANNER=1 zsh` (or `bash`). It never runs in
  non-interactive shells or when output isn't a terminal.
- **screensaver** (zsh only): starts after N seconds without typing at the prompt. It never starts
  mid-typing or while a command runs. Any key returns you to your prompt untouched. Disable it for one shell
  with `ASCIIFX_NO_SCREENSAVER=1 zsh`. Bash gets no idle screensaver, because bash's `TMOUT` closes the
  shell instead of running a hook. Use the tmux lock there.
- **tmux**: `lock-after-time` + `lock-command`, so tmux starts the screensaver after idle time even while
  programs run in panes. Reload with `tmux source-file ~/.tmux.conf`.

## Screensaver

`asciifx screensaver` (any key quits) plays scenes in a shuffled rotation. Each scene is followed by matrix
rain with the time (`HH:MM`, block font) carved into it, and a dim `idle 14m · user@host` line sits in the
bottom-right corner. Without a logo it is endless rain with the clock.

| Scene | |
|---|---|
| `skull` | the logo decodes in, glitches, re-renders itself through the styles (`--styles`, default `dots,sketch,outline`), dissolves |
| `warp` | a starfield at speed; the logo flies in from far away, holds, then jumps to warp |
| `inferno` | fire; the logo rises out of the flames, burns, sinks back |
| `hologram` | the logo projected from an emitter as a flickering hologram |
| `sysmon` | a live system monitor: CPU, memory, load, uptime, tasks, network |

Options: `--scenes skull,warp,…` (default: all five), `--palette auto|blood|green` (auto = blood red
07:00–19:00, green at night), `--rain-seconds 30`, `--no-clock`, `--no-clock-outline`, `--no-idle`,
`--idle-since EPOCH` / `--idle-for SECONDS` (where the idle counter starts; the zsh and tmux blocks pass
these), `--seed N`, `--fps 20`, `--duration S`.

## Summon

`asciifx summon` plays a ~13 s full-screen show and then leaves the startup banner in your terminal.
Any key skips it. The sequence: a "SUMMONING 3 2 1" countdown → CRT power-on → the logo assembles out of
sparks → the finale → your name revealed in big block letters (decode + glitch) → a glint and hologram
flicker while blood drips pool at the bottom → CRT power-off.

Every run picks random variants. The **assembly** is `scatter`, `vortex` or `explosion`. The **finale** is
`shockwave`, `lightning` or `eyes`. With `eyes`, the eye sockets ignite and pulse like a heartbeat while
the jaw chatters.

```bash
asciifx summon                                   # name from $ASCIIFX_NAME, else your username
asciifx summon --finale eyes --assembly vortex   # choose the variants
asciifx summon --name ''                         # no name reveal
```

Options: `--name NAME`, `--assembly scatter|vortex|explosion`, `--finale shockwave|lightning|eyes`,
`--style S`, `--seed N` (repeat a show exactly), `--no-banner`, `--logo F` (default `$ASCIIFX_LOGO`).

`eyes` needs a sidecar file next to the logo, `<logo>.asciifx.json`, with the eye and jaw positions as
fractions (0–1) of the cropped logo's width (x) and height (y). Without a sidecar, `eyes` falls back to
`shockwave`. The repo ships `skull_logo.asciifx.json` for `skull_logo.png`:

```json
{
  "version": 1,
  "eyes": [{"cx": 0.36, "cy": 0.555, "rx": 0.075, "ry": 0.045},
           {"cx": 0.655, "cy": 0.535, "rx": 0.075, "ry": 0.05}],
  "jaw": {"y": 0.79, "x0": 0.27, "x1": 0.71}
}
```

Each eye is an ellipse (centre `cx`,`cy`, radii `rx`,`ry`). The lower jaw is everything below `jaw.y` between
`x0` and `x1`. Either key may be left out.

## Commands

| Command | What it does |
|---|---|
| `startup [--logo F] [--style S] [--duration 3.5]` | the shell startup banner |
| `screensaver [--logo F] [--scenes a,b] [--palette auto\|blood\|green] [--styles a,b,c] [--rain-seconds 30] [--no-clock] [--no-idle]` | full-screen screensaver: rotating scenes, rain with a clock |
| `summon [--logo F] [--name N] [--assembly A] [--finale F] [--style S] [--seed N] [--no-banner]` | ~13 s logo show with a name reveal, then leaves the banner |
| `img PATH [--style S\|all] [--width N] [--banner-look] …` | image → text art (`--invert`, `--transparent-bg`, `--autocrop`, `--despeckle`, `--tint R,G,B`, `--outline`, …) |
| `banner TEXT [--font block\|outline\|small\|ascii\|plain] [--effect E]` | big text, animated in place |
| `fx NAME [--palette blood\|green] [--endless] [--over] [--warp N]` | full-screen effect demo (`fx --list`) |
| `spin [--style S] [--seconds N]` | spinner demo |
| `play bounce\|FILE\|DIR [--rate N] [--no-loop]` | the `bounce` demo, or a text/ANSI-art animation: a file with frames separated by `---` lines, or a directory of `*.txt` frames |
| `video PATH [--style S] [--speed N] [--no-loop]` | a video file as text art (needs ffmpeg: on PATH or the `video` extra) |
| `shell-init zsh\|bash\|tmux [--logo F] [--name N] [--screensaver-after S] [--no-banner] [--style S]` | print the shell/tmux blocks (`tools/install.sh` manages them for you) |

All animated commands: any key skips/quits, `--color truecolor|256` overrides detection.

### Logo styles (`--style`)

`dots` (braille), `outline` (braille edges), `sketch` (ASCII line art), `blocks` (half blocks),
`symbols`, `letters`, `numbers`, `ascii` (characters chosen by shape, measured from Cascadia Mono),
`binary` (0/1), `classic` (`.:-=+*#%@`), `shade` (`░▒▓█`).
Braille/blocks look good small; the character styles need about 64×32 cells and get that automatically.

### Effects

`matrix` rain, `decode`, `glitch`, `typewriter`, `fire`, `starfield`, `crt` (power on/off),
`assemble` (particles), `glint`, `lightning` (branching bolt, white flash, afterglow), `shockwave`
(an aspect-correct ring that pushes what it passes), `drip` (blood drops that run down and pool),
`hologram` (rolling scanlines, red/cyan ghost, dropouts), plus `Morph`/`Reverse` transitions.
Try any of them with `asciifx fx NAME`. Palettes: `blood`, `green`.

## Library

```python
from asciifx.api import banner, play, spinner
from asciifx.effects import BLOOD, chain, make
from asciifx.sources import image_to_grid

with spinner("deploying") as sp:          # ends as: ✔ uploading (3.2s)
    sp.text = "uploading"
    ...

banner("DONE", font="block", gradient=((255, 255, 255), (200, 20, 32)),
       effect=make("decode", BLOOD))

logo = image_to_grid("logo.png", 40, style="dots", invert=True, transparent_bg=True, autocrop=True)
banner(logo, effect=chain(make("matrix", BLOOD, duration=1.0), make("glitch", BLOOD, start=1.0)))
```

Building blocks: `asciifx.core` (`Grid`, `Cell`, `Layer`, `compose`, the fixed-timestep loop
`run`, easing), `asciifx.effects` (pure `apply(grid, t) -> Grid`), `asciifx.sources` (`ImageSource`,
`VideoSource`, `TextFramesSource`, or your own `frame(t, w, h) -> Grid`), `asciifx.fonts`
(`render_text`), `asciifx.term.Terminal` (the only code that touches the terminal).

## Development

```bash
.venv/bin/pytest -q          # full suite (tests/test_install_script.py runs the installers in a temp HOME)
tools/check_py312.sh         # compile + import + vermin + pytest under Python 3.12 (uv-managed)
```

Architecture rules live in `CLAUDE.md`.

## License

[MIT](LICENSE)