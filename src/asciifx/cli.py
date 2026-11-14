"""Command-line interface: ``asciifx play|img|video|fx|banner|spin|startup|screensaver|summon|shell-init``.

Heavy or optional modules (effects, image conversion) are imported inside the
command that needs them, so ``asciifx startup`` stays fast on every new shell.
"""

from __future__ import annotations

# Ctrl-C while this loads (a new shell's banner: the user hits Ctrl-C right away)
# exits with 130 like main() does, instead of a KeyboardInterrupt traceback and a
# death by SIGINT, which makes zsh/bash abort the rest of ~/.zshrc / ~/.bashrc.
try:
    import argparse
    import os
    import shlex
    import sys
    import time
    from collections.abc import Callable, Sequence
    from dataclasses import dataclass
    from typing import Any

    from .api import animate_inline, banner, play
    from .core.grid import Grid
    from .core.loop import DEFAULT_FPS
    from .sources import Bounce, Source
    from .sources.styles import STYLES, preferred_size  # plain data, no numpy
    from .spinner import SPINNERS
    from .term import ColorMode, Terminal
except KeyboardInterrupt:
    raise SystemExit(130) from None

DEMOS: dict[str, Callable[[], Source]] = {"bounce": Bounce}

SAMPLE_ART = "▄▀█ █▀ █▀▀ █ █ █▀▀ ▀▄▀\n█▀█ ▄█ █▄▄ █ █ █▀  █ █\n\nterminal art & animation"

# Every block checks for a real terminal: interactive, stdin and stdout a tty,
# and not TERM=dumb (Emacs M-x shell and other line-based "terminals" that show
# escape sequences as text and can't send a lone key press).
ZSH_BANNER = """\
# >>> asciifx startup banner >>>
# Animated banner for every new interactive shell; any key skips it.
# Disable for one shell with: ASCIIFX_NO_BANNER=1 zsh
if [[ -o interactive && -t 0 && -t 1 && ${{TERM:-dumb}} != dumb && -z ${{ASCIIFX_NO_BANNER-}} ]] && (( ${{+commands[asciifx]}} )); then
  asciifx startup{args}
fi
# <<< asciifx startup banner <<<
"""

BASH_BANNER = """\
# >>> asciifx startup banner >>>
# Animated banner for every new interactive shell; any key skips it.
# Disable for one shell with: ASCIIFX_NO_BANNER=1 bash
if [[ $- == *i* && -t 0 && -t 1 && ${{TERM:-dumb}} != dumb && -z ${{ASCIIFX_NO_BANNER-}} ]] && command -v asciifx >/dev/null 2>&1; then
  asciifx startup{args}
fi
# <<< asciifx startup banner <<<
"""

