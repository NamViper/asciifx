"""SysMon against a fake /proc tree, its fallbacks, caching, and the formatters."""

import os

import pytest

from asciifx import sysmon
from asciifx.sysmon import Stats, SysMon, fmt_bytes, fmt_duration, fmt_rate


class Clock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def write_proc(root, *, cpu, cores, net=(1000, 500), pids=None, running=2):
    """A minimal /proc: cpu = (busy, idle) jiffies; cores likewise."""
    (root / "net").mkdir(parents=True, exist_ok=True)
    lines = [f"cpu  {cpu[0]} 0 0 {cpu[1]} 0 0 0 0 0 0"]
    lines += [f"cpu{i} {b} 0 0 {i_} 0 0 0 0 0 0" for i, (b, i_) in enumerate(cores)]
    lines += ["intr 1 2 3", f"procs_running {running}", "procs_blocked 0"]
    (root / "stat").write_text("\n".join(lines) + "\n")
    (root / "meminfo").write_text(
        "MemTotal:       8000000 kB\nMemFree:        1000000 kB\nMemAvailable:   6000000 kB\n"
        "SwapTotal:      2000000 kB\nSwapFree:       1500000 kB\n"
    )
    (root / "loadavg").write_text("0.52 0.61 0.70 3/312 4242\n")
    (root / "uptime").write_text("277545.12 1000.0\n")
    (root / "net" / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs\n"
        f"    lo: 999999 1 0 0 0 0 0 0 999999 1 0 0 0 0 0 0\n"
        f"  eth0: {net[0]} 10 0 0 0 0 0 0 {net[1]} 10 0 0 0 0 0 0\n"
    )
    for pid, (comm, ticks) in (pids or {}).items():
        (root / str(pid)).mkdir(exist_ok=True)
        (root / str(pid) / "stat").write_text(
            f"{pid} ({comm}) S 1 1 1 0 -1 4194304 0 0 0 0 {ticks} 0 0 0 20 0 1 0 100 0 0\n"
        )


def test_two_samples_give_cpu_net_and_top(tmp_path, monkeypatch):
    monkeypatch.setattr(sysmon, "_clock_ticks", lambda: 100)
    clock = Clock()
    write_proc(tmp_path, cpu=(1000, 9000), cores=[(500, 4500), (500, 4500)],
               pids={1: ("init", 10), 42: ("tmux: server (x)", 200), 77: ("python3", 500)})
    mon = SysMon(proc=tmp_path, clock=clock)
    first = mon.stats()
    assert first.cpu is None and first.cores == () and first.net_rx is None and first.top == ()
    assert first.ncpu == 2 and first.mem_total == 8000000 * 1024
    assert first.mem_used == 2000000 * 1024 and first.mem_percent == pytest.approx(25.0)
    assert first.swap_used == 500000 * 1024 and first.swap_percent == pytest.approx(25.0)
    assert first.load == (0.52, 0.61, 0.70) and first.uptime == pytest.approx(277545.12)
    assert first.tasks == 3 and first.running == 2  # process count from the pid scan

    clock.t += 2.0
    write_proc(tmp_path, cpu=(1300, 9700), cores=[(700, 4800), (600, 4900)], net=(5000, 900),
               pids={1: ("init", 10), 42: ("tmux: server (x)", 250), 77: ("python3", 650)})
    second = mon.stats()
    assert second.cpu == pytest.approx(30.0)  # 300 busy of 1000
    assert second.cores == pytest.approx((40.0, 20.0))
    assert second.net_rx == pytest.approx(2000.0) and second.net_tx == pytest.approx(200.0)
    assert [name for name, _ in second.top] == ["python3", "tmux: server (x)"]
    assert [pct for _, pct in second.top] == pytest.approx([75.0, 25.0])
    assert second.history == pytest.approx((30.0,))


def test_cached_between_samples(tmp_path):
    clock = Clock()
    write_proc(tmp_path, cpu=(1, 1), cores=[(1, 1)])
    mon = SysMon(proc=tmp_path, clock=clock, interval=0.5)
    a = mon.stats()
    (tmp_path / "meminfo").unlink()
    clock.t += 0.4
    assert mon.stats() is a  # no re-read within the interval
    clock.t += 0.2
    b = mon.stats()
    assert b is not a and b.mem_used is None


def test_top_refreshes_more_rarely(tmp_path, monkeypatch):
    monkeypatch.setattr(sysmon, "_clock_ticks", lambda: 100)
    clock = Clock()
    write_proc(tmp_path, cpu=(1, 1), cores=[(1, 1)], pids={5: ("a", 0)})
    mon = SysMon(proc=tmp_path, clock=clock, top_interval=2.0)
    mon.stats()
    clock.t += 1.0
    write_proc(tmp_path, cpu=(2, 2), cores=[(2, 2)], pids={5: ("a", 100)})
    assert mon.stats().top == ()  # not rescanned yet
    clock.t += 1.0
    (name, pct), = mon.stats().top
    assert name == "a" and pct == pytest.approx(50.0)  # 100 ticks over 2 s


def test_history_is_bounded(tmp_path):
    clock = Clock()
    mon = SysMon(proc=tmp_path, clock=clock, history_len=5)
    for k in range(12):
        write_proc(tmp_path, cpu=(10 * k, 10 * k + 100), cores=[(1, 1)])
        mon.stats()
        clock.t += 1.0
    assert len(mon.stats().history) == 5


def test_no_proc_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getloadavg", lambda: (1.0, 2.0, 3.0))
    mon = SysMon(proc=tmp_path / "missing", clock=Clock())
    s = mon.stats()
    assert s.cpu is None and s.cores == () and s.mem_used is None and s.mem_percent is None
    assert s.load == (1.0, 2.0, 3.0)
    assert s.ncpu == (os.cpu_count() or 1)
    assert s.tasks is None and s.running is None and s.net_rx is None and s.top == ()

    def no_load():
        raise OSError("no load average")

    monkeypatch.setattr(os, "getloadavg", no_load)
    monkeypatch.setattr(sysmon, "_phys_mem", lambda: None)
    s = SysMon(proc=tmp_path / "missing", clock=Clock()).stats()
    assert s.load is None and s.mem_total is None


def test_garbage_files_dont_crash(tmp_path):
    (tmp_path / "net").mkdir()
    for name in ("stat", "meminfo", "loadavg", "uptime", "net/dev"):
        (tmp_path / name).write_text("garbage ( ) x\ncpu a b c\n")
    (tmp_path / "12").mkdir()
    (tmp_path / "12" / "stat").write_text("12 (x")
    mon = SysMon(proc=tmp_path, clock=Clock())
    s = mon.stats()
    assert s.cpu is None and s.cores == () and s.mem_used is None and s.swap_used is None
    assert s.net_rx is None and s.top == () and s.running is None
    assert mon.sample().cpu is None and mon.sample().top == ()


def test_real_system_smoke():
    s = SysMon().sample()
    assert isinstance(s, Stats) and s.ncpu >= 1


def test_formatters():
    assert fmt_bytes(None) == "n/a" and fmt_bytes(512) == "512B"
    assert fmt_bytes(1536) == "1.5K" and fmt_bytes(3 * 1024**3) == "3.0G"
    assert fmt_bytes(5 * 1024**5) == "5120.0T"
    assert fmt_rate(2048) == "2.0K/s" and fmt_rate(None) == "n/a"
    assert [fmt_duration(s) for s in (None, 0, 42, 840, 3 * 3600 + 5 * 60, 2 * 86400 + 4 * 3600)] == [
        "n/a", "0s", "42s", "14m", "3h05m", "2d 04h",
    ]
