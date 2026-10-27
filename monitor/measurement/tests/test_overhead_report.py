"""Procfs and combined overhead report tests."""

from __future__ import annotations

import json
from pathlib import Path

from lib.overhead_report import build_overhead_report
from lib.procfs import (
    delta_counters,
    delta_net,
    parse_net_dev,
    parse_proc_io,
    parse_proc_status,
    parse_smaps_rollup,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_status_and_uss_from_smaps() -> None:
    status = parse_proc_status((FIXTURES / "proc_status.txt").read_text())
    assert status["VmRSS"] == 18432
    assert status["VmHWM"] == 19000
    smaps = parse_smaps_rollup((FIXTURES / "smaps_rollup.txt").read_text())
    assert smaps["uss_kib"] == 12000


def test_io_and_net_deltas() -> None:
    before = parse_proc_io((FIXTURES / "proc_io_before.txt").read_text())
    after = parse_proc_io((FIXTURES / "proc_io_after.txt").read_text())
    delta = delta_counters(before, after)
    assert delta["read_bytes"] == 4096
    assert delta["write_bytes"] == 8192
    assert delta["wchar"] == 10000
    net = delta_net(
        parse_net_dev((FIXTURES / "net_dev_before.txt").read_text()),
        parse_net_dev((FIXTURES / "net_dev_after.txt").read_text()),
    )
    assert net["total_rx_bytes"] == 3000
    assert net["total_tx_bytes"] == 6000
    assert "lo" not in net["per_iface"]
    assert "upper bound" in net["caveat"]


def test_overhead_report_from_capture_dir(tmp_path: Path) -> None:
    (tmp_path / "pidstat.txt").write_text(
        (FIXTURES / "pidstat_urd.txt").read_text(), encoding="utf-8"
    )
    (tmp_path / "proc_status_before.txt").write_text(
        (FIXTURES / "proc_status.txt").read_text(), encoding="utf-8"
    )
    (tmp_path / "proc_status_after.txt").write_text(
        (FIXTURES / "proc_status.txt").read_text(), encoding="utf-8"
    )
    (tmp_path / "smaps_rollup_after.txt").write_text(
        (FIXTURES / "smaps_rollup.txt").read_text(), encoding="utf-8"
    )
    (tmp_path / "proc_io_before.txt").write_text(
        (FIXTURES / "proc_io_before.txt").read_text(), encoding="utf-8"
    )
    (tmp_path / "proc_io_after.txt").write_text(
        (FIXTURES / "proc_io_after.txt").read_text(), encoding="utf-8"
    )
    (tmp_path / "net_dev_before.txt").write_text(
        (FIXTURES / "net_dev_before.txt").read_text(), encoding="utf-8"
    )
    (tmp_path / "net_dev_after.txt").write_text(
        (FIXTURES / "net_dev_after.txt").read_text(), encoding="utf-8"
    )
    (tmp_path / "meta.json").write_text(
        json.dumps({"host": "node.example", "pid": 4242}), encoding="utf-8"
    )
    report = build_overhead_report(tmp_path)
    assert report["pidstat"]["peak_cpu_pct"] == 2.6
    assert report["proc"]["rss_peak_kib"] == 19000
    assert report["proc"]["uss_peak_kib"] == 12000
    assert report["proc"]["io_delta"]["write_bytes"] == 8192
    assert report["proc"]["net_delta"]["total_tx_bytes"] == 6000
