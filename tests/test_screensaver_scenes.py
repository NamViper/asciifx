"""Screensaver scene rotation, the new scenes, the carved clock, the idle line and
palettes; all at fixed t with a fake loader, a fixed clock and a fake monitor."""

import math
import time

import pytest

from asciifx.core import BLANK, Cell, Grid
from asciifx.effects import BLOOD, GREEN, Glitch, Hologram, Palette, Starfield
from asciifx.effects.base import gradient
from asciifx.fonts import render_text, text_width
from asciifx.screensaver import (
    CLOCK_FADE, HOLOGRAM_SCALE, OUTLINE_CHAR, SCALE_STEPS, SCENES, WARP_EMPTY, Screensaver,
    clock_mask, sysmon_panel,
)
from asciifx.sysmon import Stats

W, H = 100, 30
LOGO_FG = (40, 90, 250)  # in no palette, and its red/green swap is distinct too
LOGO_SIZE = (40, 16)
NOON = time.mktime((2026, 11, 12, 12, 34, 0, 0, 0, -1))  # local time
NIGHT = time.mktime((2026, 11, 12, 23, 5, 0, 0, 0, -1))
STATS = Stats(
    time=1.0, cpu=37.0, cores=(10.0, 90.0), ncpu=2, mem_total=8 * 1024**3, mem_used=2 * 1024**3,
    swap_total=1024**3, swap_used=0, load=(0.52, 0.61, 0.7), uptime=3 * 86400 + 3600, tasks=312,
    running=3, net_rx=2048.0, net_tx=10.0, top=(("python3", 75.0), ("zsh", 1.5)),
    history=(5.0, 50.0, 100.0),
)


class FakeMonitor:
    def __init__(self, stats: Stats = STATS) -> None:
        self.calls = 0
        self._stats = stats

    def stats(self) -> Stats:
        self.calls += 1
        return self._stats


