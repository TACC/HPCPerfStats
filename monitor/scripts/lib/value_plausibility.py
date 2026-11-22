"""Internal plausibility checks on payload values (no live re-probe)."""
from __future__ import annotations

import time

from .message_parse import (
    schema_key_name,
    schema_token_is_event_counter,
)
from .papi_shm_validate import (
    caps_indicate_arm_backend,
    check_host_cpu_hw_rates,
    check_papi_row_invariants,
)
from .payload_parse import row_key_names, rows_by_type, sample_header_timestamp

# Keys retired by round 2 (D33/D36): their presence in a schema means an old emitter.
RETIRED_KEYS = {
    "host_cpu_hw": ("dcgm_cpu_power_util_w", "dcgm_cpu_power_limit_w"),
    "nvidia_gpu": ("module_power_usage", "sysio_power_usage"),
}
# Exact key order (arm_power_socket.h ARM_POWER_SOCKET_KEYS).
EXACT_KEYS = {
    "host_power_socket": ("cpu_power_w", "socket_power_w", "sysio_power_w", "module_power_w"),
}
NVIDIA_GPU_PERCENT_KEYS = (
    "gpu_util", "gpu_mem_util", "fp64_active", "sm_active", "sm_occupancy", "fp32_active",
    "fp16_active", "tensor_active", "tensor_imma_active", "tensor_hmma_active",
    "tensor_dfma_active", "tensor_dmma_active", "integer_active", "gpu_dram_active",
)
INTEL_GPU_PERCENT_KEYS = ("compute_engine_util",)
# Rows only exist with /tmp on tmpfs, a Lustre/BeeGFS/NFS mount, a running job, or a
# collectible IB / OPA port (ib_port_collectible: all ports down or Ethernet -> no rows).
CONDITIONAL_TYPES = frozenset({
    "host_tmpfs", "host_nfs", "beegfs_client", "lustre_llite", "lustre_mdc", "lustre_osc",
    "host_mem_job", "host_proc", "host_ib", "host_opa",
})
POWER_SOCKET_MAX_W = 2000
POWER_SOCKET_SLACK_W = 2  # meters average over different windows


def check_schema_contract(schema_by_type: dict[str, list[str]]) -> list[str]:
    """Retired keys present, or a type whose key list differs from its fixed contract."""
    out: list[str] = []
    for type_name, keys in schema_by_type.items():
        names = [schema_key_name(k) for k in keys]
        for key in RETIRED_KEYS.get(type_name, ()):
            if key in names:
                out.append(f"{type_name}: retired key {key!r} still in schema (D33)")
        want = EXACT_KEYS.get(type_name)
        if want is not None and tuple(names) != want:
            out.append(f"{type_name}: schema keys {names} != {list(want)}")
    return out


def check_row_invariants(type_name: str, dev: str, vals: dict[str, int]) -> list[str]:
    """Value relations that hold on every sane host_power_socket / nvidia_gpu / intel_gpu row."""
    out: list[str] = []
    if type_name == "host_power_socket":
        watts = {k: vals.get(k, 0) for k in EXACT_KEYS["host_power_socket"]}
        if not any(watts.values()):
            out.append(f"{type_name}/{dev}: every meter reads 0 W")
        for key, w in watts.items():
            if w > POWER_SOCKET_MAX_W:
                out.append(f"{type_name}/{dev}: {key}={w} W > {POWER_SOCKET_MAX_W}")
        socket = watts["socket_power_w"]
        for part in ("cpu_power_w", "sysio_power_w"):
            if socket and watts[part] > socket + POWER_SOCKET_SLACK_W:
                out.append(f"{type_name}/{dev}: {part}={watts[part]} > socket_power_w={socket}")
    elif type_name == "nvidia_gpu":
        for key in NVIDIA_GPU_PERCENT_KEYS:
            if vals.get(key, 0) > 100:
                out.append(f"{type_name}/{dev}: {key}={vals[key]} > 100 %")
        total = vals.get("gpu_mem_total_mb", 0)
        used_free = vals.get("gpu_mem_used_mb", 0) + vals.get("gpu_mem_free_mb", 0)
        if total and used_free > total * 1.02 + 256:
            out.append(f"{type_name}/{dev}: used+free={used_free} MB > total={total} MB")
        if vals.get("temperature", 0) > 150:
            out.append(f"{type_name}/{dev}: temperature={vals['temperature']} C > 150")
    elif type_name == "intel_gpu":
        for key in INTEL_GPU_PERCENT_KEYS:
            if vals.get(key, 0) > 100:
                out.append(f"{type_name}/{dev}: {key}={vals[key]} > 100 %")
    return out


