"""Compare emitted metrics against live /proc,/sys reads on the data host."""
from __future__ import annotations

import time
from pathlib import Path

from .host_live_probes import (
    probe_cpu_devices,
    probe_host_fqdn,
    probe_loadavg_scaled,
    probe_net_stat,
    probe_numa_mem_kb,
    probe_nvidia_gpu_devices,
    probe_nvidia_smi_mem_total_mib,
    probe_power_socket_devices,
    probe_power_socket_watts,
    probe_x86_cpu_vendor,
)
from .message_parse import schema_key_name
from .papi_shm_validate import check_x86_host_cpu_hw_key_sets
from .payload_parse import metric_value_at_key, row_key_names, rows_by_type, sample_header_timestamp
from .shm_snapshot import read_shm_sample, wait_for_timestamp_advance


def fresh_power_sample(shm_dir: Path, kind: str, timeout_sec: float) -> tuple[str, dict] | None:
    """(body, live meter watts) for the next ``kind`` sample, with the meters read as soon as it
    lands, so the emitted gauge and the live read average over nearly the same window (an older
    sample against a read taken after the validator's own stimulus differs by tens of W). None
    when the file has no sample or none lands within ``timeout_sec``."""
    path = shm_dir / kind
    try:
        prev = read_shm_sample(path)[0]
        if prev is None:
            return None
        _, body = wait_for_timestamp_advance(path, prev, timeout_sec=timeout_sec,
                                             poll_interval_sec=0.1)
    except (OSError, TimeoutError):
        return None
    return body, probe_power_socket_watts()


def run_live_spot_checks(
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    full_body: str,
    max_age_sec: float,
    strict: bool,
    shm_dir: Path | None = None,
    fresh_timeout_sec: float = 0.0,
) -> tuple[list[str], list[str], list[str]]:
    """Notes, warnings and errors (strict). host_power_socket is compared on a fresh sample read
    from ``shm_dir`` (``fast`` with the slow tier, else ``full``), waiting at most
    ``fresh_timeout_sec`` for it."""
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
                emitted = metric_value_at_key(row.values, key_names, key)
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
                emitted = metric_value_at_key(row.values, key_names, key)
                live_val = live_mem.get("MemTotal")
                if emitted is not None and live_val is not None:
                    slack = max(live_val // 20, 65536)
                    if abs(emitted - live_val) > slack:
                        warn(f"host_mem node{node} mem_total: emitted={emitted} live={live_val}")
            for key in ("MemFree", "mem_free"):
                emitted = metric_value_at_key(row.values, key_names, key)
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
                emitted = metric_value_at_key(row.values, key_names, key)
                live = probe_net_stat(row.dev, stat)
                if emitted is not None and live is not None and live < emitted:
                    warn(f"host_net {row.dev} {key}: live={live} < emitted={emitted}")

    # Types the hardware says must be there (not derived from the schema under test).
    # host_power_socket is compiled into every build; nvidia_gpu only with configure gpu.
    # Shared nodes mirror a per-job payload into `full` while jobs run; node-wide rows
    # (host_power_socket, D38) and unassigned GPUs only appear in the `-` payload.
    built_gpu = manifest.get("compile_capabilities", {}).get("configure", {}).get("gpu", True)
    node_wide = (full_body.lstrip().split("\n", 1)[0].split()[1:2] or [""])[0] == "-"
    for type_name, probe, built in (
        ("host_power_socket", probe_power_socket_devices, node_wide),
        ("nvidia_gpu", probe_nvidia_gpu_devices, built_gpu and node_wide),
    ):
        devs = probe() if built else []
        if not devs:
            continue
        if type_name not in schema_by_type:
            warn(f"{type_name} missing from schema although the host has {len(devs)} devices")
        elif sorted(r.dev for r in full_rows.get(type_name, [])) != sorted(devs):
            got = sorted(r.dev for r in full_rows.get(type_name, []))
            warn(f"{type_name} rows {got} != live devices {sorted(devs)}")

    def _rows_with_keys(type_name: str):
        names = manifest.get("types", {}).get(type_name, {}).get("schema_key_names") or [
            schema_key_name(k) for k in schema_by_type.get(type_name, [])
        ]
        return names, full_rows.get(type_name, [])

    if "host_power_socket" in full_rows:
        kind = "fast" if enable_slow else "full"
        fresh = fresh_power_sample(shm_dir, kind, fresh_timeout_sec) if shm_dir is not None else None
        if fresh is None:
            notes.append(f"NOTE live_spot host_power_socket: no fresh {kind} sample within "
                         f"{fresh_timeout_sec:.0f}s; power comparison skipped")
        else:
            body, live_w = fresh
            tier = next((t for t in ("@fast", "@full") if f" {t}" in body), None)
            rows = rows_by_type(body, schema_by_type, require_tier=tier is not None,
                                allowed_tier=tier).get("host_power_socket", [])
            if not rows:
                notes.append(f"NOTE live_spot host_power_socket: fresh {kind} sample has no "
                             "host_power_socket rows; power comparison skipped")
            for row in rows:
                names = row_key_names(row, schema_by_type)
                for key in names:
                    live = live_w.get((row.dev, key))
                    emitted = metric_value_at_key(row.values, names, key)
                    if live is None:
                        if emitted:
                            warn(f"host_power_socket/{row.dev} {key}={emitted} W but no live meter")
                        continue
                    # The meters average over different windows and socket power moves within
                    # seconds (ondemand governor), so a value mismatch is never strict.
                    if emitted is not None and abs(emitted - live) > max(15.0, 0.25 * live):
                        warnings.append(f"WARN live_spot host_power_socket/{row.dev} {key}: "
                                        f"emitted={emitted} W live={live:.1f} W")

    if "host_cpu_hw" in full_rows:
        vendor = probe_x86_cpu_vendor()
        names, rows = _rows_with_keys("host_cpu_hw")
        for row in rows:
            for msg in check_x86_host_cpu_hw_key_sets(vendor, row.values, names, dev=row.dev):
                warn(msg)

    if "nvidia_gpu" in full_rows:
        smi = probe_nvidia_smi_mem_total_mib()
        names, rows = _rows_with_keys("nvidia_gpu")
        for row in rows:
            emitted = metric_value_at_key(row.values, names, "gpu_mem_total_mb")
            live = smi.get(row.dev)
            if emitted is not None and live and abs(emitted - live) > max(64, live // 50):
                warn(f"nvidia_gpu/{row.dev} gpu_mem_total_mb: emitted={emitted} nvidia-smi={live}")

    notes.append("PASS live_spot checks")
    return notes, warnings, errors
