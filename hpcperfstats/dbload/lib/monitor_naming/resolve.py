"""Dual-read helpers: canonical names first, then legacy (historical host_data)."""
from __future__ import annotations

from hpcperfstats.dbload.lib.monitor_naming import canonical as canon
from hpcperfstats.dbload.lib.monitor_naming import legacy as leg


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


def hbm_cas_read_write_pairs() -> tuple[tuple[str, str], ...]:
    """(reads_event, writes_event) for SPR HBM CAS (canonical only; no legacy aliases)."""
    return ((canon.HBM_CAS_READS, canon.HBM_CAS_WRITES),)


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


def amd_df_types_probe_order() -> tuple[str, ...]:
    """AMD DF typenames: live family first, then historical bare / amd64_df."""
    seen: set[str] = set()
    out: list[str] = []
    for name in canon.AMD_DF_STATS_TYPES:
        if name not in seen:
            seen.add(name)
            out.append(name)
    for name in (canon.AMD_DF_TYPE, leg.LEGACY_AMD_DF_TYPE):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def amd_df_type_names() -> tuple[str, ...]:
    return amd_df_types_probe_order()


def amd_df_bw_event_conv_tries() -> tuple[tuple[tuple[str, ...], float], ...]:
    """(events, conv) for AMD DF BW: live byte counters first, then historical MBW."""
    return (
        (canon.DRAM_CHAN_BYTES_EVENTS, 1.0 / (1024 ** 3)),
        (leg.LEGACY_AMD_DF_MBW_CHANNEL_EVENTS, 2.0 / (1024 ** 3)),
    )


def rapl_types_probe_order() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for name in canon.INTEL_RAPL_STATS_TYPES + canon.AMD_RAPL_STATS_TYPES:
        if name not in seen:
            seen.add(name)
            out.append(name)
    for name in leg.LEGACY_INTEL_RAPL_STATS_TYPES + leg.LEGACY_AMD_RAPL_STATS_TYPES:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


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


def arm_int8_ops_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.ARM_INT8_OPS, leg.LEGACY_ARM_INT8_OPS)


def arm_int16_ops_event_names() -> tuple[str, ...]:
    return _names_canonical_then_legacy(canon.ARM_INT16_OPS, leg.LEGACY_ARM_INT16_OPS)


def grace_fp_scalar_double_event_names() -> tuple[str, ...]:
    """Grace host_cpu_hw scalar FP64 event (canonical only; no Intel uppercase)."""
    return (canon.GRACE_FP_ARITH_SCALAR_DOUBLE,)


def grace_fp_scalar_single_event_names() -> tuple[str, ...]:
    """Grace host_cpu_hw scalar FP32 event (canonical only; no Intel uppercase)."""
    return (canon.GRACE_FP_ARITH_SCALAR_SINGLE,)


def canonical_type_name(typ: str) -> str:
    """Map legacy st_name to canonical for peak tables and docs."""
    return leg.TYPE_LEGACY_TO_CANONICAL.get(typ, typ)


def _type_scoped_event_map(typ: str | None) -> dict[str, str]:
    """Legacy→canonical event map for ``typ``, or empty when type has no type_events."""
    if not typ:
        return {}
    canon_typ = canonical_type_name(typ)
    mapping = leg.TYPE_EVENT_LEGACY_TO_CANONICAL.get(canon_typ)
    if mapping:
        return mapping
    return leg.TYPE_EVENT_LEGACY_TO_CANONICAL.get(typ) or {}


def event_probe_names(event: str) -> tuple[str, ...]:
    """host_data.event values to try (canonical first) using the **global** events map.

    For type-scoped renames (llite ``open`` / ``read_bytes``), use
    ``event_probe_names_for_type`` so global ``open`` is not rewritten.
    """
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


def event_probe_names_for_type(typ: str, event: str) -> tuple[str, ...]:
    """host_data.event probes for a typename (type_events when present, else global)."""
    te_map = _type_scoped_event_map(typ)
    if not te_map:
        return event_probe_names(event)

    seen: set[str] = set()
    out: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in seen:
            seen.add(name)
            out.append(name)

    canon = te_map.get(event)
    if canon is None and event in te_map.values():
        canon = event
    if canon is None:
        # Event is unrelated to this type's rename map (e.g. capacity gauges).
        return event_probe_names(event)

    add(canon)
    for leg_name, canon_name in te_map.items():
        if canon_name == canon:
            add(leg_name)
    add(event)
    return tuple(out)


def canonical_event_name_for_type(typ: str, event: str) -> str:
    """Map a stored host_data.event to its canonical name for ``typ`` when known."""
    te_map = _type_scoped_event_map(typ)
    if not te_map:
        return leg.EVENT_LEGACY_TO_CANONICAL.get(event, event)
    if event in te_map:
        return te_map[event]
    if event in te_map.values():
        return event
    return leg.EVENT_LEGACY_TO_CANONICAL.get(event, event)


def events_probe_names(events, typ: str | None = None) -> list[str]:
    """Flatten events and include legacy aliases for host_data queries.

    Pass ``typ`` (e.g. ``lustre_llite`` / ``llite``) so type-scoped renames apply.
    """
    if not events:
        return []
    probe = (
        (lambda e: event_probe_names_for_type(typ, e))
        if typ
        else event_probe_names
    )
    out: list[str] = []
    seen: set[str] = set()
    for e in events:
        if isinstance(e, (list, tuple)):
            for sub in e:
                for name in probe(str(sub)):
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
        else:
            for name in probe(str(e)):
                if name not in seen:
                    seen.add(name)
                    out.append(name)
    return out


def type_probe_names(typ: str) -> tuple[str, ...]:
    """host_data.type values to try for ORM queries (canonical first)."""
    # Bare / legacy AMD DF → full family probe order (live family types first).
    # Family types stay exact — do not alias rome/milan/… onto historical bare rows.
    if typ in (canon.AMD_DF_TYPE, leg.LEGACY_AMD_DF_TYPE):
        return amd_df_types_probe_order()
    if typ in canon.AMD_DF_STATS_TYPES:
        return (typ,)

    seen: set[str] = set()
    out: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in seen:
            seen.add(name)
            out.append(name)

    add(typ)
    add(leg.TYPE_LEGACY_TO_CANONICAL.get(typ))
    canon_name = canonical_type_name(typ)
    if canon_name != typ:
        add(canon_name)
    for leg_name, mapped in leg.TYPE_LEGACY_TO_CANONICAL.items():
        if mapped == canon_name or mapped == typ:
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
