"""Combine pidstat + /proc snapshots into one overhead JSON object."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .pidstat import parse_pidstat_text, summary_to_dict
from .procfs import (
    delta_counters,
    delta_net,
    parse_net_dev,
    parse_proc_io,
    parse_proc_status,
    parse_smaps_rollup,
)


def _read(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def build_overhead_report(capture_dir: Path) -> dict[str, Any]:
    """Load capture_dir files written by capture_daemon_overhead.sh."""
    report: dict[str, Any] = {
        "capture_dir": str(capture_dir),
        "pidstat": None,
        "proc": {},
        "meta": {},
    }
    meta_path = capture_dir / "meta.json"
    if meta_path.is_file():
        import json

        report["meta"] = json.loads(meta_path.read_text(encoding="utf-8"))

    pidstat_text = _read(capture_dir / "pidstat.txt")
    if pidstat_text:
        report["pidstat"] = summary_to_dict(parse_pidstat_text(pidstat_text))

    before_status = _read(capture_dir / "proc_status_before.txt")
    after_status = _read(capture_dir / "proc_status_after.txt")
    before_smaps = _read(capture_dir / "smaps_rollup_before.txt")
    after_smaps = _read(capture_dir / "smaps_rollup_after.txt")
    before_io = _read(capture_dir / "proc_io_before.txt")
    after_io = _read(capture_dir / "proc_io_after.txt")
    before_net = _read(capture_dir / "net_dev_before.txt")
    after_net = _read(capture_dir / "net_dev_after.txt")

    rss_values: list[int] = []
    uss_values: list[int] = []
    for blob in (before_status, after_status):
        if blob:
            parsed = parse_proc_status(blob)
            if "VmRSS" in parsed:
                rss_values.append(parsed["VmRSS"])
            if "VmHWM" in parsed:
                rss_values.append(parsed["VmHWM"])
    for blob in (before_smaps, after_smaps):
        if blob:
            parsed = parse_smaps_rollup(blob)
            if "uss_kib" in parsed:
                uss_values.append(parsed["uss_kib"])
            if "Pss" in parsed:
                report["proc"].setdefault("pss_kib_samples", []).append(parsed["Pss"])

    proc: dict[str, Any] = report["proc"]
    if rss_values:
        proc["rss_peak_kib"] = max(rss_values)
    if uss_values:
        proc["uss_peak_kib"] = max(uss_values)
    if before_io and after_io:
        proc["io_delta"] = delta_counters(parse_proc_io(before_io), parse_proc_io(after_io))
        proc["io_delta_caveat"] = (
            "read_bytes/write_bytes are block-layer storage. rchar/wchar include "
            "userspace reads/writes (sockets too) and are not a clean disk figure."
        )
    if before_net and after_net:
        proc["net_delta"] = delta_net(parse_net_dev(before_net), parse_net_dev(after_net))
    ss_text = _read(capture_dir / "ss_amqp.txt")
    if ss_text:
        proc["ss_amqp"] = ss_text.strip().splitlines()
    return report
