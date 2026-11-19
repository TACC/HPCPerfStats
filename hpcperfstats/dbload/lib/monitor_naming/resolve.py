"""
Dual-read helpers: canonical names first, then legacy (historical host_data).
"""

from __future__ import annotations

from typing import Any

from hpcperfstats.dbload.lib.monitor_naming import (
  canonical as canon,
  legacy as leg,
)


def _names_canonical_then_legacy(
  canonical: str,
  legacy: str,
) -> tuple[str, ...]:
  """
  Internal helper to handle names canonical then legacy.

  Args:
    canonical (str): String for canonical.
    legacy (str): String for legacy.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> _names_canonical_then_legacy("x", "x")  # doctest: +SKIP
  """
  if canonical == legacy:
    return (canonical,)
  return (canonical, legacy)


def _ordered_unique(*groups: tuple[str, ...]) -> tuple[str, ...]:
  """
  Concatenate name tuples in order, keeping the first occurrence of each name.

  Args:
    *groups (tuple[str, ...]): Name tuples in probe order (canonical first).

  Returns:
    tuple[str, ...]: De-duplicated names in first-seen order.

  Examples:
    >>> _ordered_unique(("a", "b"), ("b", "c"))
    ('a', 'b', 'c')
  """
  return tuple(dict.fromkeys(name for group in groups for name in group))


def imc_types_probe_order() -> tuple[str, ...]:
  """
  Intel IMC typenames to try (canonical first, then legacy aliases).

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> imc_types_probe_order()  # doctest: +SKIP
  """
  return _ordered_unique(
    canon.INTEL_IMC_STATS_TYPES,
    leg.LEGACY_INTEL_IMC_STATS_TYPES,
  )


def arm_imc_types_probe_order() -> tuple[str, ...]:
  """
  Arm imc types probe order.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> arm_imc_types_probe_order()  # doctest: +SKIP
  """
  return _ordered_unique(
    canon.ARM_IMC_STATS_TYPES,
    leg.LEGACY_ARM_IMC_STATS_TYPES,
  )


def dram_cas_event_names() -> tuple[str, ...]:
  """
  Dram cas event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> dram_cas_event_names()  # doctest: +SKIP
  """
  return _ordered_unique(
    (
      canon.DRAM_CAS_READS,
      leg.LEGACY_DRAM_CAS_READS,
      canon.DRAM_CAS_WRITES,
      leg.LEGACY_DRAM_CAS_WRITES,
    ),
  )


def dram_cas_read_write_pairs() -> tuple[tuple[str, str], ...]:
  """
  (reads_event, writes_event) pairs to try: canonical pair first, then legacy.

  Returns:
    tuple[tuple[str, str], ...]: tuple[tuple[str, str], ...] produced by
    this call.

  Examples:
    >>> dram_cas_read_write_pairs()  # doctest: +SKIP
  """
  return (
    (canon.DRAM_CAS_READS, canon.DRAM_CAS_WRITES),
    (leg.LEGACY_DRAM_CAS_READS, leg.LEGACY_DRAM_CAS_WRITES),
  )


def hbm_cas_read_write_pairs() -> tuple[tuple[str, str], ...]:
  """
  (reads_event, writes_event) for SPR HBM CAS (canonical only; no legacy.

    aliases).

  Returns:
    tuple[tuple[str, str], ...]: tuple[tuple[str, str], ...] produced by
    this call.

  Examples:
    >>> hbm_cas_read_write_pairs()  # doctest: +SKIP
  """
  return ((canon.HBM_CAS_READS, canon.HBM_CAS_WRITES),)


def instr_retired_event_names() -> tuple[str, ...]:
  """
  Instr retired event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> instr_retired_event_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.INSTR_RETIRED, leg.LEGACY_INSTR_RETIRED
  )


def aperf_event_names() -> tuple[str, ...]:
  """
  Aperf event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> aperf_event_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(canon.APERF, leg.LEGACY_APERF)