def _narrow_counter_widths(keys: list[str]) -> dict[str, int]:
    """key -> W for each ``E`` key narrower than 64 bits."""
    out: dict[str, int] = {}
    for tok in keys:
        w = [p[2:] for p in tok.split(",")[1:] if p.startswith("W=")]
        if schema_token_is_event_counter(tok) and w and int(w[0]) < 64:
            out[schema_key_name(tok)] = int(w[0])
    return out


def _host_cpu_hw_values(
    body: str, schema_by_type: dict[str, list[str]], *, enable_slow: bool, tier: str
) -> dict[str, dict[str, int]]:
    """dev -> key -> value of the host_cpu_hw rows of one payload (``tier`` "@fast" / "@full")."""
    rows = rows_by_type(
        body, schema_by_type, require_tier=enable_slow, allowed_tier=tier if enable_slow else None
    ).get("host_cpu_hw", [])
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        out[row.dev] = _value_map(row.values, row_key_names(row, schema_by_type))
    return out


def check_host_cpu_hw_pair_rates(
    schema_by_type: dict[str, list[str]],
    *,
    fast_body: str,
    full_body: str,
    enable_slow: bool,
    arm: bool,
) -> list[str]:
    """Rate ceilings between the fast and full payloads' host_cpu_hw rows, which the daemon wrote
    at different ticks of the same lifetimes (skipped when under a second apart)."""
    keys = schema_by_type.get("host_cpu_hw")
    ts_fast, ts_full = sample_header_timestamp(fast_body), sample_header_timestamp(full_body)
    if not keys or ts_fast is None or ts_full is None or abs(ts_fast - ts_full) < 1.0:
        return []
    caps = [
        (ts_fast, _host_cpu_hw_values(fast_body, schema_by_type, enable_slow=enable_slow, tier="@fast")),
        (ts_full, _host_cpu_hw_values(full_body, schema_by_type, enable_slow=enable_slow, tier="@full")),
    ]
    caps.sort(key=lambda c: c[0])
    (t_old, old), (t_new, new) = caps
    widths = _narrow_counter_widths(keys)
    out: list[str] = []
    for dev in sorted(old.keys() & new.keys()):
        out.extend(check_host_cpu_hw_rates(old[dev], new[dev], t_new - t_old, widths, dev=dev, arm=arm))
    return out


def _value_map(row_values: list[str], key_names: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, name in enumerate(key_names):
        if i < len(row_values) and row_values[i].isdigit():
            out[name] = int(row_values[i])
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
    arm = caps_indicate_arm_backend(manifest)
    # x86 direct perf publishes W<64 host_cpu_hw lifetimes modulo 2^W (O11). The Grace
    # util x freq aperf / mperf fallback is not masked, so the range check is x86-only.
    hw_widths = {} if arm else _narrow_counter_widths(schema_by_type.get("host_cpu_hw", []))

    for msg in check_schema_contract(schema_by_type):
        warn(msg)

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
            # Conditional types emit no rows without a mount, job or port; warn only when
            # the manifest's probed devices expect some.
            devices = manifest.get("types", {}).get(type_name, {}).get("devices")
            if type_name not in full_rows and not (type_name in CONDITIONAL_TYPES and not devices):
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
                    vals, type_name=type_name, dev=row.dev, arm=arm
                ):
                    warn(msg)
                if type_name == "host_cpu_hw":
                    for key, width in hw_widths.items():
                        if vals.get(key, 0) >> width:
                            warn(f"{type_name}/{row.dev}: {key}={vals[key]} >= 2^{width} (W={width} lifetime not masked)")
                for msg in check_row_invariants(type_name, row.dev, vals):
                    warn(msg)
                if type_name == "intel_gpu":
                    mem_total = vals.get("gpu_mem_total_mb", 0)
                    power = vals.get("power_usage", 0)
                    if mem_total > 0 and power == 0:
                        warn(
                            f"{type_name}/{row.dev}: gpu_mem_total_mb>0 but power_usage=0 "
                            "(XPUM realtime/stats may be empty on PVC)"
                        )

    if full_body and fast_body:
        for msg in check_host_cpu_hw_pair_rates(
            schema_by_type, fast_body=fast_body, full_body=full_body, enable_slow=enable_slow, arm=arm
        ):
            warn(msg)

    notes.append("PASS plausibility internal")
    return notes, warnings, errors
