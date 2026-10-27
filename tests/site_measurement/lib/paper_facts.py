"""Fill paper-facing measurement slots; never invent missing numbers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NOT_MEASURED = "NOT_MEASURED"

SLOTS = (
    "cadence.sample_freq",
    "cadence.sample_freq_slow",
    "cadence.send_freq",
    "cadence.enable_slow_tier",
    "cadence.host",
    "overhead.cpu_peak_pct",
    "overhead.cpu_average_pct",
    "overhead.rss_peak_kib",
    "overhead.uss_peak_kib",
    "overhead.io_read_bytes",
    "overhead.io_write_bytes",
    "overhead.net_tx_bytes",
    "overhead.net_rx_bytes",
    "overhead.gpu_collector_delta_cpu_pct",
    "overhead.fabric_collector_delta_cpu_pct",
    "jitter.cyclictest_delta_us",
    "apps.stream_triad_delta_pct",
    "apps.ior_delta_pct",
    "apps.osu_latency_delta_pct",
    "apps.real_app_delta_pct",
    "query.extended_search_1d_p50_ms",
    "query.extended_search_7d_p50_ms",
    "query.extended_search_30d_p50_ms",
    "query.extended_search_metric_p50_ms",
    "query.job_detail_p50_ms",
    "query.job_plots_p50_ms",
    "pipeline.ingest_rows_per_s",
    "pipeline.host_data_bytes",
    "pipeline.update_metrics_lag_s",
    "storage.backup_cadence",
    "storage.wal_pitr",
    "storage.replicas",
    "storage.rpo",
    "ticket.median_minutes_with_hps",
    "ticket.median_minutes_without_hps",
    "screenshots.job_list",
    "screenshots.job_detail",
)


def empty_facts() -> dict[str, Any]:
    """Every paper slot starts as NOT_MEASURED."""
    return {slot: NOT_MEASURED for slot in SLOTS}


def load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON object or return None when the file is absent."""
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        return payload
    return None


