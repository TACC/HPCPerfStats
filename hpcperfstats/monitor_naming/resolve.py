"""Dual-read helpers: canonical names first, then legacy (historical host_data)."""
from __future__ import annotations

from hpcperfstats.monitor_naming import canonical as canon
from hpcperfstats.monitor_naming import legacy as leg


def _names_canonical_then_legacy(canonical: str, legacy: str) -> tuple[str, ...]:
    if canonical == legacy:
        return (canonical,)
    return (canonical, legacy)


def imc_types_probe_order() -> tuple[str, ...]:
    """Intel IMC typenames to try (canonical first, then legacy aliases)."""
    seen: set[str] = set()
    out: list[str] = []
    for name in canon.INTEL_IMC_STATS_TYPES:
        if name not in seen:
            seen.add(name)
            out.append(name)
    for name in leg.LEGACY_INTEL_IMC_STATS_TYPES:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def arm_imc_types_probe_order() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for c in canon.ARM_IMC_STATS_TYPES:
        if c not in seen:
            seen.add(c)
            out.append(c)
    for lg in leg.LEGACY_ARM_IMC_STATS_TYPES:
        if lg not in seen:
            seen.add(lg)
            out.append(lg)
    return tuple(out)


def dram_cas_event_names() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for name in (
        canon.DRAM_CAS_READS,
        leg.LEGACY_DRAM_CAS_READS,
        canon.DRAM_CAS_WRITES,
        leg.LEGACY_DRAM_CAS_WRITES,
    ):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def dram_cas_read_write_pairs() -> tuple[tuple[str, str], ...]:
    """(reads_event, writes_event) pairs to try: canonical pair first, then legacy."""
    return (
        (canon.DRAM_CAS_READS, canon.DRAM_CAS_WRITES),
        (leg.LEGACY_DRAM_CAS_READS, leg.LEGACY_DRAM_CAS_WRITES),
    )


def instr_retired_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.INSTR_RETIRED, leg.LEGACY_INSTR_RETIRED)


def aperf_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.APERF, leg.LEGACY_APERF)


def mperf_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.MPERF, leg.LEGACY_MPERF)


def fp_ops_retired_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.FP_OPS_RETIRED, leg.LEGACY_FP_OPS_RETIRED)


def pkg_energy_event_names() -> tuple[str, ...]:
    return (
        canon.PKG_ENERGY,
        leg.LEGACY_PKG_ENERGY,
        "MSR_PKG_ENERGY_STAT",
    )


def core_pmc_types_probe_order() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for name in canon.INTEL_CORE_PMC_TYPES_ORDERED:
        if name not in seen:
            seen.add(name)
            out.append(name)
    for name in leg.LEGACY_INTEL_CORE_PMC_TYPES_ORDERED:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def pmc_typename_priority() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for name in canon.PMC_TYPENAME_PRIORITY:
        if name not in seen:
            seen.add(name)
            out.append(name)
    for name in leg.LEGACY_PMC_TYPENAME_PRIORITY:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def cha_typename_priority() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for name in canon.CHA_TYPENAME_PRIORITY:
        if name not in seen:
            seen.add(name)
            out.append(name)
    for name in leg.LEGACY_CHA_TYPENAME_PRIORITY:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def amd_pmc_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.AMD_PMC_TYPE, leg.LEGACY_AMD_PMC_TYPE)


def amd_df_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.AMD_DF_TYPE, leg.LEGACY_AMD_DF_TYPE)


def host_cpu_hw_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_CPU_HW_TYPE, leg.LEGACY_HOST_CPU_HW_TYPE)


def host_roofline_peak_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(
        canon.HOST_ROOFLINE_PEAK_TYPE,
        leg.LEGACY_HOST_ROOFLINE_PEAK_TYPE,
    )


def host_mem_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_MEM_TYPE, leg.LEGACY_HOST_MEM_TYPE)


def host_cpu_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_CPU_TYPE, leg.LEGACY_HOST_CPU_TYPE)


def host_ib_fabric_type_names() -> tuple[str, ...]:
    """InfiniBand fabric typenames (unified host_ib first, then retired collectors)."""
    return type_probe_names(canon.HOST_IB_TYPE)