def mperf_event_names() -> tuple[str, ...]:
  """
  Mperf event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> mperf_event_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(canon.MPERF, leg.LEGACY_MPERF)


def fp_ops_retired_event_names() -> tuple[str, ...]:
  """
  Fp ops retired event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> fp_ops_retired_event_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.FP_OPS_RETIRED, leg.LEGACY_FP_OPS_RETIRED
  )


def pkg_energy_event_names() -> tuple[str, ...]:
  """
  Pkg energy event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> pkg_energy_event_names()  # doctest: +SKIP
  """
  return (
    canon.PKG_ENERGY,
    leg.LEGACY_PKG_ENERGY,
    "MSR_PKG_ENERGY_STAT",
  )


def core_pmc_types_probe_order() -> tuple[str, ...]:
  """
  Core pmc types probe order.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> core_pmc_types_probe_order()  # doctest: +SKIP
  """
  return _ordered_unique(
    canon.INTEL_CORE_PMC_TYPES_ORDERED,
    leg.LEGACY_INTEL_CORE_PMC_TYPES_ORDERED,
  )


def pmc_typename_priority() -> tuple[str, ...]:
  """
  Pmc typename priority.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> pmc_typename_priority()  # doctest: +SKIP
  """
  return _ordered_unique(
    canon.PMC_TYPENAME_PRIORITY,
    leg.LEGACY_PMC_TYPENAME_PRIORITY,
  )


def cha_typename_priority() -> tuple[str, ...]:
  """
  Cha typename priority.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> cha_typename_priority()  # doctest: +SKIP
  """
  return _ordered_unique(
    canon.CHA_TYPENAME_PRIORITY,
    leg.LEGACY_CHA_TYPENAME_PRIORITY,
  )


def amd_pmc_type_names() -> tuple[str, ...]:
  """
  Amd pmc type names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> amd_pmc_type_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.AMD_PMC_TYPE, leg.LEGACY_AMD_PMC_TYPE
  )


def amd_df_types_probe_order() -> tuple[str, ...]:
  """
  AMD DF typenames: live family first, then historical bare / amd64_df.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> amd_df_types_probe_order()  # doctest: +SKIP
  """
  return _ordered_unique(
    canon.AMD_DF_STATS_TYPES,
    (canon.AMD_DF_TYPE, leg.LEGACY_AMD_DF_TYPE),
  )


def amd_df_type_names() -> tuple[str, ...]:
  """
  Amd DataFrame type names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> amd_df_type_names()  # doctest: +SKIP
  """
  return amd_df_types_probe_order()


def amd_df_bw_event_conv_tries() -> tuple[tuple[tuple[str, ...], float], ...]:
  """
  (events, conv) for AMD DF BW: live beat counters first, then historical MBW.

  ``dram_chanN_bytes`` count DRAM beats of ``canon.AMD_DF_BYTES_PER_BEAT``
  bytes each; ``MBW_CHANNEL_*`` keep their own factor.

  Returns:
    tuple[tuple[tuple[str, ...], float], ...]: tuple[tuple[tuple[str, ...],
    float], ...] produced by this call.

  Examples:
    >>> amd_df_bw_event_conv_tries()  # doctest: +SKIP
  """
  return (
    (canon.DRAM_CHAN_BYTES_EVENTS, canon.AMD_DF_BYTES_PER_BEAT / (1024**3)),
    (leg.LEGACY_AMD_DF_MBW_CHANNEL_EVENTS, 2.0 / (1024**3)),
  )


def rapl_types_probe_order() -> tuple[str, ...]:
  """
  Rapl types probe order.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> rapl_types_probe_order()  # doctest: +SKIP
  """
  return _ordered_unique(
    canon.INTEL_RAPL_STATS_TYPES + canon.AMD_RAPL_STATS_TYPES,
    leg.LEGACY_INTEL_RAPL_STATS_TYPES + leg.LEGACY_AMD_RAPL_STATS_TYPES,
  )


def host_cpu_hw_type_names() -> tuple[str, ...]:
  """
  Host cpu hw type names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> host_cpu_hw_type_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.HOST_CPU_HW_TYPE, leg.LEGACY_HOST_CPU_HW_TYPE
  )


