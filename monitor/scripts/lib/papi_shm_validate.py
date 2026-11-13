"""host_cpu_hw counter-key contract for shm validation (Grace / aarch64 and x86).

Every host_cpu_hw schema must carry the portable cycle / FLOP / integer keys. Builds on the
aarch64 ``arm`` CPU backend (capabilities configure.cpu_backend == "arm", or the legacy
configure.likwid_overlay / configure.papi_hybrid flags of pre-3.1 Grace builds) must also carry
the measured Grace FP instruction keys arm_fp_dp_spec / arm_fp_sp_spec / arm_fp_hp_spec (D1),
which the x86 schema does not declare. Module and function names keep the historical "papi"
prefix so callers and reports stay stable.
"""
from __future__ import annotations

# Keys every host_cpu_hw schema must emit (x86 LIKWID and aarch64 arm backend).
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

# Added on the aarch64 arm backend: measured FP instruction counts by precision (D1, W=64).
HOST_CPU_HW_ARM_REQUIRED_KEYS: frozenset[str] = HOST_CPU_HW_PAPI_REQUIRED_KEYS | frozenset(
    {
        "arm_fp_dp_spec",
        "arm_fp_sp_spec",
        "arm_fp_hp_spec",
    }
)

_ST_NAME = "host_cpu_hw"


def _cfg_indicates_arm(cfg: dict) -> bool:
    return bool(
        cfg.get("cpu_backend") == "arm" or cfg.get("likwid_overlay") or cfg.get("papi_hybrid")
    )


def caps_indicate_papi_hybrid(caps: dict | None) -> bool:
    """True when compile capabilities record the aarch64 arm CPU backend.

    Accepts the pre-3.1 configure.likwid_overlay / configure.papi_hybrid flags as aliases.
    """
    if not caps:
        return False
    cfg = caps.get("configure") or {}
    if _cfg_indicates_arm(cfg):
        return True
    nested = caps.get("compile_capabilities") or {}
    nested_cfg = nested.get("configure") or {}
    return _cfg_indicates_arm(nested_cfg)


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
    """Return (notes, errors) for host_cpu_hw counter-key presence.

    On an arm-backend build host_cpu_hw is mandatory and must carry the D1 keys as well.
    Any host_cpu_hw schema must carry the portable key set, so captures cannot drop
    int8/int16 or the cycle keys quietly.
    """
    notes: list[str] = []
    errors: list[str] = []
    types = manifest.get("types") or {}
    arm = caps_indicate_papi_hybrid(caps) or caps_indicate_papi_hybrid(manifest)
    has_hw = _ST_NAME in types

    if arm and not has_hw:
        errors.append(f"FAIL papi: arm CPU backend build missing type {_ST_NAME!r} in schema/manifest")
        return notes, errors

    if not has_hw:
        return notes, errors

    required = HOST_CPU_HW_ARM_REQUIRED_KEYS if arm else HOST_CPU_HW_PAPI_REQUIRED_KEYS
    present = set(host_cpu_hw_key_names(manifest))
    missing = sorted(required - present)
    if missing:
        errors.append(f"FAIL papi: {_ST_NAME} schema missing required PAPI keys {missing}")
        return notes, errors

    notes.append(
        "PASS papi schema "
        f"({_ST_NAME} has arm_int8_ops/arm_int16_ops and FLOPs keys"
        f"{'; arm_fp_*_spec' if arm else ''})"
    )
    return notes, errors


def check_papi_row_invariants(
    vals: dict[str, int],
    *,
    type_name: str,
    dev: str,
) -> list[str]:
    """Return warning messages for one host_cpu_hw row (no errors).

    arm_est_flops is measured FLOPs (FP_FIXED_OPS_SPEC + FP_SCALE_OPS_SPEC) on Grace since 3.1,
    not the scalar SP + DP instruction sum, so no FLOPs-vs-scalar invariant is checked.
    """
    if type_name != _ST_NAME:
        return []
    warns: list[str] = []
    aperf = vals.get("aperf")
    mperf = vals.get("mperf")
    cycles = vals.get("cpu_clock_est_cycles")
    if aperf is not None and mperf is not None and aperf != mperf:
        warns.append(f"{type_name}/{dev}: aperf={aperf} != mperf={mperf}")
    # aperf is W=48 (emitted modulo 2^48); cpu_clock_est_cycles is W=64.
    if aperf is not None and cycles is not None and aperf != cycles & ((1 << 48) - 1):
        warns.append(f"{type_name}/{dev}: aperf={aperf} != cpu_clock_est_cycles={cycles}")
    for key in ("arm_int8_ops", "arm_int16_ops", "arm_fp_dp_spec", "arm_fp_sp_spec", "arm_fp_hp_spec"):
        if key in vals and vals[key] < 0:
            warns.append(f"{type_name}/{dev}: {key}={vals[key]} is negative")
    return warns
