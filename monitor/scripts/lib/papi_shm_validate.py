"""host_cpu_hw counter-key contract for shm validation (Grace / aarch64 and x86 direct perf).

Every host_cpu_hw schema carries the portable cycle / FLOP / integer keys, the x86 FLOP lifetimes
fp_ops_retired / amd_fp_dp_ops and the PMU sharing time lifetimes pmu_enabled_time /
pmu_running_time / pmu_solo_running_time (D6, every platform). Builds on the aarch64 ``arm`` CPU
backend (capabilities configure.cpu_backend == "arm") also carry the measured Grace FP
instruction keys arm_fp_dp_spec / arm_fp_sp_spec / arm_fp_hp_spec (D1). Module and function
names keep the historical "papi" prefix so callers and reports stay stable.

On x86 both vendors read the core PMU by direct perf: one event may feed two keys (instructions
-> instr_retired_any + instr_retired, cycles -> cycles_unhalted_core + aperf, ref-cycles ->
cycles_unhalted_ref + mperf), the W=48 keys carry the software lifetime modulo 2^48 (O11), and a
key the CPU class has no event for is NOT_PRESENT, never NOT_BUILT.
"""
from __future__ import annotations

from .message_parse import schema_key_name
from .payload_parse import metric_sentinel_at_key, metric_value_at_key
from .sentinel_catalog import sentinel_slug

# Keys every host_cpu_hw schema must emit (x86 direct perf and aarch64 arm backend).
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
        "fp_ops_retired",
        "amd_fp_dp_ops",
        "pmu_enabled_time",
        "pmu_running_time",
        "pmu_solo_running_time",
    }
)

# Added on the aarch64 arm backend: measured FP instruction counts by precision (D1, W=64).
HOST_CPU_HW_ARM_REQUIRED_KEYS: frozenset[str] = HOST_CPU_HW_PAPI_REQUIRED_KEYS | frozenset(
    {"arm_fp_dp_spec", "arm_fp_sp_spec", "arm_fp_hp_spec"}
)

# x86 keys only one vendor's event table fills; the other vendor never emits a value there.
# ref-cycles (cycles_unhalted_ref, mperf) does not exist on AMD.
X86_INTEL_ONLY_KEYS: tuple[str, ...] = (
    "cycles_unhalted_ref",
    "mperf",
    "l1d_replacement",
    "mem_load_uops_retired_l1_hit",
    "mem_load_uops_retired_l2_hit",
    "mem_load_uops_retired_llc_hit",
    "fp_arith_inst_retired_scalar_double",
    "fp_arith_inst_retired_scalar_single",
    "fp_arith_inst_retired_128b_packed_double",
    "fp_arith_inst_retired_128b_packed_single",
    "fp_arith_inst_retired_256b_packed_double",
    "fp_arith_inst_retired_256b_packed_single",
    "fp_arith_inst_retired_512b_packed_double",
    "fp_arith_inst_retired_512b_packed_single",
)
X86_AMD_ONLY_KEYS: tuple[str, ...] = (
    "retired_instructions",
    "retired_branch_instr",
    "retired_misp_branch_instr",
    "ls_dispatch",
    "fp_ops_retired",
    "amd_fp_dp_ops",
)
# Keys of the generic perf events (sysfs instructions / cycles) both vendors program.
X86_GENERIC_KEYS: tuple[str, ...] = ("instr_retired", "instr_retired_any", "aperf", "cycles_unhalted_core")

# Key pairs one x86 event feeds; both carry the same lifetime modulo 2^48.
X86_SAME_EVENT_PAIRS: tuple[tuple[str, str], ...] = (
    ("instr_retired", "instr_retired_any"),
    ("aperf", "cycles_unhalted_core"),
    ("mperf", "cycles_unhalted_ref"),
)

# Unit bounds against the summed enabled time (ns, D6): any one open event was enabled at most
# pmu_enabled_time ns, so a cycle lifetime cannot exceed it x the clock ceiling, nor a FLOP
# lifetime exceed it x clock x the widest per-cycle FLOP rate (2 x 512-bit FMA, 16 SP lanes).
X86_MAX_CYCLES_PER_NS = 10
X86_MAX_FLOPS_PER_NS = X86_MAX_CYCLES_PER_NS * 64
_X86_CYCLE_KEYS = ("aperf", "cycles_unhalted_core", "mperf", "cycles_unhalted_ref")
_X86_FLOP_KEYS = ("fp_ops_retired", "amd_fp_dp_ops")