def host_roofline_peak_type_names() -> tuple[str, ...]:
  """
  Host roofline peak type names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> host_roofline_peak_type_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.HOST_ROOFLINE_PEAK_TYPE,
    leg.LEGACY_HOST_ROOFLINE_PEAK_TYPE,
  )


def host_mem_type_names() -> tuple[str, ...]:
  """
  Host mem type names (NUMA ``host_mem`` / legacy ``mem`` only).

  Does **not** include ``host_mem_job`` (job-scoped cgroup series).

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> host_mem_type_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.HOST_MEM_TYPE, leg.LEGACY_HOST_MEM_TYPE
  )


def host_mem_job_type_names() -> tuple[str, ...]:
  """
  Job-scoped cgroup memory type names (``host_mem_job`` only).

  Returns:
    tuple[str, ...]: Canonical type tuple for future readers.

  Examples:
    >>> host_mem_job_type_names()
    ('host_mem_job',)
  """
  return (canon.HOST_MEM_JOB_TYPE,)


def host_cpu_type_names() -> tuple[str, ...]:
  """
  Host cpu type names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> host_cpu_type_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.HOST_CPU_TYPE, leg.LEGACY_HOST_CPU_TYPE
  )


def arm_est_flops_event_names() -> tuple[str, ...]:
  """
  Arm est flops event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> arm_est_flops_event_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.ARM_EST_FLOPS, leg.LEGACY_ARM_EST_FLOPS
  )


def arm_dram_bw_event_names() -> tuple[str, ...]:
  """
  ARM/DCGM synthetic DRAM byte traffic event names (canonical then legacy).

  Live monitor KEY is ``arm_dram_bw_bytes``; historical analysis strings used
  ``ARM_DRAM_BW_BYTES``.

  Returns:
    tuple[str, ...]: Probe order for ``host_data.event``.

  Examples:
    >>> arm_dram_bw_event_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.ARM_DRAM_BW_BYTES, leg.LEGACY_ARM_DRAM_BW_BYTES
  )


def arm_int8_ops_event_names() -> tuple[str, ...]:
  """
  Arm int8 ops event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> arm_int8_ops_event_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.ARM_INT8_OPS, leg.LEGACY_ARM_INT8_OPS
  )


def arm_int16_ops_event_names() -> tuple[str, ...]:
  """
  Arm int16 ops event names.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> arm_int16_ops_event_names()  # doctest: +SKIP
  """
  return _names_canonical_then_legacy(
    canon.ARM_INT16_OPS, leg.LEGACY_ARM_INT16_OPS
  )


def arm_fp_dp_spec_event_names() -> tuple[str, ...]:
  """
  Grace ``host_cpu_hw`` FP64 FP instruction count event (monitor 3.1).

  ``arm_fp_dp_spec`` is a new canonical key with no legacy alias, so the probe
  list holds the canonical name only.

  Returns:
    tuple[str, ...]: ``host_data.event`` probe order (one name).

  Examples:
    >>> arm_fp_dp_spec_event_names()
    ('arm_fp_dp_spec',)
  """
  return (canon.ARM_FP_DP_SPEC,)


def arm_fp_sp_spec_event_names() -> tuple[str, ...]:
  """
  Grace ``host_cpu_hw`` FP32 FP instruction count event (monitor 3.1).

  ``arm_fp_sp_spec`` is a new canonical key with no legacy alias, so the probe
  list holds the canonical name only.

  Returns:
    tuple[str, ...]: ``host_data.event`` probe order (one name).

  Examples:
    >>> arm_fp_sp_spec_event_names()
    ('arm_fp_sp_spec',)
  """
  return (canon.ARM_FP_SP_SPEC,)


def arm_fp_hp_spec_event_names() -> tuple[str, ...]:
  """
  Grace ``host_cpu_hw`` FP16 FP instruction count event (monitor 3.1).

  ``arm_fp_hp_spec`` is a new canonical key with no legacy alias, so the probe
  list holds the canonical name only.

  Returns:
    tuple[str, ...]: ``host_data.event`` probe order (one name).

  Examples:
    >>> arm_fp_hp_spec_event_names()
    ('arm_fp_hp_spec',)
  """
  return (canon.ARM_FP_HP_SPEC,)


