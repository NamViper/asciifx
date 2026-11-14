# asciifx – Terminal ASCII Art & Animation

## Goal
Python library + CLI for terminal ASCII/Unicode art and animation. Terminal only.
Used as: shell startup banner, importable banners/spinners for other CLI tools, tmux screensaver, standalone player.

## Architecture (do not violate)
- core/: Grid (w×h of Cell{ch, fg, bg}), layers + compositing, fixed-timestep loop (30 FPS default), easing.
- sources/: procedural, image→ASCII (ramp / half-block / braille), video via ffmpeg, text-frame files.
- effects/: pure `apply(grid, t: float) -> Grid`, no I/O.
- term/: the ONLY module touching the terminal:
  - alt screen, hidden cursor, synchronized output (?2026), truecolor w/ 256-color fallback (COLORTERM)
  - diff-based redraw, one stdout.write per frame
  - raw/cbreak mode, non-blocking key input via select, SIGWINCH resize handling
  - guaranteed restore in finally + SIGINT/SIGTERM
  - non-TTY: print a single static frame, no escape sequences
- api.py: public library API: banner(text, effect=..., duration=...), spinner() context manager, play(source).
- cli.py: `asciifx play|img|video|banner|screensaver`, any key skips/quits.

## Conventions
- Python 3.12, type hints, dataclasses. Core = stdlib only; numpy + Pillow allowed in sources/.
- Account for ~2:1 cell aspect ratio. Time-based animation, never frame-count-based.
- Each effect: demo command + pytest asserting grid output at fixed t.
- Installable via pyproject.toml, entry point `asciifx`, installed with `pipx install -e .`.

## Build order
1. Grid + term renderer (static frame, cleanup) 2. Loop + moving sprite + key-to-quit
3. Diff rendering + resize 4. Image→ASCII (3 modes) 5. Effects: matrix, fire, typewriter, glitch, starfield
6. banner()/spinner() API 7. Screensaver + shell startup integration 8. Video