# Types whose rows may carry the D6 sharing times (uncore rows per device).
_PMU_TIME_TYPE_PREFIXES = ("host_cpu_hw", "intel_x86_uncore_imc", "amd_x86_uncore_df")

_ST_NAME = "host_cpu_hw"


def _cfg_indicates_arm(cfg: dict) -> bool:
    return cfg.get("cpu_backend") == "arm"


def caps_indicate_arm_backend(caps: dict | None) -> bool:
    """True when compile capabilities (or a manifest carrying them) record the arm CPU backend."""
    if not caps:
        return False
    if _cfg_indicates_arm(caps.get("configure") or {}):
        return True
    nested = caps.get("compile_capabilities") or {}
    return _cfg_indicates_arm(nested.get("configure") or {})


def host_cpu_hw_key_names(manifest: dict) -> list[str]:
    info = (manifest.get("types") or {}).get(_ST_NAME) or {}
    names = info.get("schema_key_names")
    if names:
        return list(names)
    return [schema_key_name(tok) for tok in info.get("schema_keys") or []]


def check_papi_schema_contract(
    manifest: dict,
    *,
    caps: dict | None = None,
) -> tuple[list[str], list[str]]:
    """Return (notes, errors) for host_cpu_hw counter-key presence.

    On an arm-backend build host_cpu_hw is mandatory and must carry the D1 keys as well.
    Any host_cpu_hw schema must carry the portable key set, so captures cannot drop
    int8/int16, the FLOP lifetimes, the sharing times or the cycle keys quietly.
    """
    notes: list[str] = []
    errors: list[str] = []
    types = manifest.get("types") or {}
    arm = caps_indicate_arm_backend(caps) or caps_indicate_arm_backend(manifest)
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
        f"({_ST_NAME} has arm_int8_ops/arm_int16_ops, FLOP and pmu_*_time keys"
        f"{'; arm_fp_*_spec' if arm else ''})"
    )
    return notes, errors


def check_papi_row_invariants(
    vals: dict[str, int],
    *,
    type_name: str,
    dev: str,
    arm: bool,
) -> list[str]:
    """Return warning messages for one host_cpu_hw (or uncore sharing-time) row (no errors).

    arm_est_flops is measured FLOPs (FP_FIXED_OPS_SPEC + FP_SCALE_OPS_SPEC) on Grace since 3.1,
    not the scalar SP + DP instruction sum, so no FLOPs-vs-scalar invariant is checked. On Grace
    aperf, mperf and cpu_clock_est_cycles all come from CPU_CYCLES; on x86 aperf (cycles) and
    mperf (ref-cycles) differ under turbo, so the x86 checks compare each key with the other key
    its event feeds and bound the cycle / FLOP lifetimes by the summed enabled time.
    """
    if not type_name.startswith(_PMU_TIME_TYPE_PREFIXES):
        return []
    warns: list[str] = []
    # D6: an event cannot run (or be expected to run) longer than it was enabled.
    enabled = vals.get("pmu_enabled_time")
    for key in ("pmu_running_time", "pmu_solo_running_time"):
        if enabled is not None and key in vals and vals[key] > enabled:
            warns.append(f"{type_name}/{dev}: {key}={vals[key]} > pmu_enabled_time={enabled}")
    if type_name != _ST_NAME:
        return warns
    for key in ("arm_int8_ops", "arm_int16_ops", "arm_fp_dp_spec", "arm_fp_sp_spec", "arm_fp_hp_spec"):
        if key in vals and vals[key] < 0:
            warns.append(f"{type_name}/{dev}: {key}={vals[key]} is negative")
    aperf = vals.get("aperf")
    if arm:
        mperf = vals.get("mperf")
        cycles = vals.get("cpu_clock_est_cycles")
        if aperf is not None and mperf is not None and aperf != mperf:
            warns.append(f"{type_name}/{dev}: aperf={aperf} != mperf={mperf}")
        # aperf is W=48 (emitted modulo 2^48); cpu_clock_est_cycles is W=64.
        if aperf is not None and cycles is not None and aperf != cycles & ((1 << 48) - 1):
            warns.append(f"{type_name}/{dev}: aperf={aperf} != cpu_clock_est_cycles={cycles}")
        return warns
    for a, b in X86_SAME_EVENT_PAIRS:
        if a in vals and b in vals and vals[a] != vals[b]:
            warns.append(f"{type_name}/{dev}: {a}={vals[a]} != {b}={vals[b]} (same perf event)")
    if enabled:
        # A wrapped W=48 key only gets smaller, so the upper bound holds after a wrap too.
        for keys, per_ns in ((_X86_CYCLE_KEYS, X86_MAX_CYCLES_PER_NS), (_X86_FLOP_KEYS, X86_MAX_FLOPS_PER_NS)):
            for key in keys:
                if key in vals and vals[key] > enabled * per_ns:
                    warns.append(
                        f"{type_name}/{dev}: {key}={vals[key]} > {per_ns} per ns of"
                        f" pmu_enabled_time={enabled} (unit or scaling error)"
                    )
    return warns