class FakeLoader:
    """The logo fitted into the box with LOGO_SIZE's aspect: a checkered block of '#'."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def __call__(self, style: str, max_w: int, max_h: int) -> Grid:
        self.calls.append((style, max_w, max_h))
        k = min(1.0, max_w / LOGO_SIZE[0], max_h / LOGO_SIZE[1])
        w, h = max(1, round(LOGO_SIZE[0] * k)), max(1, round(LOGO_SIZE[1] * k))
        g = Grid(w, h)
        for y in range(h):
            for x in range(w):
                if (x + y) % 4:
                    g.set(x, y, Cell("#", LOGO_FG))
        return g


def saver(**kw) -> Screensaver:
    kw.setdefault("styles", ("dots",))
    kw.setdefault("palette", "blood")
    kw.setdefault("clock", lambda: NOON)
    kw.setdefault("show_idle", False)
    kw.setdefault("show_clock", False)
    kw.setdefault("monitor", FakeMonitor())
    return Screensaver(kw.pop("loader", FakeLoader()), **kw)


def find(ss: Screensaver, scene: str, name: str, nth: int = 0):
    """The nth phase called ``name`` of ``scene`` over the first cycles."""
    found = [p for k in range(6) for p in ss.plan(k) if p.scene == scene and p.name == name]
    return found[nth]


def at(phase, u: float) -> float:
    return phase.start + u * (phase.end - phase.start)


def quiet(phase, u: float = 0.5) -> float:
    """A time from u on inside a phase whose effect is a Glitch, outside every burst."""
    t = at(phase, u)
    if isinstance(phase.effect, Glitch):
        while phase.effect.burst_at(t) is not None or phase.effect.burst_at(t + 1e-6) is not None:
            t += 0.01
    assert t < phase.end
    return t


def bbox(g: Grid, test) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) inclusive of the cells passing ``test``."""
    pts = [(i % g.width, i // g.width) for i, c in enumerate(g.cells) if test(c)]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def is_logo(c: Cell) -> bool:
    return c.fg == LOGO_FG


def lit(g: Grid) -> float:
    return sum(c.ch != " " for c in g.cells) / len(g.cells)


def full_logo(width: int = W, height: int = H) -> tuple[int, int]:
    g = FakeLoader()("dots", width - 4, height - 2)
    return g.width, g.height


# --- timeline -------------------------------------------------------------------


def test_default_scenes_and_cycle_structure():
    ss = saver()
    assert ss.scenes == SCENES == ("skull", "warp", "inferno", "hologram", "sysmon")
    plan = ss.plan(0)
    assert plan[0].start == 0.0 and plan[-1].end == pytest.approx(ss.cycle_length)
    assert all(a.end == pytest.approx(b.start) for a, b in zip(plan, plan[1:]))
    scenes = [p.scene for p in plan]
    order = [s for i, s in enumerate(scenes) if s != "rain" and (i == 0 or scenes[i - 1] != s)]
    assert sorted(order) == sorted(SCENES) and tuple(order) == ss.scene_order(0)
    rains = [p for p in plan if p.scene == "rain"]
    assert len(rains) == 5 and all(r.end - r.start == pytest.approx(30.0) for r in rains)
    for i, p in enumerate(plan[:-1]):  # every scene is followed by rain
        if p.scene != "rain" and plan[i + 1].scene != p.scene:
            assert plan[i + 1].scene == "rain" and plan[i + 1].slot == p.slot
    assert ss.cycle_length == pytest.approx(sum(ss.scene_length(s) for s in SCENES) + 150.0)
    assert {p.slot for p in plan} == set(range(5))


def test_order_reshuffles_every_cycle_without_repeats():
    ss = saver()
    orders = [ss.scene_order(k) for k in range(40)]
    assert all(sorted(o) == sorted(SCENES) for o in orders)
    assert len(set(orders)) > 15
    for a, b in zip(orders, orders[1:]):
        assert a[-1] != b[0]  # never the same scene twice in a row across the seam
    assert saver().scene_order(7) == orders[7]  # seeded
    assert any(saver(seed=1).scene_order(k) != orders[k] for k in range(5))
    assert saver(scenes=("sysmon", "warp")).scene_order(3) == ("sysmon", "warp")


def test_phase_and_scene_introspection():
    ss = saver()
    for k in (0, 3):
        for p in ss.plan(k):
            for t in (p.start, (p.start + p.end) / 2, p.end - 1e-6):
                got = ss.phase_at(t)
                assert got == p and (got.scene, got.slot, got.cycle) == (p.scene, p.slot, k)
                assert ss.scene_at(t) == p.scene
    names = {(p.scene, p.name) for p in ss.plan(0)}
    assert {("warp", "approach"), ("warp", "cruise"), ("warp", "jump"), ("inferno", "ignite"),
            ("inferno", "rise"), ("inferno", "burn"), ("inferno", "sink"), ("hologram", "project"),
            ("hologram", "transmit"), ("hologram", "collapse"), ("sysmon", "boot"),
            ("sysmon", "monitor"), ("sysmon", "shutdown"), ("rain", "rain")} <= names
    warp = find(ss, "warp", "cruise")
    assert isinstance(warp.backdrop, Starfield) and warp.style == "dots"
    assert isinstance(find(ss, "hologram", "transmit").effect, Hologram)
    assert find(ss, "sysmon", "monitor").style is None


def test_validation():
    with pytest.raises(ValueError, match="unknown scene"):
        saver(scenes=("skull", "disco"))
    with pytest.raises(ValueError):
        saver(scenes=())
    with pytest.raises(ValueError):
        saver(palette="purple")
    assert saver(scenes=["warp", "warp", "skull"]).scenes == ("warp", "skull")


# --- warp -------------------------------------------------------------------------


def test_warp_flies_in_holds_and_jumps_away():
    loader = FakeLoader()
    ss = saver(loader=loader, scenes=("warp",))
    approach, cruise, jump = (find(ss, "warp", n) for n in ("approach", "cruise", "jump"))
    fw, fh = full_logo()
    stars_only = ss.frame(approach.start + WARP_EMPTY / 2, W, H)
    assert bbox(stars_only, is_logo) is None and lit(stars_only) > 0
    sizes = []
    for u in (0.4, 0.7, 0.9):
        box = bbox(ss.frame(at(approach, u), W, H), is_logo)
        assert box is not None
        sizes.append(box[2] - box[0] + 1)
    assert sizes == sorted(sizes) and sizes[-1] < fw  # growing, not yet full size
    hold = ss.frame(quiet(cruise), W, H)
    x0, y0 = (W - fw) // 2, (H - fh) // 2
    assert bbox(hold, is_logo) == (x0, y0, x0 + fw - 1, y0 + fh - 1)  # full size, centred
    outside = [c for i, c in enumerate(hold.cells) if not (x0 <= i % W < x0 + fw and y0 <= i // W < y0 + fh)]
    assert sum(c.ch != " " for c in outside) > 20  # stars around it
    away = bbox(ss.frame(at(jump, 0.5), W, H), is_logo)
    assert away is not None and away[2] - away[0] + 1 < fw / 2
    # the loader is only asked for the size steps, once each
    calls = set(loader.calls)
    box_w, box_h = W - 4, H - 2
    allowed = {("dots", max(1, round(box_w * s)), max(1, round(box_h * s))) for s in SCALE_STEPS}
    assert calls <= allowed and len(loader.calls) == len(calls)
    for u in (0.4, 0.7, 0.9):
        ss.frame(at(approach, u), W, H)
    assert len(loader.calls) == len(calls)


def test_warp_jump_streaks_harder():
    ss = saver(scenes=("warp",))
    cruise, jump = find(ss, "warp", "cruise"), find(ss, "warp", "jump")

    def streaks(t: float) -> int:
        return sum(c.ch in "-\\|/" for c in ss.frame(t, W, H).cells)

    assert streaks(at(jump, 0.8)) > 2 * streaks(quiet(cruise))


# --- inferno ------------------------------------------------------------------------


def test_inferno_rises_out_of_the_flames():
    ss = saver(scenes=("inferno",))
    ignite, rise, burn, sink = (find(ss, "inferno", n) for n in ("ignite", "rise", "burn", "sink"))
    first = ss.frame(at(ignite, 0.8), W, H)
    assert bbox(first, is_logo) is None
    assert sum(c.ch != " " for c in first.cells[(H - 3) * W :]) > W  # flames along the bottom
    tops = [bbox(ss.frame(t, W, H), is_logo)[1] for t in (at(rise, 0.3), at(rise, 0.7), quiet(burn))]
    assert tops[0] > tops[1] > tops[2]  # rising
    burning = ss.frame(quiet(burn), W, H)
    logo = ss.scaled_logo("dots", 0.8, W, H)
    assert sum(map(is_logo, burning.cells)) > 0.6 * sum(c.ch != " " for c in logo.cells)
    assert any(c.ch in "░▒▓█" for c in burning.cells)  # fire glyphs
    assert bbox(ss.frame(at(sink, 0.7), W, H), is_logo)[1] > tops[2]  # sinking back


# --- hologram -----------------------------------------------------------------------


def test_hologram_projects_flickers_and_collapses():
    ss = saver(scenes=("hologram",))
    project, transmit, collapse = (find(ss, "hologram", n) for n in ("project", "transmit", "collapse"))
    logo = ss.scaled_logo("dots", HOLOGRAM_SCALE, W, H)
    lh = logo.height
    y0 = max(0, (H - 3 - 2 - lh) // 2)

    def logo_rows(t: float) -> set[int]:
        g = ss.frame(t, W, H)
        return {y for y in range(y0, y0 + lh) if any(c.ch == "#" for c in g.cells[y * W : (y + 1) * W])}

    early = logo_rows(at(project, 0.2))
    assert early and min(early) > y0 + lh // 3  # built up from the bottom
    full = ss.frame(at(transmit, 0.5), W, H)
    rows = logo_rows(at(transmit, 0.5))
    assert len(rows) >= lh - 3  # all there (bar a dropout or two)
    fgs = {c.fg for c in full.cells if c.ch == "#"}
    assert any(fg[0] == 0 and fg[1] > 60 for fg in fgs)  # cyan ghost
    assert any(fg[0] > 3 * fg[1] for fg in fgs)  # red ghost
    assert "▄" in full.to_text().split("\n")[H - 3]  # the emitter
    late = logo_rows(at(collapse, 0.9))
    assert len(late) < lh // 3 and (not late or min(late) > y0 + lh // 2)


# --- sysmon -------------------------------------------------------------------------


def test_sysmon_scene_shows_the_monitor_panel():
    mon = FakeMonitor()
    ss = saver(scenes=("sysmon",), monitor=mon)
    boot, monitor, shutdown = (find(ss, "sysmon", n) for n in ("boot", "monitor", "shutdown"))
    t = quiet(monitor)
    out = ss.frame(t, W, H)
    assert out == sysmon_panel(STATS, W, H, BLOOD, "12:34:00")
    text = out.to_text()
    for s in ("SYSMON", "12:34:00", "CPU", "MEM", "LOAD", "TASKS  312", "running 3", "python3 75.0%",
              "2.0G / 8.0G", "3d 01h", "↓ 2.0K/s", "0.52  0.61  0.70"):
        assert s in text, s
    big = render_text("37%", "block")
    assert big.to_text().split("\n")[0].strip() in text  # the big CPU number
    assert mon.calls >= 1
    assert lit(ss.frame(boot.start, W, H)) < 0.2 * lit(out)  # decodes in
    assert lit(ss.frame(shutdown.end - 1e-3, W, H)) < 0.05 * lit(out)  # and out
    calls = mon.calls
    ss.frame(find(ss, "rain", "rain").start + 5, W, H)
    assert mon.calls == calls  # only read during the scene


def test_sysmon_panel_layouts_and_na():
    big = sysmon_panel(STATS, 200, 50, BLOOD, "x").to_text()
    small = sysmon_panel(STATS, 80, 20, BLOOD).to_text()
    assert "MEM" in big and "MEM" in small and "hist" in small and "history" in big
    assert "█" in small and "░" in small  # bars
    empty = sysmon_panel(Stats(), 80, 20, GREEN).to_text()
    assert "n/a" in empty and "--" in empty
    tiny = sysmon_panel(STATS, 20, 5, BLOOD)
    assert "CPU 37%" in tiny.to_text() and (tiny.width, tiny.height) == (20, 5)
    for w, h in ((200, 50), (80, 24), (61, 21), (40, 9)):
        g = sysmon_panel(STATS, w, h, BLOOD)
        assert (g.width, g.height) == (w, h) and len(g.cells) == w * h


# --- the clock carved in the rain ------------------------------------------------------


def rain_phase(ss: Screensaver):
    return find(ss, "rain", "rain")


def test_clock_mask_is_the_block_font_scaled_and_centred():
    inside, edge = clock_mask("12:34", W, H)
    assert set(edge) <= set(inside)
    xs, ys = [i % W for i in inside], [i // W for i in inside]
    pw = text_width("12:34", "block")
    k = (max(xs) - min(xs) + 1) // pw
    assert k >= 2 and max(xs) - min(xs) + 1 <= pw * k and max(ys) - min(ys) + 1 == 4 * k
    assert abs((min(xs) + max(xs)) / 2 - (W - 1) / 2) <= 1 and abs((min(ys) + max(ys)) / 2 - (H - 1) / 2) <= 1
    assert clock_mask("12:34", 10, 5) is None
    small = clock_mask("12:34", 34, 8)  # too small to scale: the font as is
    assert small is not None and len(small[0]) == sum(c.ch != " " for c in render_text("12:34").cells)


def test_rain_glyphs_inside_the_digits_are_bright():
    ss = saver(show_clock=True)
    rain = rain_phase(ss)
    inside, edge = clock_mask("12:34", W, H)
    inside_set = set(inside)
    out = ss.frame(rain.start + 6.0, W, H)
    lit_in = [out.cells[i] for i in inside if out.cells[i].ch not in (" ", OUTLINE_CHAR)]
    lit_out = [c for i, c in enumerate(out.cells) if i not in inside_set and c.ch != " "]
    assert lit_in and all(min(c.fg) >= 190 for c in lit_in)
    assert sum(min(c.fg) >= 190 for c in lit_out) < 0.15 * len(lit_out)
    plain = saver(show_clock=False).frame(rain.start + 6.0, W, H)
    assert [i for i in range(W * H) if out.cells[i] != plain.cells[i]] and all(
        i in inside_set for i in range(W * H) if out.cells[i] != plain.cells[i]
    )  # only the digits differ


def test_clock_is_readable_within_a_second():
    ss = saver(show_clock=True, clock_outline=False)
    rain = rain_phase(ss)
    inside = set(clock_mask("12:34", W, H)[0])
    acc = [0.0] * (W * H)
    for k in range(20):  # one second at 20 fps
        for i, c in enumerate(ss.frame(rain.start + 8.0 + k / 20, W, H).cells):
            if c.ch != " ":
                acc[i] += sum(c.fg) / 3
    mean_in = sum(acc[i] for i in inside) / len(inside)
    mean_out = sum(a for i, a in enumerate(acc) if i not in inside) / (W * H - len(inside))
    assert mean_in > 3 * mean_out


def test_outline_is_faint_persistent_and_fades():
    ss = saver(show_clock=True)
    rain = rain_phase(ss)
    _, edge = clock_mask("12:34", W, H)

    def outline(t: float) -> list[Cell]:
        g = ss.frame(t, W, H)
        return [g.cells[i] for i in edge if g.cells[i].ch == OUTLINE_CHAR]

    mid = outline(rain.start + 10.0)
    assert len(mid) > 0.3 * len(edge)
    assert all(max(c.fg) <= 130 for c in mid)  # faint
    early = outline(rain.start + CLOCK_FADE / 4)
    assert early and max(c.fg[0] for c in early) < mid[0].fg[0]
    assert not outline(rain.start)  # nothing at the very start
    assert not [c for c in saver(show_clock=True, clock_outline=False).frame(rain.start + 10, W, H).cells
                if c.ch == OUTLINE_CHAR]


def test_clock_comes_from_the_injected_clock():
    rain = rain_phase(saver())
    t = rain.start + 6.0
    a = saver(show_clock=True, clock=lambda: NOON).frame(t, W, H)
    b = saver(show_clock=True, clock=lambda: NOON + 60).frame(t, W, H)  # 12:35
    assert a == saver(show_clock=True, clock=lambda: NOON + 30).frame(t, W, H)  # same minute
    union = set(clock_mask("12:34", W, H)[0]) | set(clock_mask("12:35", W, H)[0])
    diff = [i for i in range(W * H) if a.cells[i] != b.cells[i]]
    assert diff and set(diff) <= union


def test_clock_only_during_rain_and_in_endless_rain():
    ss = saver(show_clock=True, scenes=("skull",))
    hold = find(ss, "skull", "hold")
    assert not any(c.ch == OUTLINE_CHAR for c in ss.frame(quiet(hold), W, H).cells)
    endless = saver(loader=None, show_clock=True)
    out = endless.frame(20.0, W, H)
    inside, _ = clock_mask("12:34", W, H)
    assert any(out.cells[i].ch == OUTLINE_CHAR for i in inside)
    assert all(min(out.cells[i].fg) >= 190 for i in inside if out.cells[i].ch not in (" ", OUTLINE_CHAR))


# --- idle line ---------------------------------------------------------------------------


def bottom(g: Grid) -> str:
    return g.to_text(rstrip=False).split("\n")[-1]


def test_idle_line_bottom_right():
    ss = saver(show_idle=True, idle_since=NOON - 840, user_host="me@box", scenes=("skull",))
    hold = find(ss, "skull", "hold")
    out = ss.frame(quiet(hold), W, H)
    assert bottom(out).endswith("idle 14m · me@box ")
    x = W - len("idle 14m · me@box") - 1
    assert out.get(x, H - 1) == Cell("i", BLOOD.tail[1])
    assert ss.idle_text(5.0, NOON) == "idle 14m · me@box"
    assert ss.idle_text(5.0, NOON + 3600) == "idle 1h14m · me@box"


def test_idle_counts_from_the_start_without_idle_since():
    ss = saver(show_idle=True, user_host="me@box")
    assert ss.idle_text(125.0, NOON) == "idle 2m · me@box"
    assert bottom(ss.frame(125.0, W, H)).endswith("idle 2m · me@box ")


def test_idle_line_shrinks_on_narrow_screens_and_can_be_off():
    ss = saver(show_idle=True, idle_since=NOON - 840, user_host="someone@a-long-hostname")
    assert bottom(ss.frame(3.0, 24, 10)).endswith("idle 14m ")
    assert "idle" not in ss.frame(3.0, 9, 10).to_text()
    off = saver(idle_since=NOON - 840).frame(50.0, W, H)
    assert "idle" not in off.to_text()
    assert "@" in Screensaver(None).idle_text(1.0, NOON)  # detected user@host


# --- palettes ------------------------------------------------------------------------------


def trail_colors(p: Palette) -> set:
    return {p.head} | {c for n in range(1, 40) for c in gradient(p.tail, n)}


def test_auto_palette_is_blood_by_day_and_green_at_night():
    day, night = saver(palette="auto", clock=lambda: NOON), saver(palette="auto", clock=lambda: NIGHT)
    assert day.palette_at() == BLOOD and night.palette_at() == GREEN
    for hour, want in ((6, GREEN), (7, BLOOD), (18, BLOOD), (19, GREEN), (0, GREEN)):
        assert day.palette_at(time.mktime((2026, 11, 12, hour, 59, 0, 0, 0, -1))) == want
    rain = rain_phase(night)
    out = night.frame(rain.start + 6.0, W, H)
    assert {c.fg for c in out.cells if c.ch != " "} <= trail_colors(GREEN)
    assert {c.fg for c in day.frame(rain.start + 6.0, W, H).cells if c.ch != " "} <= trail_colors(BLOOD)


def test_green_palette_recolors_logo_and_effects():
    night = saver(palette="auto", clock=lambda: NIGHT, scenes=("skull",))
    hold = find(night, "skull", "hold")
    out = night.frame(quiet(hold), W, H)
    swapped = (LOGO_FG[1], LOGO_FG[0], LOGO_FG[2])
    assert {c.fg for c in out.cells if c.ch != " "} == {swapped}
    assert hold.effect.colors == GREEN.glitch
    assert night.plan(0, BLOOD)[1].effect.colors == BLOOD.glitch  # plans per palette
    custom = Palette.mono((0, 0, 255))
    mine = saver(palette=custom, scenes=("skull",))
    assert mine.palette_at() is custom
    hold = find(mine, "skull", "hold")
    assert {c.fg for c in mine.frame(quiet(hold), W, H).cells if c.ch != " "} == {LOGO_FG}


def test_palette_follows_into_every_scene():
    for scene in ("warp", "inferno", "sysmon"):
        ss = saver(palette="green", scenes=(scene,))
        mid = ss.plan(0)[1]
        out = ss.frame(quiet(mid), W, H)
        reds = [c for c in out.cells if c.ch != " " and c.fg and c.fg[0] > c.fg[1] + 60]
        assert not reds, scene


# --- the skull in the rotation ----------------------------------------------------------------


def test_skull_in_rotation_and_matrix_intro_after_rain():
    ss = saver(styles=("dots", "letters"))
    hold = find(ss, "skull", "hold")
    out = ss.frame(quiet(hold), W, H)
    fw, fh = full_logo()
    assert bbox(out, is_logo) == ((W - fw) // 2, (H - fh) // 2, (W - fw) // 2 + fw - 1, (H - fh) // 2 + fh - 1)
    odd = next(p for k in (1, 3, 5) for p in ss.plan(k) if p.scene == "skull" and p.name == "intro")
    assert lit(ss.frame(odd.start + 0.05, W, H)) > 0.3  # the previous rain keeps falling into it
    even = find(ss, "skull", "intro")
    assert even.cycle % 2 == 0 and ss.frame(even.start, W, H) == Grid(W, H)


# --- determinism, caches, long runs ------------------------------------------------------------


def test_frames_are_deterministic_in_every_scene():
    a, b = (saver(show_clock=True, show_idle=True, user_host="u@h") for _ in range(2))
    times = [p.start + 0.37 * (p.end - p.start) for p in a.plan(0)]
    for t in times:
        assert a.frame(t, W, H) == b.frame(t, W, H)
    assert len({tuple(a.frame(t, W, H).cells) for t in times}) == len(times)


def test_caches_stay_bounded_over_long_runs():
    loader = FakeLoader()
    ss = saver(loader=loader, show_clock=True, show_idle=True, user_host="u@h",
               styles=("dots", "letters", "binary"), palette="auto",
               clock=lambda: NOON)  # fmt: skip
    c = ss.cycle_length
    times = [k * c + f * c for k in (0, 1, 7, 50, 400, 3000) for f in (0.05, 0.3, 0.55, 0.8)]
    times += [1e7, 1e7 + 0.05, 3.15e7]
    for t in times:
        for size in ((W, H), (80, 24), (120, 35)):
            out = ss.frame(t, *size)
            assert (out.width, out.height) == size
    assert len(ss._plans) <= 8 and len(ss._scenes) <= 4 and len(ss._morphs) <= 4
    assert len(ss._loaded) <= 40
    assert clock_mask.cache_info().currsize <= 8
    calls = len(loader.calls)
    for t in times[:4]:
        ss.frame(t, W, H)
    assert len(loader.calls) - calls <= 3 * len(SCALE_STEPS) + 3  # reloads only what was evicted
    assert math.isfinite(ss.cycle_length)


def test_tiny_and_empty_screens():
    ss = saver(show_clock=True, show_idle=True)
    for t in (0.0, 30.0, 100.0, 200.0):
        assert ss.frame(t, 0, 0) == Grid(0, 0)
        g = ss.frame(t, 10, 5)
        assert (g.width, g.height) == (10, 5)
    assert BLANK in ss.frame(0.0, 10, 5).cells