# zsh only runs TRAPALRM after TMOUT seconds *since the prompt appeared* (even
# mid-typing), and exits the shell if TMOUT is set without a TRAPALRM. So:
# define the trap first, record every keystroke via a zle hook, and when the
# alarm fires too early, re-arm it for the remaining idle time instead.
# EPOCHSECONDS is whole seconds: a difference of N means at least N - 1 s, so
# wait for N + 1 to be sure of N.
# TMOUT may come from the environment (hardened systems export it, often
# readonly): readonly, it can't be set, and the failed assignment would abort the
# rest of ~/.zshrc, so the block is skipped; exported, child shells would inherit
# the short re-arm timeouts and bash would log out after a few idle seconds, so
# it is un-exported first.
# zsh < 5.3 has no add-zle-hook-widget: without keystroke tracking the alarm
# would fire mid-typing, so the block is skipped there.
# A screensaver killed by a signal it can't catch (kill -9) never leaves its
# alternate screen; the trap does that for it. zsh itself restores the tty
# modes after a command that fails, and a repeated "leave" is harmless.
ZSH_SCREENSAVER = """\
# >>> asciifx screensaver >>>
# Full-screen screensaver after {idle} s without typing at the prompt; any key returns.
# Disable for one shell with: ASCIIFX_NO_SCREENSAVER=1 zsh
if [[ -o interactive && -t 0 && -t 1 && ${{TERM:-dumb}} != dumb && -z ${{ASCIIFX_NO_SCREENSAVER-}}
      && ${{(t)TMOUT-}} != *readonly* ]] && (( ${{+commands[asciifx]}} )) \\
    && autoload -Uz is-at-least && is-at-least 5.3; then  # 5.3+: add-zle-hook-widget
  zmodload zsh/datetime
  typeset -g +x TMOUT  # never pass the short timeouts below on to child shells
  typeset -gi _asciifx_idle_after={idle} _asciifx_last_key=$EPOCHSECONDS
  _asciifx_keypress() {{ _asciifx_last_key=$EPOCHSECONDS }}
  autoload -Uz add-zle-hook-widget
  add-zle-hook-widget line-init _asciifx_keypress
  add-zle-hook-widget line-pre-redraw _asciifx_keypress
  TRAPALRM() {{
    local idle=$(( EPOCHSECONDS - _asciifx_last_key ))
    if (( idle <= _asciifx_idle_after )); then
      TMOUT=$(( _asciifx_idle_after - idle + 1 ))  # typed recently: check again later
      return
    fi
    TMOUT=$_asciifx_idle_after
    asciifx screensaver{args} --idle-since $_asciifx_last_key
    (( $? > 128 )) && print -n -- '\\e[?2026l\\e[0m\\e[?7h\\e[?25h\\e[?1049l'  # killed: leave its screen
    _asciifx_last_key=$EPOCHSECONDS
    zle && zle reset-prompt
  }}
  TMOUT=$_asciifx_idle_after
fi
# <<< asciifx screensaver <<<
"""


# tmux runs lock-command in every client after lock-after-time idle seconds,
# even while programs run in panes, and returns to the session when it exits.
TMUX_SNIPPET = """\
# >>> asciifx screensaver >>>
set -g lock-after-time {idle}
set -g lock-command {command}
# <<< asciifx screensaver <<<
"""


def _positive(value: str) -> float:
    x = float(value)
    if x <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value}")
    return x


def _positive_int(value: str) -> int:
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value}")
    return n


def _non_negative_int(value: str) -> int:
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return n


