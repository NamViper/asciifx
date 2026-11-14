"""`asciifx shell-init zsh`: generated snippets are valid zsh and guard against footguns."""

import shutil
from pathlib import Path
import subprocess

import pytest

from asciifx.cli import main


def snippet(capsys, *args):
    assert main(["shell-init", "zsh", *args]) == 0
    return capsys.readouterr().out


def test_banner_only_by_default(capsys):
    out = snippet(capsys, "--logo", "/tmp/my logo.png")
    assert "asciifx startup --logo '/tmp/my logo.png'" in out  # shell-quoted
    assert "TMOUT" not in out


def test_screensaver_block(capsys):
    out = snippet(capsys, "--logo", "/x/skull.png", "--screensaver-after", "120", "--no-banner")
    assert "asciifx startup" not in out
    assert "typeset -gi _asciifx_idle_after=120" in out
    assert "asciifx screensaver --logo /x/skull.png --idle-since $_asciifx_last_key" in out
    # zsh exits the shell if TMOUT is set without a TRAPALRM: the trap must come first
    assert out.index("TRAPALRM()") < out.index("  TMOUT=$_asciifx_idle_after\nfi")


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
def test_snippets_are_valid_zsh(capsys, tmp_path):
    f = tmp_path / "snippet.zsh"
    f.write_text(snippet(capsys, "--logo", "/x/skull.png", "--screensaver-after", "120"))
    assert subprocess.run(["zsh", "-n", str(f)], capture_output=True).returncode == 0


def test_shell_init_passes_banner_style(capsys):
    out = snippet(capsys, "--logo", "/x/skull.png", "--style", "binary")
    assert "asciifx startup --logo /x/skull.png --style binary" in out


def test_screensaver_rejects_unknown_style(capsys):
    with pytest.raises(SystemExit) as info:
        main(["screensaver", "--styles", "dots,nope"])
    assert info.value.code == 2
    assert "nope" in capsys.readouterr().err