def merge_results_dir(results_dir: Path) -> dict[str, Any]:
    """Populate slots from capture JSON files under a results directory."""
    facts = empty_facts()
    cadence = load_json(results_dir / "cadence.json")
    if cadence:
        for key in (
            "sample_freq",
            "sample_freq_slow",
            "send_freq",
            "enable_slow_tier",
        ):
            if cadence.get(key) is not None:
                facts[f"cadence.{key}"] = cadence[key]
        if cadence.get("host"):
            facts["cadence.host"] = cadence["host"]

    overhead = load_json(results_dir / "overhead.json")
    if overhead:
        pidstat = overhead.get("pidstat") or {}
        proc = overhead.get("proc") or {}
        _set_if_present(facts, "overhead.cpu_peak_pct", pidstat.get("peak_cpu_pct"))
        _set_if_present(
            facts, "overhead.cpu_average_pct", pidstat.get("average_cpu_pct")
        )
        rss = proc.get("rss_peak_kib")
        if rss is None:
            rss = pidstat.get("peak_rss_kib")
        _set_if_present(facts, "overhead.rss_peak_kib", rss)
        _set_if_present(facts, "overhead.uss_peak_kib", proc.get("uss_peak_kib"))
        io_delta = proc.get("io_delta") or {}
        _set_if_present(facts, "overhead.io_read_bytes", io_delta.get("read_bytes"))
        _set_if_present(facts, "overhead.io_write_bytes", io_delta.get("write_bytes"))
        net = proc.get("net_delta") or {}
        _set_if_present(facts, "overhead.net_tx_bytes", net.get("total_tx_bytes"))
        _set_if_present(facts, "overhead.net_rx_bytes", net.get("total_rx_bytes"))

    isolation = load_json(results_dir / "collector_isolation.json")
    if isolation:
        _set_if_present(
            facts,
            "overhead.gpu_collector_delta_cpu_pct",
            isolation.get("gpu_delta_peak_cpu_pct"),
        )
        _set_if_present(
            facts,
            "overhead.fabric_collector_delta_cpu_pct",
            isolation.get("fabric_delta_peak_cpu_pct"),
        )

    jitter = load_json(results_dir / "jitter.json")
    if jitter:
        _set_if_present(
            facts, "jitter.cyclictest_delta_us", jitter.get("max_delta_us")
        )

    apps = load_json(results_dir / "apps_on_vs_off.json")
    if apps:
        _set_if_present(facts, "apps.stream_triad_delta_pct", apps.get("stream_delta_pct"))
        _set_if_present(facts, "apps.ior_delta_pct", apps.get("ior_delta_pct"))
        _set_if_present(facts, "apps.osu_latency_delta_pct", apps.get("osu_delta_pct"))
        _set_if_present(facts, "apps.real_app_delta_pct", apps.get("app_delta_pct"))

    query = load_json(results_dir / "query_latency.json")
    if query:
        by_name = {
            row["name"]: row
            for row in query.get("scenarios", [])
            if isinstance(row, dict) and "name" in row
        }
        _query_slot(facts, by_name, "extended_search_1d", "query.extended_search_1d_p50_ms")
        _query_slot(facts, by_name, "extended_search_7d", "query.extended_search_7d_p50_ms")
        _query_slot(
            facts, by_name, "extended_search_30d", "query.extended_search_30d_p50_ms"
        )
        _query_slot(
            facts,
            by_name,
            "extended_search_metric_threshold",
            "query.extended_search_metric_p50_ms",
        )
        _query_slot(facts, by_name, "job_detail", "query.job_detail_p50_ms")
        _query_slot(facts, by_name, "job_plots", "query.job_plots_p50_ms")

    pipeline = load_json(results_dir / "pipeline_scale.json")
    if pipeline:
        _set_if_present(
            facts, "pipeline.ingest_rows_per_s", pipeline.get("ingest_rows_per_s")
        )
        _set_if_present(
            facts, "pipeline.host_data_bytes", pipeline.get("host_data_bytes")
        )
        _set_if_present(
            facts,
            "pipeline.update_metrics_lag_s",
            pipeline.get("update_metrics_lag_s"),
        )

    storage = load_json(results_dir / "storage_ha.json")
    if storage:
        for key, slot in (
            ("backup_cadence", "storage.backup_cadence"),
            ("wal_pitr", "storage.wal_pitr"),
            ("replicas", "storage.replicas"),
            ("rpo", "storage.rpo"),
        ):
            _set_if_present(facts, slot, storage.get(key))

    tickets = load_json(results_dir / "ticket_time.json")
    if tickets:
        _set_if_present(
            facts, "ticket.median_minutes_with_hps", tickets.get("median_minutes_with")
        )
        _set_if_present(
            facts,
            "ticket.median_minutes_without_hps",
            tickets.get("median_minutes_without"),
        )

    for name, slot in (
        ("job_list_full.png", "screenshots.job_list"),
        ("job_detail_full.png", "screenshots.job_detail"),
    ):
        if (results_dir / name).is_file() or (results_dir / "screenshots" / name).is_file():
            facts[slot] = name

    measured = [k for k, v in facts.items() if v != NOT_MEASURED]
    facts["_measured_count"] = len(measured)
    facts["_not_measured_count"] = len(SLOTS) - len(measured)
    facts["_not_measured_slots"] = [
        k for k, v in facts.items() if v == NOT_MEASURED and not k.startswith("_")
    ]
    return facts


def render_markdown(facts: dict[str, Any]) -> str:
    """Render a table authors can paste into notes; blanks stay NOT_MEASURED."""
    lines = [
        "# Paper measurement slots",
        "",
        "Do not invent numbers for `NOT_MEASURED` rows.",
        "",
        "| Slot | Value |",
        "|---|---|",
    ]
    for slot in SLOTS:
        lines.append(f"| `{slot}` | {facts.get(slot, NOT_MEASURED)} |")
    lines.append("")
    lines.append(
        f"Measured {facts.get('_measured_count', 0)} / {len(SLOTS)}; "
        f"still open: {facts.get('_not_measured_count', len(SLOTS))}."
    )
    return "\n".join(lines) + "\n"


def _set_if_present(facts: dict[str, Any], slot: str, value: Any) -> None:
    if value is None or value == "" or value == NOT_MEASURED:
        return
    facts[slot] = value


def _query_slot(
    facts: dict[str, Any],
    by_name: dict[str, dict[str, Any]],
    scenario: str,
    slot: str,
) -> None:
    row = by_name.get(scenario)
    if not row or row.get("not_measured") or row.get("p50_ms") is None:
        return
    facts[slot] = row["p50_ms"]