def pmu_sharing_event_names() -> tuple[str, str, str]:
  """
  Grace PMU sharing counter-time events (monitor 3.1, Phase 1b D6).

  New canonical keys with no legacy alias, in the order (enabled, running,
  solo). Ingest sums ``host_cpu_hw`` rows across CPUs, so a host's running
  share is d(running) / d(enabled) and the share lost to other PMU users is
  1 - d(running) / d(solo).

  Returns:
    tuple[str, str, str]: ``host_data.event`` names for enabled, running and
    solo-expected counter time.

  Examples:
    >>> pmu_sharing_event_names()
    ('pmu_enabled_time', 'pmu_running_time', 'pmu_solo_running_time')
  """
  return (
    canon.PMU_ENABLED_TIME,
    canon.PMU_RUNNING_TIME,
    canon.PMU_SOLO_RUNNING_TIME,
  )


def grace_fp_scalar_double_event_names() -> tuple[str, ...]:
  """
  Grace host_cpu_hw scalar FP64 event (canonical only; no Intel uppercase).

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> grace_fp_scalar_double_event_names()  # doctest: +SKIP
  """
  return (canon.GRACE_FP_ARITH_SCALAR_DOUBLE,)


def grace_fp_scalar_single_event_names() -> tuple[str, ...]:
  """
  Grace host_cpu_hw scalar FP32 event (canonical only; no Intel uppercase).

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> grace_fp_scalar_single_event_names()  # doctest: +SKIP
  """
  return (canon.GRACE_FP_ARITH_SCALAR_SINGLE,)


def canonical_type_name(typ: str) -> str:
  """
  Map legacy st_name to canonical for peak tables and docs.

  Args:
    typ (str): String for typ.

  Returns:
    str: str produced by this call.

  Examples:
    >>> canonical_type_name("x")  # doctest: +SKIP
  """
  return leg.TYPE_LEGACY_TO_CANONICAL.get(typ, typ)


def _type_scoped_event_map(typ: str | None) -> dict[str, str]:
  """
  Legacy->canonical event map for ``typ``, or empty when type has no.

    type_events.

  Args:
    typ (str | None): One of ``str``, ``None``.

  Returns:
    dict[str, str]: dict[str, str] produced by this call.

  Examples:
    >>> _type_scoped_event_map(None)  # doctest: +SKIP
  """
  if not typ:
    return {}
  canon_typ = canonical_type_name(typ)
  mapping = leg.TYPE_EVENT_LEGACY_TO_CANONICAL.get(canon_typ)
  if mapping:
    return mapping
  return leg.TYPE_EVENT_LEGACY_TO_CANONICAL.get(typ) or {}


def event_probe_names(event: str) -> tuple[str, ...]:
  """
  host_data.event values to try (canonical first) using the **global** events.

    map.

  For type-scoped renames (llite ``open`` / ``read_bytes``), use
  ``event_probe_names_for_type`` so global ``open`` is not rewritten.

  Args:
    event (str): String for event.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> event_probe_names("x")  # doctest: +SKIP
  """
  seen: set[str] = set()
  out: list[str] = []

  def add(name: str | None) -> None:
    """
    Add an entry to this collection.

    Args:
      name (str | None): One of ``str``, ``None``.

    Returns:
      None

    Examples:
      >>> add(None)  # doctest: +SKIP
    """
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
  """
  host_data.event probes for a typename (type_events when present, else.

    global).

  Args:
    typ (str): String for typ.
    event (str): String for event.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> event_probe_names_for_type("x", "x")  # doctest: +SKIP
  """
  te_map = _type_scoped_event_map(typ)
  if not te_map:
    return event_probe_names(event)

  seen: set[str] = set()
  out: list[str] = []

  def add(name: str | None) -> None:
    """
    Add an entry to this collection.

    Args:
      name (str | None): One of ``str``, ``None``.

    Returns:
      None

    Examples:
      >>> add(None)  # doctest: +SKIP
    """
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
  """
  Map a stored host_data.event to its canonical name for ``typ`` when known.

  Args:
    typ (str): String for typ.
    event (str): String for event.

  Returns:
    str: str produced by this call.

  Examples:
    >>> canonical_event_name_for_type("x", "x")  # doctest: +SKIP
  """
  te_map = _type_scoped_event_map(typ)
  if not te_map:
    return leg.EVENT_LEGACY_TO_CANONICAL.get(event, event)
  if event in te_map:
    return te_map[event]
  if event in te_map.values():
    return event
  return leg.EVENT_LEGACY_TO_CANONICAL.get(event, event)


