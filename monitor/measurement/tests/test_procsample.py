"""Process sampler parsers: schedstat CPU, jiffies fallback, threads, FDs."""

from __future__ import annotations

from pathlib import Path

from lib import procsample

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _fake_proc(tmp_path: Path, pid: int, *, run_ns: int, utime: int, stime: int,
               threads: dict[int, tuple[str, int]], schedstat: bool = True,
               fds: tuple[str, ...] = ("/dev/null", "anon_inode:[perf_event]",
                                       "anon_inode:[perf_event]", "socket:[1]")) -> Path:
    root = tmp_path / "proc"
    pdir = root / str(pid)
    (pdir / "task").mkdir(parents=True, exist_ok=True)
    (pdir / "fd").mkdir(exist_ok=True)
    stat = (FIX / "proc_pid_stat.txt").read_text().split()
    stat[0] = str(pid)
    stat[1] = "(hpcperfstatsd)"
    stat[13] = str(utime)
    stat[14] = str(stime)
    stat[19] = str(len(threads))
    (pdir / "stat").write_text(" ".join(stat) + "\n")
    (pdir / "comm").write_text("hpcperfstatsd\n")
    if schedstat:
        (pdir / "schedstat").write_text(f"{run_ns} 1000 7\n")
    (pdir / "status").write_text((FIX / "proc_status_ctxt.txt").read_text())
    (pdir / "io").write_text((FIX / "proc_io_before.txt").read_text())
    (pdir / "smaps_rollup").write_text((FIX / "smaps_rollup.txt").read_text())
    for tid, (comm, tns) in threads.items():
        tdir = pdir / "task" / str(tid)
        tdir.mkdir(exist_ok=True)
        (tdir / "comm").write_text(comm + "\n")
        tstat = list(stat)
        tstat[0], tstat[1] = str(tid), f"({comm})"
        tstat[13], tstat[14] = str(utime + tid), str(stime + tid)  # distinct per thread
        (tdir / "stat").write_text(" ".join(tstat) + "\n")
        (tdir / "status").write_text((FIX / "proc_status_ctxt.txt").read_text())
        if schedstat:
            (tdir / "schedstat").write_text(f"{tns} 0 1\n")
    for i, target in enumerate(fds):
        link = pdir / "fd" / str(i)
        if not link.is_symlink():
            link.symlink_to(target)
    return root


def test_parse_pid_stat_real_fixture_handles_comm_and_fields():
    parsed = procsample.parse_pid_stat((FIX / "proc_pid_stat.txt").read_text())
    assert parsed["comm"] == "bash"
    assert parsed["utime"] == 0 and parsed["stime"] == 5
    assert parsed["minflt"] == 446 and parsed["majflt"] == 0
    assert parsed["num_threads"] == 1


def test_parse_pid_stat_comm_with_spaces_and_parens():
    text = "12 (a b) c)) R 1 1 1 0 -1 0 10 0 2 0 7 8 0 0 20 0 3 0 99 0 0"
    parsed = procsample.parse_pid_stat(text)
    assert parsed["comm"] == "a b) c)"
    assert parsed["utime"] == 7 and parsed["stime"] == 8 and parsed["num_threads"] == 3


def test_procsample_cpu_schedstat_exact(tmp_path):
    threads0 = {100: ("hpcperfstatsd", 1_000_000), 101: ("ev-send", 500_000)}
    root = _fake_proc(tmp_path, 100, run_ns=1_500_000, utime=10, stime=5, threads=threads0)
    s0 = procsample.sample_process(100, proc_root=root)
    threads1 = {100: ("hpcperfstatsd", 1_000_000 + 2_500_000_123), 101: ("ev-send", 500_000 + 10)}
    root = _fake_proc(tmp_path, 100, run_ns=1_500_000 + 2_500_000_133, utime=210, stime=55,
                      threads=threads1)
    s1 = procsample.sample_process(100, proc_root=root)
    delta = procsample.cpu_delta(s0, s1)
    assert delta["cpu_resolution"] == "schedstat_ns"
    assert delta["cpu_s"] == 2.500000133
    assert delta["user_s"] == 2.0 and delta["system_s"] == 0.5
    split = procsample.thread_split(s0, s1)
    assert abs(sum(split.values()) - 1.0) < 1e-12
    assert split["hpcperfstatsd"] > 0.99


def test_procsample_falls_back_to_jiffies_without_schedstat(tmp_path):
    root = _fake_proc(tmp_path, 100, run_ns=0, utime=10, stime=5,
                      threads={100: ("hpcperfstatsd", 0)}, schedstat=False)
    s0 = procsample.sample_process(100, proc_root=root)
    root = _fake_proc(tmp_path, 100, run_ns=0, utime=20, stime=15,
                      threads={100: ("hpcperfstatsd", 0)}, schedstat=False)
    s1 = procsample.sample_process(100, proc_root=root)
    delta = procsample.cpu_delta(s0, s1)
    assert delta["cpu_resolution"] == "jiffies"
    assert delta["cpu_s"] == 0.2


def test_procsample_perf_event_fd_count(tmp_path):
    root = _fake_proc(tmp_path, 100, run_ns=1, utime=1, stime=1, threads={100: ("x", 1)})
    s = procsample.sample_process(100, proc_root=root)
    assert s["fds"] == 4
    assert s["perf_event_fds"] == 2
    assert s["vol_ctxt"] == 11 and s["invol_ctxt"] == 3
    assert s["uss_kib"] > 0 and s["rss_kib"] > 0


def test_procsample_missing_fd_dir_is_not_measured(tmp_path):
    root = _fake_proc(tmp_path, 100, run_ns=1, utime=1, stime=1, threads={100: ("x", 1)})
    for link in (root / "100" / "fd").iterdir():
        link.unlink()
    (root / "100" / "fd").rmdir()
    s = procsample.sample_process(100, proc_root=root)
    assert s["fds"] is None and s["perf_event_fds"] is None


def test_procsample_gone_process_returns_none(tmp_path):
    assert procsample.sample_process(999999, proc_root=tmp_path) is None


def test_find_pid_by_comm(tmp_path):
    root = _fake_proc(tmp_path, 4242, run_ns=1, utime=1, stime=1, threads={4242: ("x", 1)})
    other = root / "77"
    other.mkdir()
    (other / "comm").write_text("bash\n")
    assert procsample.find_pid_by_comm("hpcperfstatsd", proc_root=root) == 4242
    assert procsample.find_pid_by_comm("nothing", proc_root=root) is None


def test_procsample_threads_keep_user_system_and_ctxt(tmp_path):
    """Approach B: each thread's own stat (utime/stime) and status (vol/invol ctxt) are kept
    beside run_ns, so the leg summary can show per-thread user/system and wakeups/s."""
    root = _fake_proc(tmp_path, 100, run_ns=5, utime=10, stime=5,
                      threads={100: ("hpcperfstatsd", 3), 101: ("dcgm worker", 2)})
    s = procsample.sample_process(100, proc_root=root)
    t = s["threads"]
    assert t[101]["comm"] == "dcgm worker" and t[101]["run_ns"] == 2
    assert t[101]["utime"] == 111 and t[101]["stime"] == 106
    assert t[100]["vol_ctxt"] == 11 and t[100]["invol_ctxt"] == 3
    # A tid whose stat vanished (exited between listdir and read) is skipped, not half-filled.
    (root / "100" / "task" / "101" / "stat").unlink()
    assert list(procsample.sample_process(100, proc_root=root)["threads"]) == [100]