def test_img_all_styles_piped(capsys, monkeypatch):
    from pathlib import Path

    from asciifx.sources.styles import STYLES

    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "30")
    logo = Path(__file__).parent.parent / "skull_logo.png"
    if not logo.exists():
        pytest.skip("logo not present")
    assert main(["img", str(logo), "--style", "all", "--banner-look", "--width", "20"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out
    labels = [line for line in out.splitlines() if line in STYLES]
    assert labels == list(STYLES)


def test_tmux_lock_snippet(capsys):
    assert main(["shell-init", "tmux", "--logo", "/x/skull.png", "--screensaver-after", "300"]) == 0
    out = capsys.readouterr().out
    assert "set -g lock-after-time 300" in out
    assert 'set -g lock-command "asciifx screensaver --logo /x/skull.png --idle-for 300"' in out


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux not installed")
def test_tmux_snippet_is_valid_tmux_config(capsys, tmp_path):
    main(["shell-init", "tmux", "--screensaver-after", "120"])
    conf = tmp_path / "tmux.conf"
    conf.write_text(capsys.readouterr().out)
    sock = tmp_path / "sock"
    r = subprocess.run(["tmux", "-S", str(sock), "-f", str(conf), "start-server", ";", "show", "-g", "lock-after-time"],
                       capture_output=True, text=True)
    subprocess.run(["tmux", "-S", str(sock), "kill-server"], capture_output=True)
    assert "lock-after-time 120" in r.stdout, r.stderr


def test_zsh_snippet_exports_logo(capsys):
    out = snippet(capsys, "--logo", "/x/skull.png")
    assert "export ASCIIFX_LOGO=/x/skull.png" in out


def test_logo_defaults_to_env(monkeypatch):
    from asciifx.cli import build_parser

    monkeypatch.setenv("ASCIIFX_LOGO", "/env/logo.png")
    for cmd in ("startup", "screensaver", "summon"):
        assert build_parser().parse_args([cmd]).logo == "/env/logo.png"


def test_fx_options_only_reach_effects_that_take_them(monkeypatch):
    import asciifx.cli as cli

    seen = []
    monkeypatch.setattr(cli, "play", lambda source, **kw: seen.append(source.effect))
    assert main(["fx", "fire", "--endless", "--over", "--palette", "blood"]) == 0
    assert main(["fx", "starfield", "--warp", "3"]) == 0
    assert main(["fx", "matrix", "--endless", "--warp", "3", "--palette", "blood"]) == 0
    fire, stars, rain = seen
    assert fire.duration is None and fire.layer == "over" and fire.colors[-1] == (255, 220, 220)
    assert stars.warp == 3 and stars.duration == 4.0
    assert rain.duration == 1.5 and rain.head == (255, 220, 220)  # --endless/--warp don't apply to matrix


def test_cli_banner_defaults_to_block_font_with_theme_fade(capsys):
    from asciifx.fonts import render_text
    from asciifx.startup import BLOOD

    assert main(["banner", "HI", "--effect", "typewriter"]) == 0
    assert capsys.readouterr().out == render_text("HI", "block").to_text() + "\n"
    assert main(["banner", "HI", "--font", "plain", "--effect", "decode"]) == 0
    assert capsys.readouterr().out == "HI\n"
    assert main(["banner", "HI", "--font", "nope", "--effect", "decode"]) == 2
    assert BLOOD.logo and BLOOD.logo_bottom  # gradient source exists


def test_play_text_frames_file_piped(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("COLUMNS", "20")
    monkeypatch.setenv("LINES", "5")
    f = tmp_path / "anim.txt"
    f.write_text("AA\n---\nBB\n")
    assert main(["play", str(f), "--no-loop"]) == 0
    assert "AA" in capsys.readouterr().out  # piped: first frame as plain text


def test_play_unknown_source_is_an_error(capsys):
    assert main(["play", "definitely-not-a-demo-or-file"]) == 2
    assert "neither a demo" in capsys.readouterr().err


def test_video_missing_file_is_an_error(capsys, tmp_path):
    assert main(["video", str(tmp_path / "nope.mp4")]) == 2
    assert "asciifx:" in capsys.readouterr().err


def test_bash_snippet_banner_only_and_valid(capsys, tmp_path):
    assert main(["shell-init", "bash", "--logo", "/x/s.png", "--name", "Nam Viper", "--screensaver-after", "120"]) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "export ASCIIFX_LOGO=/x/s.png" in out and "export ASCIIFX_NAME='Nam Viper'" in out
    assert "asciifx startup --logo /x/s.png" in out and "TMOUT" not in out
    assert "needs zsh" in captured.err
    f = tmp_path / "b.sh"
    f.write_text(out)
    assert subprocess.run(["bash", "-n", str(f)], capture_output=True).returncode == 0


def test_zsh_snippet_exports_name(capsys):
    out = snippet(capsys, "--name", "NamViper")
    assert "export ASCIIFX_NAME=NamViper" in out


def test_screensaver_options_reach_the_screensaver(monkeypatch):
    import asciifx.cli as cli

    seen = []
    monkeypatch.setattr(cli, "play", lambda source, **kw: seen.append(source))
    assert main(["screensaver", "--scenes", "warp,sysmon", "--palette", "green", "--idle-for", "60",
                 "--no-clock", "--no-idle", "--seed", "1"]) == 0
    (saver,) = seen
    assert saver.scenes == ("warp", "sysmon") and saver.palette == "green"
    assert saver.show_clock is False and saver.show_idle is False and saver.idle_since is not None


def test_screensaver_rejects_unknown_scene(capsys):
    assert main(["screensaver", "--scenes", "skull,nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_summon_options_reach_the_show(monkeypatch, tmp_path):
    import asciifx.cli as cli

    seen = []
    monkeypatch.setattr(cli, "play", lambda source, **kw: seen.append(source))
    monkeypatch.setattr(cli, "_terminal", lambda args, **kw: type("T", (), {"is_tty": True, "size": lambda s: (120, 35)})())
    logo = Path(__file__).parent.parent / "skull_logo.png"
    assert main(["summon", "--logo", str(logo), "--name", "NamViper", "--assembly", "vortex",
                 "--finale", "eyes", "--no-banner", "--seed", "3"]) == 0
    (show,) = seen
    assert show.name == "NamViper" and show.assembly == "vortex" and show.finale == "eyes"
    assert show.features is not None  # skull_logo.asciifx.json sits next to the logo