def events_probe_names(events: Any, typ: str | None = None) -> list[str]:
  """
  Flatten events and include legacy aliases for host_data queries.

  Pass ``typ`` (e.g. ``lustre_llite`` / ``llite``) so type-scoped renames
    apply.

  Args:
    events (Any): Events passed to this helper.
    typ (str | None): One of ``str``, ``None``.

  Returns:
    list[str]: list[str] produced by this call.

  Examples:
    >>> events_probe_names(None, None)  # doctest: +SKIP
  """
  if not events:
    return []
  probe = (
    (lambda e: event_probe_names_for_type(typ, e)) if typ else event_probe_names
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
  """
  host_data.type values to try for ORM queries (canonical first).

  Args:
    typ (str): String for typ.

  Returns:
    tuple[str, ...]: tuple[str, ...] produced by this call.

  Examples:
    >>> type_probe_names("x")  # doctest: +SKIP
  """
  # Bare / legacy AMD DF -> full family probe order (live family types first).
  # Family types stay exact - do not alias rome/milan/... onto historical bare rows.
  if typ in (canon.AMD_DF_TYPE, leg.LEGACY_AMD_DF_TYPE):
    return amd_df_types_probe_order()
  if typ in canon.AMD_DF_STATS_TYPES:
    return (typ,)

  seen: set[str] = set()
  out: list[str] = []

  def add(name: str | None) -> None:
    """
    Add an entry to this collection.

    Args:
      name (str | None): One of ``str``, ``None``.

    Returns:
      None

    Examples:
      >>> add(None)  # doctest: +SKIP
    """
    if name and name not in seen:
      seen.add(name)
      out.append(name)

  add(typ)
  add(leg.TYPE_LEGACY_TO_CANONICAL.get(typ))
  canon_name = canonical_type_name(typ)
  if canon_name != typ:
    add(canon_name)
  for leg_name, mapped in leg.TYPE_LEGACY_TO_CANONICAL.items():
    if mapped in (canon_name, typ):
      add(leg_name)
  return tuple(out)


def resolve_get_type(u: Any, type_names: tuple[str, ...]) -> Any:
  """
  First matching (schema, stats, typename) from utils.get_type candidates.

  Args:
    u (Any): U passed to this helper.
    type_names (tuple[str, ...]): Sequence for type names.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> resolve_get_type(None, [])  # doctest: +SKIP
  """
  for typ in type_names:
    schema, stats = u.get_type(typ)
    if schema is not None:
      return schema, stats, typ
  return None, {}, None


def schema_needs_legacy_hardware_decode(
  typ: str,
  schema_events: list[str],
) -> bool:
  """
  True when stats line must use CTL/CTR hex decode (legacy archives).

  Args:
    typ (str): String for typ.
    schema_events (list[str]): Sequence for schema events.

  Returns:
    bool: True or False for this check.

  Examples:
    >>> schema_needs_legacy_hardware_decode("x", [])  # doctest: +SKIP
  """
  for token in schema_events:
    base = token.split(",")[0]
    if base in leg.REMOVED_LEGACY_SYMBOLS:
      return True
    if "CTL" in base or "CTR" in base or "FIXED_CTR" in base:
      return True
  return typ in leg.LEGACY_HARDWARE_DECODE_TYPES