def _forward_delta(old: int, new: int, width: int) -> int | None:
    """new - old modulo 2^width; None when the forward distance is half the range or more (a
    decrease, which the cross-sample monotonic layer reports)."""
    d = (new - old) % (1 << width)
    return None if d >> (width - 1) else d


def check_host_cpu_hw_rates(
    old: dict[str, int],
    new: dict[str, int],
    dt: float,
    widths: dict[str, int],
    *,
    dev: str,
    arm: bool,
) -> list[str]:
    """Warnings for one host_cpu_hw row captured twice ``dt`` seconds apart: a cycle or FLOP
    lifetime advancing faster than its physical ceiling (W<64 keys wrap modulo 2^W), or
    running / solo time advancing more than enabled time. Grace aperf / mperf /
    cpu_clock_est_cycles switch between the measured CPU_CYCLES lifetime and the util x freq
    estimate tick by tick, so their rate is not checked there."""
    warns: list[str] = []
    flop_keys = ("arm_est_flops",) if arm else _X86_FLOP_KEYS
    ceilings = [(flop_keys, X86_MAX_FLOPS_PER_NS * 10**9)]
    if not arm:
        ceilings.append((_X86_CYCLE_KEYS, X86_MAX_CYCLES_PER_NS * 10**9))
    for keys, ceiling in ceilings:
        for key in keys:
            if key not in old or key not in new:
                continue
            d = _forward_delta(old[key], new[key], widths.get(key, 64))
            if d is not None and d > ceiling * dt:
                warns.append(f"{_ST_NAME}/{dev}: {key} rate {d / dt:.3g}/s > {ceiling:.3g}/s")
    if "pmu_enabled_time" in old and "pmu_enabled_time" in new:
        d_en = _forward_delta(old["pmu_enabled_time"], new["pmu_enabled_time"], 64)
        for key in ("pmu_running_time", "pmu_solo_running_time"):
            if d_en is None or key not in old or key not in new:
                continue
            d = _forward_delta(old[key], new[key], 64)
            if d is not None and d > d_en:
                warns.append(f"{_ST_NAME}/{dev}: {key} advanced {d} ns > pmu_enabled_time {d_en} ns")
    return warns


def check_x86_host_cpu_hw_key_sets(
    vendor: str | None,
    row_values: list[str],
    key_names: list[str],
    *,
    dev: str,
) -> list[str]:
    """Vendor key-set problems on one x86 host_cpu_hw row (``vendor`` "intel" / "amd" from the
    live /proc/cpuinfo; any other vendor returns no message).

    - The other vendor's keys (and mperf on AMD, which has no ref-cycles) never carry a value.
    - This vendor's keys and the generic instructions / cycles keys are never NOT_BUILT: direct
      perf programs every event the class table names and emits NOT_PRESENT for the rest
      (amd_fp_dp_ops before Zen5, FP_ARITH on an unknown Intel model).
    """
    if vendor == "intel":
        foreign, own = X86_AMD_ONLY_KEYS, X86_INTEL_ONLY_KEYS + X86_GENERIC_KEYS
    elif vendor == "amd":
        foreign, own = X86_INTEL_ONLY_KEYS, X86_AMD_ONLY_KEYS + X86_GENERIC_KEYS
    else:
        return []
    label = vendor.upper()
    out: list[str] = []
    for key in foreign:
        v = metric_value_at_key(row_values, key_names, key)
        if v is not None:
            out.append(f"host_cpu_hw/{dev} {key}={v} on an {label} CPU (no such event)")
    for key in own:
        code = metric_sentinel_at_key(row_values, key_names, key)
        if code is not None and sentinel_slug(code) == "NOT_BUILT":
            out.append(f"host_cpu_hw/{dev} {key} is NOT_BUILT on an {label} CPU (direct perf reads it)")
    return out
