"""Live system stats for the screensaver's monitor scene.

Reads ``/proc`` (Linux, WSL) and falls back gracefully elsewhere: the load
average comes from ``os.getloadavg`` (macOS has it too), the core count from
``os.cpu_count``, and anything else that can't be read is None (shown "n/a").
CPU, network and per-process percentages are deltas between two samples, so
the very first sample has none of them.

``SysMon.stats()`` re-samples at most once per ``interval`` seconds and
otherwise returns the cached sample; the busiest-process list is refreshed
more rarely still (``top_interval``). Memory use is bounded: a short CPU
history and the previous sample's counters only.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Stats", "SysMon", "fmt_bytes", "fmt_duration", "fmt_rate"]

_MAX_PIDS = 4096  # never scan more process entries than this per refresh


@dataclass(frozen=True)
class Stats:
    """One sample. Percentages are 0..100; None means unknown on this system."""

    time: float = 0.0  # when it was taken (the SysMon's clock)
    cpu: float | None = None  # all cores, since the previous sample
    cores: tuple[float, ...] = ()  # per core, since the previous sample
    ncpu: int = 1
    mem_total: int | None = None  # bytes
    mem_used: int | None = None  # bytes in use (total - available)
    swap_total: int | None = None
    swap_used: int | None = None
    load: tuple[float, float, float] | None = None  # 1, 5, 15 minutes
    uptime: float | None = None  # seconds since boot
    tasks: int | None = None  # processes
    running: int | None = None  # runnable right now
    net_rx: float | None = None  # bytes per second, all interfaces but loopback
    net_tx: float | None = None
    top: tuple[tuple[str, float], ...] = ()  # busiest processes: (command, cpu %)
    history: tuple[float, ...] = ()  # recent cpu %, oldest first

    @property
    def mem_percent(self) -> float | None:
        if not self.mem_total or self.mem_used is None:
            return None
        return 100.0 * self.mem_used / self.mem_total

    @property
    def swap_percent(self) -> float | None:
        if not self.swap_total or self.swap_used is None:
            return None
        return 100.0 * self.swap_used / self.swap_total


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except (OSError, ValueError):
        return None


def _cpu_times(text: str | None) -> tuple[tuple[int, int] | None, list[tuple[int, int]], int | None]:
    """(busy, total) jiffies for all cores and per core, and procs_running, from /proc/stat."""
    total: tuple[int, int] | None = None
    cores: list[tuple[int, int]] = []
    running = None
    for line in (text or "").splitlines():
        if line.startswith("cpu"):
            parts = line.split()
            try:
                nums = [int(v) for v in parts[1:9]]
            except ValueError:
                continue
            if len(nums) < 4:
                continue
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
            all_ = sum(nums)
            if parts[0] == "cpu":
                total = (all_ - idle, all_)
            else:
                cores.append((all_ - idle, all_))
        elif line.startswith("procs_running"):
            try:
                running = int(line.split()[1])
            except (IndexError, ValueError):
                pass
    return total, cores, running


def _percent(now: tuple[int, int], before: tuple[int, int] | None) -> float | None:
    if before is None:
        return None
    busy, total = now[0] - before[0], now[1] - before[1]
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * busy / total))


def _meminfo(text: str | None) -> dict[str, int]:
    out = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            try:
                out[key] = int(parts[0]) * (1024 if len(parts) > 1 and parts[1] == "kB" else 1)
            except ValueError:
                pass
    return out


def _net_bytes(text: str | None) -> tuple[int, int] | None:
    if text is None:
        return None
    rx = tx = 0
    found = False
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if not sep or name.strip() in ("lo", "") or "|" in name:
            continue
        cols = rest.split()
        if len(cols) >= 9:
            try:
                rx += int(cols[0])
                tx += int(cols[8])
                found = True
            except ValueError:
                pass
    return (rx, tx) if found else None


def _proc_ticks(text: str) -> tuple[str, int] | None:
    """(command, utime + stime) from a /proc/<pid>/stat line."""
    lo, hi = text.find("("), text.rfind(")")
    if lo < 0 or hi < lo:
        return None
    fields = text[hi + 2 :].split()
    try:
        return text[lo + 1 : hi], int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


@dataclass
class SysMon:
    """Samples the system at most once per ``interval`` seconds (see module doc).

    ``proc`` is the procfs root (tests point it at a fake tree), ``clock`` a
    monotonic seconds clock.
    """

    interval: float = 0.5
    top_interval: float = 2.0
    top_n: int = 5
    history_len: int = 60
    proc: Path | str = "/proc"
    clock: Callable[[], float] = time.monotonic
    _last: Stats | None = field(default=None, init=False, repr=False)
    _cpu: tuple[tuple[int, int] | None, list[tuple[int, int]]] = field(
        default=(None, []), init=False, repr=False
    )
    _net: tuple[float, tuple[int, int]] | None = field(default=None, init=False, repr=False)
    _pids: tuple[float, dict[str, int]] | None = field(default=None, init=False, repr=False)
    _top: tuple[float, tuple[tuple[str, float], ...], int | None] | None = field(
        default=None, init=False, repr=False
    )
    _history: deque[float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.proc = Path(self.proc)
        self._history = deque(maxlen=max(1, self.history_len))

    def stats(self) -> Stats:
        """The latest sample, re-sampled if it is ``interval`` seconds old."""
        last = self._last
        if last is not None and self.clock() - last.time < self.interval:
            return last
        return self.sample()

    def sample(self) -> Stats:
        """Take a sample now."""
        now = self.clock()
        proc = Path(self.proc)

        total, cores, running = _cpu_times(_read(proc / "stat"))
        prev_total, prev_cores = self._cpu
        cpu = _percent(total, prev_total) if total is not None else None
        core_pcts = tuple(
            p for p in (_percent(c, prev_cores[i] if i < len(prev_cores) else None)
                        for i, c in enumerate(cores)) if p is not None
        )  # fmt: skip
        self._cpu = (total, cores)
        if cpu is not None:
            self._history.append(cpu)

        mem = _meminfo(_read(proc / "meminfo"))
        mem_total = mem.get("MemTotal")
        avail = mem.get("MemAvailable", mem.get("MemFree"))
        mem_used = None if mem_total is None or avail is None else max(0, mem_total - avail)
        if mem_total is None:
            mem_total = _phys_mem()
        swap_total = mem.get("SwapTotal")
        swap_free = mem.get("SwapFree")
        swap_used = None if swap_total is None or swap_free is None else max(0, swap_total - swap_free)

        load = _loadavg(proc)
        uptime = _uptime(proc)
        tasks = _loadavg_tasks(proc)

        rx = tx = None
        net = _net_bytes(_read(proc / "net" / "dev"))
        if net is not None:
            if self._net is not None and now > self._net[0]:
                dt = now - self._net[0]
                rx = max(0.0, (net[0] - self._net[1][0]) / dt)
                tx = max(0.0, (net[1] - self._net[1][1]) / dt)
            self._net = (now, net)

        top, procs = self._top_procs(proc, now)
        stats = Stats(
            time=now, cpu=cpu, cores=core_pcts, ncpu=len(cores) or os.cpu_count() or 1,
            mem_total=mem_total, mem_used=mem_used, swap_total=swap_total, swap_used=swap_used,
            load=load, uptime=uptime, tasks=procs if procs is not None else tasks,
            running=running, net_rx=rx, net_tx=tx, top=top, history=tuple(self._history),
        )  # fmt: skip
        self._last = stats
        return stats

    def _top_procs(self, proc: Path, now: float) -> tuple[tuple[tuple[str, float], ...], int | None]:
        """Busiest processes by CPU since the previous scan, and the process count."""
        cached = self._top
        if cached is not None and now - cached[0] < self.top_interval:
            return cached[1], cached[2]
        try:
            names = [n for n in os.listdir(proc) if n.isdigit()][:_MAX_PIDS]
        except OSError:
            self._top = (now, (), None)
            return (), None
        ticks: dict[str, int] = {}
        comm: dict[str, str] = {}
        for pid in names:
            text = _read(proc / pid / "stat")
            parsed = _proc_ticks(text) if text else None
            if parsed is not None:
                comm[pid], ticks[pid] = parsed
        top: tuple[tuple[str, float], ...] = ()
        prev = self._pids
        if prev is not None and now > prev[0]:
            hz = _clock_ticks()
            dt = now - prev[0]
            busy = [
                (comm[pid], 100.0 * (n - prev[1][pid]) / (hz * dt))
                for pid, n in ticks.items() if pid in prev[1] and n > prev[1][pid]
            ]  # fmt: skip
            busy.sort(key=lambda p: -p[1])
            top = tuple(busy[: self.top_n])
        self._pids = (now, ticks)
        self._top = (now, top, len(names) or None)
        return top, len(names) or None


def _clock_ticks() -> int:
    try:
        return max(1, os.sysconf("SC_CLK_TCK"))
    except (AttributeError, OSError, ValueError):
        return 100


def _phys_mem() -> int | None:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None


def _loadavg(proc: Path) -> tuple[float, float, float] | None:
    parts = (_read(proc / "loadavg") or "").split()
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (IndexError, ValueError):
        pass
    try:
        return os.getloadavg()  # macOS and other systems without /proc
    except (AttributeError, OSError):
        return None


def _loadavg_tasks(proc: Path) -> int | None:
    parts = (_read(proc / "loadavg") or "").split()
    try:
        return int(parts[3].split("/")[1])
    except (IndexError, ValueError):
        return None


def _uptime(proc: Path) -> float | None:
    parts = (_read(proc / "uptime") or "").split()
    try:
        return float(parts[0])
    except (IndexError, ValueError):
        pass
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is None:
        return None
    try:
        return time.clock_gettime(clock)
    except OSError:
        return None


# -- formatting -------------------------------------------------------------------


def fmt_bytes(n: float | None) -> str:
    """1536 -> "1.5K", 3.2e9 -> "3.0G" (binary units); None -> "n/a"."""
    if n is None:
        return "n/a"
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    raise AssertionError("unreachable")


def fmt_rate(n: float | None) -> str:
    return "n/a" if n is None else f"{fmt_bytes(n)}/s"


def fmt_duration(seconds: float | None) -> str:
    """Uptime style: "42s", "14m", "3h05m", "2d 04h"; None -> "n/a"."""
    if seconds is None:
        return "n/a"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d {h:02d}h"