def _tmux_quote(s: str) -> str:
    """``s`` as a tmux config string, taken literally (tmux expands $VARS in double quotes)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def _non_negative(value: str) -> float:
    x = float(value)
    if x < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return x


def _rgb(value: str) -> tuple[int, int, int]:
    try:
        r, g, b = (int(v) for v in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected R,G,B like 200,20,30, got {value}") from None
    if not all(0 <= v <= 255 for v in (r, g, b)):
        raise argparse.ArgumentTypeError(f"color values must be 0..255, got {value}")
    return r, g, b


def _style_list(value: str) -> tuple[str, ...]:
    styles = tuple(s.strip() for s in value.split(",") if s.strip())
    unknown = [s for s in styles if s not in STYLES]
    if unknown or not styles:
        raise argparse.ArgumentTypeError(f"unknown style(s) {', '.join(unknown)}; choose from {', '.join(STYLES)}")
    return styles


def _gradient(value: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    parts = value.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected TOP:BOTTOM like 255,255,255:200,20,30, got {value}")
    return _rgb(parts[0]), _rgb(parts[1])


def _color_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--color",
        choices=["auto", *(m.value for m in ColorMode)],
        default="auto",
        help="color mode (default: auto, from $COLORTERM)",
    )


def _terminal(args: argparse.Namespace, **kwargs: Any) -> Terminal:
    color = None if args.color == "auto" else ColorMode(args.color)
    return Terminal(color_mode=color, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asciifx", description="Terminal ASCII art & animation.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("play", help="play a demo or a text/ANSI-art animation file full-screen (any key quits)")
    p.add_argument("source", nargs="?", default="bounce",
                   help=f"demo name ({', '.join(sorted(DEMOS))}) or a text-frame file / directory of .txt frames")
    p.add_argument("--fps", type=_positive, default=DEFAULT_FPS, help="render frames per second (default: 30)")
    p.add_argument("--rate", type=_positive, default=10.0,
                   help="text-frame animation frames per second (default: 10; a '# asciifx fps=N' header wins)")
    p.add_argument("--no-loop", action="store_true", help="text frames: play once and stop")
    p.add_argument("--duration", type=_non_negative, help="stop after this many seconds")
    _color_arg(p)

    p = sub.add_parser("video", help="play a video file as text art full-screen (any key quits)")
    p.add_argument("path")
    p.add_argument("--style", choices=list(STYLES), help="named look (overrides --mode)")
    p.add_argument("--mode", choices=["ramp", "half", "braille", "glyph"], default="half")
    p.add_argument("--no-loop", action="store_true", help="play once and stop")
    p.add_argument("--speed", type=_positive, default=1.0, help="playback speed (default: 1)")
    p.add_argument("--invert", action="store_true", help="invert brightness")
    p.add_argument("--tint", type=_rgb, help="color every glyph R,G,B")
    p.add_argument("--threshold", type=float, help="brightness cutoff 0..1 (braille)")
    p.add_argument("--dither", action="store_true", help="dither braille dots")
    p.add_argument("--no-color", action="store_true", help="glyphs only, no colors")
    p.add_argument("--duration", type=_non_negative, help="stop after this many seconds")
    _color_arg(p)

    p = sub.add_parser("img", help="print an image as ASCII/Unicode art")
    p.add_argument("path")
    p.add_argument("--style", choices=[*STYLES, "all"],
                   help="named look (overrides --mode etc.); 'all' prints every style")
    p.add_argument("--mode", choices=["ramp", "half", "braille", "glyph"], default="half")
    p.add_argument("--charset", help="glyph mode: characters to use (default: all printable ASCII)")
    p.add_argument("--fill", action="store_true", help="glyph mode: every inked cell gets a glyph")
    p.add_argument("--outline", action="store_true", help="draw only the outline of the subject")
    p.add_argument("--banner-look", action="store_true",
                   help="exactly as the startup banner draws a logo (invert, no background, cropped, red gradient)")
    p.add_argument("--width", type=_positive_int, help="max columns (default: terminal width)")
    p.add_argument("--height", type=_positive_int, help="max rows")
    p.add_argument("--invert", action="store_true", help="invert brightness (dark ink on white)")
    p.add_argument("--transparent-bg", action="store_true", help="drop the background around the subject")
    p.add_argument("--autocrop", action="store_true", help="trim empty borders")
    p.add_argument("--despeckle", action="store_true", help="remove tiny stray specks")
    p.add_argument("--dither", action="store_true", help="dither braille dots")
    p.add_argument("--threshold", type=float, default=0.5, help="brightness cutoff 0..1 (braille, default 0.5)")
    p.add_argument("--tint", type=_rgb, help="color every glyph R,G,B (e.g. 200,20,30)")
    p.add_argument("--no-color", action="store_true", help="glyphs only, no colors")
    _color_arg(p)

    p = sub.add_parser("fx", help="demo an effect full-screen, looping (any key quits)")
    p.add_argument("effect", help="effect name (see `asciifx fx --list`)", nargs="?")
    p.add_argument("--list", action="store_true", help="list effects")
    p.add_argument("--duration", type=_non_negative, help="stop after this many seconds")
    p.add_argument("--palette", choices=["blood", "green"], help="color scheme (default: the effect's own)")
    p.add_argument("--endless", action="store_true", help="fire/starfield: run forever instead of looping")
    p.add_argument("--over", action="store_true", help="fire/starfield: draw over the art instead of behind it")
    p.add_argument("--warp", type=_positive, help="starfield: speed multiplier")
    _color_arg(p)

    p = sub.add_parser("banner", help="show text inline with an effect (any key skips)")
    p.add_argument("text", help="text to show; \\n for new lines")
    p.add_argument("--effect", help="effect name (default: decode)", default="decode")
    p.add_argument("--duration", type=_non_negative, help="seconds (default: the effect's length)")
    p.add_argument("--font", default="block", help="big-letter font: block, outline, small, ascii, or plain")
    p.add_argument("--fg", type=_rgb, help="text color R,G,B (default: white-to-blood-red fade)")
    p.add_argument("--gradient", type=_gradient, metavar="R,G,B:R,G,B", help="top:bottom color fade")
    p.add_argument("--align", choices=["left", "center", "right"], default="left")
    _color_arg(p)

    p = sub.add_parser("startup", help="hacker-style shell startup banner (any key skips)")
    p.add_argument("--logo", default=os.environ.get("ASCIIFX_LOGO"),
                   help="image to use as the logo (default: $ASCIIFX_LOGO)")
    p.add_argument("--style", choices=list(STYLES), help="logo style (default: dots)")
    p.add_argument("--duration", type=_non_negative, default=3.5, help="seconds (default: 3.5)")
    _color_arg(p)

    p = sub.add_parser("screensaver", help="full-screen screensaver: logo scenes, rain clock, system monitor (any key quits)")
    p.add_argument("--logo", default=os.environ.get("ASCIIFX_LOGO"),
                   help="image to use as the logo (default: $ASCIIFX_LOGO)")
    p.add_argument("--styles", type=_style_list, default=("dots", "sketch", "outline"),
                   help=f"logo styles to re-render through, comma-separated (default: dots,sketch,outline; "
                        f"choose from {','.join(STYLES)})")
    p.add_argument("--rain-seconds", type=_positive, default=30.0, help="length of the rain phase (default: 30)")
    p.add_argument("--fps", type=_positive, default=20.0, help="frames per second (default: 20)")
    p.add_argument("--duration", type=_non_negative, help="stop after this many seconds")
    p.add_argument("--seed", type=int, help="fixed seed for repeatable runs (default: different every run)")
    p.add_argument("--scenes", help="scenes to rotate through, comma-separated "
                                    "(default: skull,warp,inferno,hologram,sysmon)")
    p.add_argument("--palette", choices=["auto", "blood", "green"], default="auto",
                   help="auto = blood red 07:00-19:00, green at night (default: auto)")
    p.add_argument("--idle-since", type=float, metavar="EPOCH", help="when the user went idle (shown in the corner)")
    p.add_argument("--idle-for", type=_non_negative, metavar="SECONDS", help="idle this long already (for tmux)")
    p.add_argument("--no-clock", action="store_true", help="don't carve the time into the rain")
    p.add_argument("--clock-outline", action="store_true", help="faint dotted outline around the clock digits")
    p.add_argument("--no-clock-outline", action="store_true", help=argparse.SUPPRESS)  # the default now; still accepted
    p.add_argument("--no-idle", action="store_true", help="hide the 'idle 14m · user@host' line")
    _color_arg(p)

    p = sub.add_parser("spin", help="demo an inline spinner (any key ends it)")
    p.add_argument("--style", choices=list(SPINNERS), default="dots")
    p.add_argument("--seconds", type=_non_negative, default=3.0, help="how long to spin (default: 3)")
    p.add_argument("--text", default="summoning", help="message next to the spinner")
    _color_arg(p)

    p = sub.add_parser("summon", help="special ~13 s logo show (random variant each run), then leaves the banner")
    p.add_argument("--logo", default=os.environ.get("ASCIIFX_LOGO"),
                   help="image to use as the logo (default: $ASCIIFX_LOGO)")
    p.add_argument("--style", choices=list(STYLES), default="dots", help="logo style (default: dots)")
    p.add_argument("--no-banner", action="store_true", help="don't leave the banner behind afterwards")
    p.add_argument("--seed", type=int, help="fixed seed for a repeatable show")
    p.add_argument("--name", default=os.environ.get("ASCIIFX_NAME"),
                   help="name revealed under the logo (default: $ASCIIFX_NAME, else your username; '' = none)")
    p.add_argument("--assembly", choices=["scatter", "vortex", "explosion"], help="how the logo forms (default: random)")
    p.add_argument("--finale", choices=["shockwave", "lightning", "eyes"], help="finale (default: random)")
    _color_arg(p)

    p = sub.add_parser("shell-init", help="print the zsh, bash or tmux snippet for the banner and screensaver")
    p.add_argument("shell", choices=["zsh", "bash", "tmux"],
                   help="zsh: ~/.zshrc blocks; bash: ~/.bashrc blocks (banner only); tmux: ~/.tmux.conf lock")
    p.add_argument("--name", default=os.environ.get("ASCIIFX_NAME"),
                   help="name for `asciifx summon`'s reveal (exported as ASCIIFX_NAME)")
    p.add_argument("--logo", default=os.environ.get("ASCIIFX_LOGO"),
                   help="image to use as the logo (default: $ASCIIFX_LOGO)")
    p.add_argument("--no-banner", action="store_true", help="leave out the startup banner")
    p.add_argument("--style", choices=list(STYLES), help="logo style for the startup banner")
    p.add_argument("--screensaver-after", type=_non_negative_int, default=0, metavar="SECONDS",
                   help="start the screensaver after this many idle seconds at the prompt (0 = off)")
    return parser


def _cmd_play(args: argparse.Namespace) -> int:
    if args.source in DEMOS:
        source: Any = DEMOS[args.source]()
        duration = args.duration
    elif os.path.exists(args.source):
        from .sources.textframes import TextFramesSource

        try:
            source = TextFramesSource(args.source, fps=args.rate, loop=not args.no_loop)
        except (OSError, ValueError) as exc:
            print(f"asciifx: {exc}", file=sys.stderr)
            return 2
        duration = args.duration if args.duration is not None or not args.no_loop else source.duration
    else:
        print(f"asciifx: {args.source!r} is neither a demo ({', '.join(sorted(DEMOS))}) nor a file",
              file=sys.stderr)
        return 2
    play(source, fps=args.fps, duration=duration, terminal=_terminal(args))
    return 0


def _cmd_video(args: argparse.Namespace) -> int:
    from .sources.video import FFmpegNotFoundError, VideoSource

    options: dict[str, Any] = {"color": not args.no_color, "invert": args.invert, "dither": args.dither}
    if args.tint:
        options["tint"] = args.tint
    if args.threshold is not None:
        options["threshold"] = args.threshold
    try:  # before play(): errors print on the normal screen, not the alternate one
        video = VideoSource(args.path, style=args.style, mode=args.mode, loop=not args.no_loop,
                            speed=args.speed, **options)
    except (FFmpegNotFoundError, FileNotFoundError, ValueError) as exc:
        print(f"asciifx: {exc}", file=sys.stderr)
        return 2
    with video:  # always stops ffmpeg and the reader thread
        duration = args.duration if args.duration is not None or video.loop else video.duration
        play(video, duration=duration, terminal=_terminal(args))
    return 0


def _cmd_img(args: argparse.Namespace) -> int:
    from .sources.image import image_to_grid

    term = _terminal(args, fullscreen=False)
    cols, rows = term.size()
    show_all = args.style == "all"
    width = min(args.width or cols, cols) if term.is_tty else args.width or cols  # piped: no screen to fit
    height = min(args.height or rows - 3, rows - 3) if term.is_tty else args.height
    options: dict[str, Any] = dict(
        mode=args.mode, color=not args.no_color, tint=args.tint, invert=args.invert,
        threshold=args.threshold, transparent_bg=args.transparent_bg, autocrop=args.autocrop,
        despeckle=args.despeckle, dither=args.dither, fill=args.fill, outline=args.outline,
    )
    if args.charset:
        options["charset"] = args.charset
    if args.banner_look:
        from .startup import BLOOD, LOGO_PARAMS, recolor

        options.update(LOGO_PARAMS)
    for style in (list(STYLES) if show_all else [args.style]):
        w = width if args.width or not show_all else min(preferred_size(style)[0], cols)
        try:
            grid = image_to_grid(args.path, w, height, style=style, **options)
        except (OSError, ValueError) as exc:  # missing / unreadable / not an image, bad option value
            print(f"asciifx: {exc}", file=sys.stderr)
            return 2
        if args.banner_look:
            grid = recolor(grid, BLOOD.logo, BLOOD.logo_bottom)
        if show_all:  # label above each version
            labeled = Grid(max(grid.width, len(style)), grid.height + 1)
            labeled.put_text(0, 0, style, fg=(225, 25, 35))
            for y, row in enumerate(grid.rows(), start=1):
                for x, cell in enumerate(row):
                    labeled.set(x, y, cell)
            grid = labeled
        animate_inline(lambda t, g=grid: g, 0.0, terminal=_terminal(args, fullscreen=False))
    return 0


@dataclass
class _EffectDemo:
    """Full-screen sample art run through an effect, looping with a short hold."""

    effect: Any
    hold: float = 1.5

    def frame(self, t: float, width: int, height: int) -> Grid:
        art = Grid.from_text(SAMPLE_ART, fg=(235, 60, 60))
        grid = Grid(width, height)
        x, y = (width - art.width) // 2, (height - art.height) // 2
        for row, line in enumerate(SAMPLE_ART.split("\n")):
            grid.put_text(x, y + row, line, fg=(235, 60, 60) if row < 2 else (200, 200, 200))
        period = self.effect.end_for(grid) + self.hold
        return self.effect.apply(grid, t % period)


def _cmd_fx(args: argparse.Namespace) -> int:
    from .effects import EFFECTS

    if args.list or not args.effect:
        print("\n".join(sorted(EFFECTS)))
        return 0
    if args.effect not in EFFECTS:
        print(f"asciifx: unknown effect {args.effect!r}; try: {', '.join(sorted(EFFECTS))}", file=sys.stderr)
        return 2
    from dataclasses import fields

    from .effects import make

    types = {f.name: str(f.type) for f in fields(EFFECTS[args.effect])}
    params: dict[str, Any] = {}
    if args.endless and "None" in types.get("duration", ""):  # only effects whose duration may be None
        params["duration"] = None
    if args.over and "layer" in types:
        params["layer"] = "over"
    if args.warp is not None and "warp" in types:
        params["warp"] = args.warp
    effect = make(args.effect, args.palette, **params)
    play(_EffectDemo(effect), duration=args.duration, terminal=_terminal(args))
    return 0


def _cmd_banner(args: argparse.Namespace) -> int:
    from .effects import EFFECTS

    if args.effect not in EFFECTS:
        print(f"asciifx: unknown effect {args.effect!r}; try: {', '.join(sorted(EFFECTS))}", file=sys.stderr)
        return 2
    from .fonts import FONTS

    if args.font != "plain" and args.font.lower() not in FONTS:
        print(f"asciifx: unknown font {args.font!r}; try: {', '.join([*FONTS, 'plain'])}", file=sys.stderr)
        return 2
    text = args.text.replace("\\n", "\n")
    gradient = args.gradient
    if gradient is None and args.fg is None:
        from .startup import BLOOD as THEME

        gradient = (THEME.logo, THEME.logo_bottom)
    banner(text, font=None if args.font == "plain" else args.font, fg=args.fg,
           gradient=None if args.fg else gradient, align=args.align, effect=EFFECTS[args.effect](),
           duration=args.duration, terminal=_terminal(args, fullscreen=False))
    return 0


def _cmd_startup(args: argparse.Namespace) -> int:
    from . import startup

    try:
        startup.run(args.logo, duration=args.duration, style=args.style,
                    terminal=_terminal(args, fullscreen=False))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # never spew a traceback into every new shell
        if os.environ.get("ASCIIFX_DEBUG"):
            raise
        print(f"asciifx: startup banner failed: {exc} (set ASCIIFX_DEBUG=1 for details)", file=sys.stderr)
        return 1
    return 0


def _cmd_screensaver(args: argparse.Namespace) -> int:
    from .screensaver import SCENES, Screensaver, default_loader

    scenes = tuple(SCENES) if not args.scenes else tuple(x.strip() for x in args.scenes.split(",") if x.strip())
    unknown = [x for x in scenes if x not in SCENES]
    if unknown or not scenes:
        print(f"asciifx: unknown scene(s) {', '.join(unknown)}; choose from {', '.join(SCENES)}", file=sys.stderr)
        return 2
    idle_since = args.idle_since
    if idle_since is None and args.idle_for is not None:
        idle_since = time.time() - args.idle_for
    seed = args.seed if args.seed is not None else int(time.time())
    loader = default_loader(args.logo) if args.logo else None  # no logo: rain only
    saver = Screensaver(loader=loader, styles=args.styles, rain_seconds=args.rain_seconds, seed=seed,
                        scenes=scenes, palette=args.palette, idle_since=idle_since,
                        show_clock=not args.no_clock, clock_outline=args.clock_outline and not args.no_clock_outline,
                        show_idle=not args.no_idle)
    play(saver, fps=args.fps, duration=args.duration, terminal=_terminal(args))
    return 0


def _cmd_spin(args: argparse.Namespace) -> int:
    from .spinner import demo

    demo(args.style, args.seconds, args.text, terminal=_terminal(args, fullscreen=False))
    return 0


def _cmd_summon(args: argparse.Namespace) -> int:
    from . import startup
    from .screensaver import default_loader
    from .summon import RECOMMENDED_FPS, Summon

    from .logofeatures import load_features

    seed = args.seed if args.seed is not None else int(time.time())
    name = args.name
    if name is None:
        import getpass

        try:
            name = getpass.getuser()
        except Exception:  # no user info available (containers etc.)
            name = ""
    features = None
    if args.logo:
        try:  # eye/jaw positions from <logo>.asciifx.json, if present
            features = load_features(args.logo)
        except ValueError as exc:
            print(f"asciifx: ignoring logo features: {exc}", file=sys.stderr)
    show = Summon(loader=default_loader(args.logo) if args.logo else None, style=args.style, seed=seed,
                  name=name or None, assembly=args.assembly, finale=args.finale, features=features)
    term = _terminal(args)
    if term.is_tty:  # piped: skip the (black) first frame, just print the banner below
        play(show, fps=RECOMMENDED_FPS, duration=show.duration_for(*term.size()), terminal=term)
    if not args.no_banner:  # leave the finished banner in the terminal, like the startup one
        term = _terminal(args, fullscreen=False)
        cols, rows = term.size()
        try:
            banner = startup.build(cols, rows, args.logo, style=args.style)
        except (OSError, ValueError) as exc:  # not a readable image (the show above did without it)
            print(f"asciifx: {exc}", file=sys.stderr)
            return 2
        if banner is not None:
            final = banner.final()
            animate_inline(lambda t: final, 0.0, terminal=term)
    return 0


def _cmd_shell_init(args: argparse.Namespace) -> int:
    logo = f" --logo {shlex.quote(os.path.abspath(os.path.expanduser(args.logo)))}" if args.logo else ""
    if args.shell == "tmux":
        idle = args.screensaver_after or 120
        command = _tmux_quote(f"asciifx screensaver{logo} --idle-for {idle}")
        print(TMUX_SNIPPET.format(command=command, idle=args.screensaver_after or 120), end="")
        return 0
    banner_args = logo + (f" --style {args.style}" if args.style else "")
    blocks = []
    if args.logo:  # lets `asciifx summon` / `screensaver` / `startup` find the logo without --logo
        path = shlex.quote(os.path.abspath(os.path.expanduser(args.logo)))
        blocks.append(f"# >>> asciifx logo >>>\nexport ASCIIFX_LOGO={path}\n# <<< asciifx logo <<<\n")
    if args.name:
        blocks.append(f"# >>> asciifx name >>>\nexport ASCIIFX_NAME={shlex.quote(args.name)}\n# <<< asciifx name <<<\n")
    if not args.no_banner:
        blocks.append((BASH_BANNER if args.shell == "bash" else ZSH_BANNER).format(args=banner_args))
    if args.screensaver_after > 0:
        if args.shell == "bash":  # bash's TMOUT exits the shell instead of calling a trap
            print("asciifx: the idle screensaver needs zsh (or tmux: `shell-init tmux`); skipped for bash",
                  file=sys.stderr)
        else:
            blocks.append(ZSH_SCREENSAVER.format(args=logo, idle=args.screensaver_after))
    print("\n".join(blocks), end="")
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "play": _cmd_play,
    "img": _cmd_img,
    "video": _cmd_video,
    "fx": _cmd_fx,
    "banner": _cmd_banner,
    "startup": _cmd_startup,
    "screensaver": _cmd_screensaver,
    "summon": _cmd_summon,
    "spin": _cmd_spin,
    "shell-init": _cmd_shell_init,
}


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)  # in here too: building the parser takes ~25 ms
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # piped into `head` & co., which stopped reading: no traceback
        try:  # Python flushes stdout at exit, which would fail (and complain) again
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 141  # 128 + SIGPIPE, as for tools that die of it
