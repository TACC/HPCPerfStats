"""Compare emitted metrics against live /proc,/sys reads on the data host."""
from __future__ import annotations

import time

from .host_live_probes import (
    probe_cpu_devices,
    probe_host_fqdn,
    probe_loadavg_scaled,
    probe_net_stat,
    probe_numa_mem_kb,
)
from .message_parse import schema_key_name
from .payload_parse import rows_by_type, sample_header_timestamp


def _row_val(row_values: list[str], key_names: list[str], key: str) -> int | None:
    try:
        idx = key_names.index(key)
    except ValueError:
        return None
    if idx >= len(row_values):
        return None
    try:
        return int(row_values[idx])
    except ValueError:
        return None


def run_live_spot_checks(
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    full_body: str,
    max_age_sec: float,
    strict: bool,
) -> tuple[list[str], list[str], list[str]]:
    notes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    def warn(msg: str) -> None:
        (errors if strict else warnings).append(f"WARN live_spot {msg}")

    ts = sample_header_timestamp(full_body)
    sample_fresh = ts is not None and (time.time() - ts) <= max_age_sec

    want_host = manifest.get("host_fqdn") or ""
    live_host = probe_host_fqdn()
    if want_host and live_host and want_host != live_host:
        warn(f"live FQDN {live_host!r} != manifest {want_host!r}")

    enable_slow = manifest.get("enable_slow_tier", True)
    full_rows = rows_by_type(
        full_body,
        schema_by_type,
        require_tier=enable_slow,
        allowed_tier="@full" if enable_slow else None,
    )

    live_cpus = probe_cpu_devices()
    if "host_cpu" in full_rows and live_cpus:
        if len(full_rows["host_cpu"]) != len(live_cpus):
            warn(f"host_cpu rows {len(full_rows['host_cpu'])} != live cpus {len(live_cpus)}")

    load_live = probe_loadavg_scaled()
    if "host_ps" in full_rows and load_live:
        info = manifest.get("types", {}).get("host_ps", {})
        key_names = info.get("schema_key_names") or []
        for row in full_rows["host_ps"]:
            for key in ("load_1", "load_5", "load_15"):
                emitted = _row_val(row.values, key_names, key)
                live = load_live.get(key)
                if emitted is None or live is None:
                    continue
                if abs(emitted - live) > 500:
                    warn(f"host_ps {key}: emitted={emitted} live={live} (tolerance 500)")

    if "host_mem" in full_rows:
        for row in full_rows["host_mem"]:
            node = row.dev if row.dev.isdigit() else None
            if node is None:
                continue
            live_mem = probe_numa_mem_kb(node)
            info = manifest.get("types", {}).get("host_mem", {})
            key_names = info.get("schema_key_names") or []
            for key in ("MemTotal", "mem_total"):
                emitted = _row_val(row.values, key_names, key)
                live_val = live_mem.get("MemTotal")
                if emitted is not None and live_val is not None:
                    slack = max(live_val // 20, 65536)
                    if abs(emitted - live_val) > slack:
                        warn(f"host_mem node{node} mem_total: emitted={emitted} live={live_val}")
            for key in ("MemFree", "mem_free"):
                emitted = _row_val(row.values, key_names, key)
                live_val = live_mem.get("MemFree")
                if emitted is not None and live_val is not None:
                    slack = max(live_val // 10, 65536)
                    if abs(emitted - live_val) > slack:
                        warn(f"host_mem node{node} mem_free: emitted={emitted} live={live_val}")

    if "host_net" in full_rows and sample_fresh:
        info = manifest.get("types", {}).get("host_net", {})
        key_names = info.get("schema_key_names") or []
        for row in full_rows["host_net"]:
            for key, stat in (("rx_bytes", "rx_bytes"), ("tx_bytes", "tx_bytes")):
                emitted = _row_val(row.values, key_names, key)
                live = probe_net_stat(row.dev, stat)
                if emitted is not None and live is not None and live < emitted:
                    warn(f"host_net {row.dev} {key}: live={live} < emitted={emitted}")

    notes.append("PASS live_spot checks")
    return notes, warnings, errors
