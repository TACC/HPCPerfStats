"""metric-profiler journal lines from the --profile-collectors (instrumented) leg."""

from __future__ import annotations

from pathlib import Path

from lib import driver_map, profiler_lines

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_last_cumulative_report_wins():
    rep = profiler_lines.parse_last_report((FIX / "journal_metric_profiler.txt").read_text())
    assert rep["cycles"] == 134
    cpu = rep["types"]["host_cpu"]
    assert cpu["calls"] == 134
    assert abs(cpu["cpu_ms_per_call"] - 0.8) < 1e-9
    assert abs(cpu["wall_ms_per_call"] - 1.0) < 1e-9
    assert abs(cpu["wait_ms_per_call"] - 0.2) < 1e-9
    assert list(rep["types"]) == ["host_cpu", "host_mem"]  # sorted by CPU, descending
    assert rep["metrics_top"] == []  # the last report carried no metric= lines


def test_undercounted_cycle_header_uses_type_calls():
    """c122-018 2026-11-09: shared-node daemon printed cycles=1 after 47 collects."""
    text = ("x hpcperfstatsd-profiler[1]: metric-profiler: cycles=1 tracked_metrics=1 "
            "tracked_types=2 overflow=0\n"
            "x hpcperfstatsd-profiler[1]: metric-profiler:type=host_cpu calls=47 "
            "wall_ns=94000000 cpu_ns=47000000 wait_ns=47000000 avg_wall_ns=2000000\n"
            "x hpcperfstatsd-profiler[1]: metric-profiler:type=host_mem calls=47 "
            "wall_ns=4700000 cpu_ns=4700000 wait_ns=0 avg_wall_ns=100000\n")
    rep = profiler_lines.parse_last_report(text)
    assert rep["cycles"] == 47 and rep["cycles_reported"] == 1
    caps = {"arch": "x86_64", "configure": {"cpu_backend": "likwid", "infiniband": False}}
    fs = profiler_lines.group_rollup(rep, caps)["filesystem"]
    assert abs(fs["cpu_ms_per_cycle"] - (47.0 + 4.7) / 47) < 1e-9  # not 47x inflated


def test_no_report_and_group_rollup():
    assert profiler_lines.parse_last_report("Enabled types: host_cpu\n") is None
    rep = profiler_lines.parse_last_report((FIX / "journal_metric_profiler.txt").read_text())
    caps = {"arch": "x86_64", "configure": {"cpu_backend": "likwid", "infiniband": False}}
    groups = profiler_lines.group_rollup(rep, caps)
    assert set(groups) == {"filesystem"}
    assert abs(groups["filesystem"]["cpu_ms_per_cycle"] - (107.2 + 67.0) / 134) < 1e-9
    assert driver_map.group_for_type("host_mem", caps) == "filesystem"
