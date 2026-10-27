"""Cadence conf keys are not the same quantity as pidstat busy spacing."""

from __future__ import annotations

from pathlib import Path

from lib.cadence import parse_monitor_conf
from lib.pidstat import parse_pidstat_text

ROOT = Path(__file__).resolve().parents[1]


def test_conf_and_stampede3_pidstat_are_not_the_same_quantity() -> None:
    conf = parse_monitor_conf(
        (ROOT / "fixtures" / "hpcperfstats.conf").read_text(encoding="utf-8")
    )
    pid = parse_pidstat_text(
        (ROOT / "fixtures" / "pidstat_cpu_stampede3.txt").read_text(encoding="utf-8")
    )
    assert conf["sample_freq"] == 30.0
    assert pid.median_busy_spacing_seconds == 150
    assert conf["sample_freq"] != float(pid.median_busy_spacing_seconds)
