"""Internal plausibility checks on payload values (no live re-probe)."""
from __future__ import annotations

import time

from .message_parse import schema_key_name
from .papi_shm_validate import check_papi_row_invariants
from .payload_parse import rows_by_type, sample_header_timestamp


def _value_map(row_values: list[str], key_names: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, name in enumerate(key_names):
        if i < len(row_values):
            try:
                out[name] = int(row_values[i])
            except ValueError:
                continue
    return out


def check_plausibility(
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    full_body: str | None,
    fast_body: str | None,
    schema_body: str | None,
    no_freshness: bool,
    strict: bool,
) -> tuple[list[str], list[str], list[str]]:
    notes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    def warn(msg: str) -> None:
        (errors if strict else warnings).append(f"WARN plausibility {msg}")

    enable_slow = manifest.get("enable_slow_tier", True)

    if full_body:
        ts = sample_header_timestamp(full_body)
        if ts is not None and not no_freshness:
            age = abs(time.time() - ts)
            if age > 300:
                # Stale samples (e.g. SEGV restart loop) must fail live verify under strict.
                warn(f"sample timestamp age {age:.0f}s > 300s")

        full_rows = rows_by_type(
            full_body,
            schema_by_type,
            require_tier=enable_slow,
            allowed_tier="@full" if enable_slow else None,
        )
        for type_name in schema_by_type:
            if type_name not in full_rows:
                # host_tmpfs only emits when /tmp is tmpfs (tmpfs.c). Schema may
                # still list the type when compiled in; skip presence WARN unless
                # manifest devices (from probe_tmpfs_devices) expect /tmp.
                if type_name == "host_tmpfs":
                    devices = (
                        manifest.get("types", {}).get("host_tmpfs", {}).get("devices")
                        or []
                    )
                    if not devices:
                        continue
                warn(f"type {type_name!r} missing from full payload")

        for type_name, rows in full_rows.items():
            info = manifest.get("types", {}).get(type_name, {})
            key_names = info.get("schema_key_names") or [
                schema_key_name(k) for k in schema_by_type.get(type_name, [])
            ]
            for row in rows:
                vals = _value_map(row.values, key_names)
                if "mem_total" in vals and "mem_free" in vals:
                    if vals["mem_total"] < vals["mem_free"]:
                        warn(f"{type_name}/{row.dev}: mem_total < mem_free")
                if "load_1" in vals:
                    cpus = manifest.get("types", {}).get("host_cpu", {}).get("devices") or []
                    cap = max(len(cpus) * 10000, 10000)
                    if vals["load_1"] > cap:
                        warn(f"host_ps load_1={vals['load_1']} exceeds heuristic cap {cap}")
                for msg in check_papi_row_invariants(
                    vals, type_name=type_name, dev=row.dev
                ):
                    warn(msg)
                if type_name == "intel_gpu":
                    mem_total = vals.get("gpu_mem_total_mb", 0)
                    power = vals.get("power_usage", 0)
                    if mem_total > 0 and power == 0:
                        warn(
                            f"{type_name}/{row.dev}: gpu_mem_total_mb>0 but power_usage=0 "
                            "(XPUM realtime/stats may be empty on PVC)"
                        )

    if schema_body and full_body is None:
        pass

    notes.append("PASS plausibility internal")
    return notes, warnings, errors
