"""PAPI hybrid host_cpu_hw contract for shm validation (Grace / aarch64).

When the build sets MONITOR_CPU_PAPI_FLOPS (capabilities configure.papi_hybrid),
or when host_cpu_hw appears in the expectations schema, require the portable
PAPI keys including arm_int8_ops / arm_int16_ops and check value invariants.
"""
from __future__ import annotations

# Schema keys that PAPI hybrid must emit on host_cpu_hw (additive to DCGM keys).
HOST_CPU_HW_PAPI_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "aperf",
        "mperf",
        "cpu_clock_est_cycles",
        "fp_arith_inst_retired_scalar_single",
        "fp_arith_inst_retired_scalar_double",
        "arm_est_flops",
        "arm_int8_ops",
        "arm_int16_ops",
    }
)

_ST_NAME = "host_cpu_hw"


def caps_indicate_papi_hybrid(caps: dict | None) -> bool:
    """True when compile capabilities record MONITOR_CPU_PAPI_FLOPS."""
    if not caps:
        return False
    cfg = caps.get("configure") or {}
    if cfg.get("papi_hybrid"):
        return True
    # Manifest may nest the same blob under compile_capabilities.
    nested = caps.get("compile_capabilities") or {}
    nested_cfg = nested.get("configure") or {}
    return bool(nested_cfg.get("papi_hybrid"))


def host_cpu_hw_key_names(manifest: dict) -> list[str]:
    info = (manifest.get("types") or {}).get(_ST_NAME) or {}
    names = info.get("schema_key_names")
    if names:
        return list(names)
    keys = info.get("schema_keys") or []
    out: list[str] = []
    for tok in keys:
        out.append(tok.split(",", 1)[0])
    return out


def check_papi_schema_contract(
    manifest: dict,
    *,
    caps: dict | None = None,
) -> tuple[list[str], list[str]]:
    """Return (notes, errors) for host_cpu_hw PAPI key presence.

    When papi_hybrid is set in capabilities, host_cpu_hw and all required keys
    are mandatory. When host_cpu_hw is present without the flag, still require
    the full PAPI key set so Grace shm captures cannot drop int8/int16 quietly.
    """
    notes: list[str] = []
    errors: list[str] = []
    types = manifest.get("types") or {}
    hybrid = caps_indicate_papi_hybrid(caps) or caps_indicate_papi_hybrid(manifest)
    has_hw = _ST_NAME in types

    if hybrid and not has_hw:
        errors.append(
            f"FAIL papi: papi_hybrid build missing type {_ST_NAME!r} in schema/manifest"
        )
        return notes, errors

    if not has_hw:
        return notes, errors

    present = set(host_cpu_hw_key_names(manifest))
    missing = sorted(HOST_CPU_HW_PAPI_REQUIRED_KEYS - present)
    if missing:
        errors.append(
            f"FAIL papi: {_ST_NAME} schema missing required PAPI keys {missing}"
        )
        return notes, errors

    notes.append(
        "PASS papi schema "
        f"({_ST_NAME} has arm_int8_ops/arm_int16_ops and FLOPs keys)"
    )
    return notes, errors


def check_papi_row_invariants(
    vals: dict[str, int],
    *,
    type_name: str,
    dev: str,
) -> list[str]:
    """Return warning messages for one host_cpu_hw row (no errors)."""
    if type_name != _ST_NAME:
        return []
    warns: list[str] = []
    sp = vals.get("fp_arith_inst_retired_scalar_single")
    dp = vals.get("fp_arith_inst_retired_scalar_double")
    flops = vals.get("arm_est_flops")
    if sp is not None and dp is not None and flops is not None:
        expect = sp + dp
        if flops != expect:
            warns.append(
                f"{type_name}/{dev}: arm_est_flops={flops} != "
                f"scalar_single+scalar_double={expect}"
            )
    aperf = vals.get("aperf")
    mperf = vals.get("mperf")
    cycles = vals.get("cpu_clock_est_cycles")
    if aperf is not None and mperf is not None and aperf != mperf:
        warns.append(f"{type_name}/{dev}: aperf={aperf} != mperf={mperf}")
    if aperf is not None and cycles is not None and aperf != cycles:
        warns.append(
            f"{type_name}/{dev}: aperf={aperf} != cpu_clock_est_cycles={cycles}"
        )
    for key in ("arm_int8_ops", "arm_int16_ops"):
        if key in vals and vals[key] < 0:
            warns.append(f"{type_name}/{dev}: {key}={vals[key]} is negative")
    return warns