def host_ib_ext_type_names() -> tuple[str, ...]:
    return host_ib_fabric_type_names()


def host_lnet_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_LNET_TYPE, leg.LEGACY_HOST_LNET_TYPE)


def host_opa_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_OPA_TYPE, leg.LEGACY_HOST_OPA_TYPE)


def host_numa_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_NUMA_TYPE, leg.LEGACY_HOST_NUMA_TYPE)


def host_nfs_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_NFS_TYPE, leg.LEGACY_HOST_NFS_TYPE)


def lustre_llite_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.LUSTRE_LLITE_TYPE, leg.LEGACY_LUSTRE_LLITE_TYPE)


def host_block_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_BLOCK_TYPE, leg.LEGACY_HOST_BLOCK_TYPE)


def host_net_type_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.HOST_NET_TYPE, leg.LEGACY_HOST_NET_TYPE)


def mem_total_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.MEM_TOTAL, leg.LEGACY_MEM_TOTAL)


def mem_used_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.MEM_USED, leg.LEGACY_MEM_USED)


def dcg_cpu_power_util_events() -> tuple[str, ...]:
    return _names_canonical_then_legacy(
        canon.DCGM_CPU_POWER_UTIL_W,
        leg.LEGACY_DCGM_CPU_POWER_UTIL_W,
    )


def dcg_cpu_power_limit_events() -> tuple[str, ...]:
    return _names_canonical_then_legacy(
        canon.DCGM_CPU_POWER_LIMIT_W,
        leg.LEGACY_DCGM_CPU_POWER_LIMIT_W,
    )


def arm_est_flops_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.ARM_EST_FLOPS, leg.LEGACY_ARM_EST_FLOPS)


def canonical_type_name(typ: str) -> str:
    """Map legacy st_name to canonical for peak tables and docs."""
    return leg.TYPE_LEGACY_TO_CANONICAL.get(typ, typ)


def event_probe_names(event: str) -> tuple[str, ...]:
    """host_data.event values to try (canonical first)."""
    seen: set[str] = set()
    out: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in seen:
            seen.add(name)
            out.append(name)

    add(event)
    add(leg.EVENT_LEGACY_TO_CANONICAL.get(event))
    canon = leg.EVENT_LEGACY_TO_CANONICAL.get(event, event)
    for leg_name, canon_name in leg.EVENT_LEGACY_TO_CANONICAL.items():
        if canon_name == canon or leg_name == event:
            add(leg_name)
            add(canon_name)
    return tuple(out)


def events_probe_names(events) -> list[str]:
    """Flatten events and include legacy aliases for host_data queries."""
    if not events:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for e in events:
        if isinstance(e, (list, tuple)):
            for sub in e:
                for name in event_probe_names(str(sub)):
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
        else:
            for name in event_probe_names(str(e)):
                if name not in seen:
                    seen.add(name)
                    out.append(name)
    return out


def type_probe_names(typ: str) -> tuple[str, ...]:
    """host_data.type values to try for ORM queries (canonical first)."""
    seen: set[str] = set()
    out: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in seen:
            seen.add(name)
            out.append(name)

    add(typ)
    add(leg.TYPE_LEGACY_TO_CANONICAL.get(typ))
    canon = canonical_type_name(typ)
    if canon != typ:
        add(canon)
    for leg_name, canon_name in leg.TYPE_LEGACY_TO_CANONICAL.items():
        if canon_name == canon or canon_name == typ:
            add(leg_name)
    return tuple(out)


def resolve_get_type(u, type_names: tuple[str, ...]):
    """First matching (schema, stats, typename) from utils.get_type candidates."""
    for typ in type_names:
        schema, stats = u.get_type(typ)
        if schema is not None:
            return schema, stats, typ
    return None, {}, None


def schema_needs_legacy_hardware_decode(typ: str, schema_events: list[str]) -> bool:
    """True when stats line must use CTL/CTR hex decode (legacy archives)."""
    for token in schema_events:
        base = token.split(",")[0]
        if base in leg.REMOVED_LEGACY_SYMBOLS:
            return True
        if "CTL" in base or "CTR" in base or "FIXED_CTR" in base:
            return True
    return typ in leg.LEGACY_HARDWARE_DECODE_TYPES
