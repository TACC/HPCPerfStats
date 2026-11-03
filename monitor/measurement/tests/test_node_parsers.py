"""Node-wide sampler, ss tcp_info, perf stat CSV, RAPL/hwmon energy parsers."""

from __future__ import annotations

from pathlib import Path

from lib import nodesample, perfstat, rapl, tcpinfo

FIX = Path(__file__).resolve().parents[1] / "fixtures"

PSI_TEXT = (
    "some avg10=1.50 avg60=0.80 avg300=0.10 total=123456\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=789\n"
)


def test_nodesample_stat_psi_interrupts():
    stat = nodesample.parse_proc_stat((FIX / "proc_stat_node.txt").read_text())
    assert set(("user", "system", "irq", "softirq", "idle", "iowait")) <= set(stat["cpu"])
    assert stat["ctxt"] > 0 and stat["intr_total"] > 0
    assert stat["cpu_count"] == 28
    psi = nodesample.parse_psi(PSI_TEXT)
    assert psi["some"]["avg10"] == 1.5 and psi["some"]["total"] == 123456
    assert psi["full"]["total"] == 789
    irqs = nodesample.parse_interrupts((FIX / "proc_interrupts.txt").read_text())
    assert irqs["0"] == 40 and irqs["1"] == 9
    soft = nodesample.parse_interrupts((FIX / "proc_softirqs.txt").read_text())
    assert "TIMER" in soft and soft["TIMER"] > 0
    mem = nodesample.parse_meminfo((FIX / "proc_meminfo.txt").read_text())
    assert mem["MemTotal"] > mem["MemAvailable"] > 0


def test_nodesample_psi_absent_is_none(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    for name in ("stat", "interrupts", "softirqs", "meminfo", "loadavg"):
        src = {"stat": "proc_stat_node.txt", "interrupts": "proc_interrupts.txt",
               "softirqs": "proc_softirqs.txt", "meminfo": "proc_meminfo.txt"}.get(name)
        (root / name).write_text((FIX / src).read_text() if src else "0.10 0.20 0.30 1/400 9\n")
    sampler = nodesample.NodeSampler(proc_root=root, sys_root=tmp_path / "sys")
    s = sampler.sample()
    assert s["psi"] is None
    assert s["loadavg1"] == 0.1
    assert s["cpu"]["idle"] > 0
    (root / "pressure").mkdir()
    for res in ("cpu", "memory", "io"):
        (root / "pressure" / res).write_text(PSI_TEXT)
    s = nodesample.NodeSampler(proc_root=root, sys_root=tmp_path / "sys").sample()
    assert s["psi"]["cpu"]["some"]["avg10"] == 1.5


def test_parse_online_cpus():
    assert nodesample.parse_cpu_list("0-3,8,10-11\n") == [0, 1, 2, 3, 8, 10, 11]
    assert nodesample.parse_cpu_list("") == []


def test_tcpinfo_daemon_socket_bytes():
    text = (FIX / "ss_tinpH_amqp.txt").read_text()
    got = tcpinfo.socket_counters(text, pid=4242, port=5672)
    assert got == {"bytes_acked": 3405, "bytes_received": 9336, "segs_out": 89, "segs_in": 91,
                   "bytes_sent": 3404, "sockets": 1}
    assert tcpinfo.socket_counters(text, pid=1, port=5672) is None
    assert tcpinfo.socket_counters(text, pid=4242, port=5671) is None
    assert tcpinfo.socket_counters("", pid=4242, port=5672) is None


def test_perfstat_csv_unsupported_events():
    events = perfstat.parse_perf_stat_csv((FIX / "perf_stat_x.csv").read_text())
    assert events["instructions"]["value"] == 6018518517
    assert events["task-clock"]["value"] == 1834.21 and events["task-clock"]["unit"] == "msec"
    assert events["cache-references"] == {"status": "NOT_MEASURED", "reason": "<not supported>"}
    assert events["cache-misses"]["status"] == "NOT_MEASURED"
    derived = perfstat.derived_metrics(events)
    assert abs(derived["ipc"]["value"] - 6018518517 / 4012345678) < 1e-12
    assert derived["cache_miss_ratio"]["status"] == "NOT_MEASURED"
    no_cycles = dict(events)
    no_cycles.pop("cycles")
    assert perfstat.derived_metrics(no_cycles)["ipc"]["status"] == "NOT_MEASURED"


def _zone(root: Path, name: str, label: str, energy: int, max_range: int) -> None:
    zdir = root / name
    zdir.mkdir(parents=True, exist_ok=True)
    (zdir / "name").write_text(label + "\n")
    (zdir / "energy_uj").write_text(f"{energy}\n")
    (zdir / "max_energy_range_uj").write_text(f"{max_range}\n")


def test_rapl_wraparound(tmp_path):
    root = tmp_path / "powercap"
    _zone(root, "intel-rapl:0", "package-0", 999_000_000, 1_000_000_000)
    _zone(root, "intel-rapl:0:0", "dram", 5, 1_000_000_000)
    before = rapl.read_energy(powercap_root=root, hwmon_root=tmp_path / "none")
    _zone(root, "intel-rapl:0", "package-0", 1_000_000, 1_000_000_000)
    _zone(root, "intel-rapl:0:0", "dram", 2_000_005, 1_000_000_000)
    after = rapl.read_energy(powercap_root=root, hwmon_root=tmp_path / "none")
    delta = rapl.energy_delta_j(before, after)
    assert delta["intel-rapl:0/package-0"] == 2.0
    assert delta["intel-rapl:0:0/dram"] == 2.0
    assert before["source"] == "powercap"


def test_rapl_absent_and_hwmon_fallback(tmp_path):
    empty = rapl.read_energy(powercap_root=tmp_path / "no", hwmon_root=tmp_path / "no2")
    assert empty["source"] is None and empty["zones"] == {}
    hw = tmp_path / "hwmon" / "hwmon3"
    hw.mkdir(parents=True)
    (hw / "name").write_text("grace_power\n")
    (hw / "energy1_input").write_text("5000000\n")
    (hw / "energy1_label").write_text("Module Power Socket 0\n")
    got = rapl.read_energy(powercap_root=tmp_path / "no", hwmon_root=tmp_path / "hwmon")
    assert got["source"] == "hwmon"
    assert got["zones"]["hwmon3/Module Power Socket 0"]["energy_uj"] == 5_000_000
