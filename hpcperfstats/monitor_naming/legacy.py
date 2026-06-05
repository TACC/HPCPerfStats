"""Legacy monitor st_name and event keys (historical host_data and old archive ingest)."""
from __future__ import annotations

from hpcperfstats.monitor_naming.load_map import (
    event_renames,
    legacy_type_names,
    load_monitor_rename_map,
    type_renames,
)

# Ingest-only typename: dbload used to normalize intel_knl_mc -> intel_knl_mc_dclk.
INGEST_LEGACY_KNL_IMC_TYPE = "intel_knl_mc_dclk"
MONITOR_LEGACY_KNL_IMC_TYPE = "intel_knl_mc"

TYPE_LEGACY_TO_CANONICAL: dict[str, str] = dict(type_renames())
# KNL IMC retired from canonical emission; identity mapping for historical host_data.
TYPE_LEGACY_TO_CANONICAL[INGEST_LEGACY_KNL_IMC_TYPE] = INGEST_LEGACY_KNL_IMC_TYPE
TYPE_LEGACY_TO_CANONICAL[MONITOR_LEGACY_KNL_IMC_TYPE] = MONITOR_LEGACY_KNL_IMC_TYPE

LEGACY_TYPENAMES: frozenset[str] = frozenset(TYPE_LEGACY_TO_CANONICAL.keys())

EVENT_LEGACY_TO_CANONICAL: dict[str, str] = dict(event_renames())

# Intel IMC types in legacy DB / archives (probe order matches old utils.py).
LEGACY_INTEL_IMC_STATS_TYPES = (
    "intel_snb_imc",
    "intel_ivb_imc",
    "intel_hsw_imc",
    "intel_bdw_imc",
    INGEST_LEGACY_KNL_IMC_TYPE,
    MONITOR_LEGACY_KNL_IMC_TYPE,
    "intel_skx_imc",
)

LEGACY_ARM_IMC_STATS_TYPES = ("arm_imc",)

LEGACY_INTEL_CORE_PMC_TYPES_ORDERED = (
    "intel_8pmc3",
    "intel_4pmc3",
    "cpu_counter_metrics",
)

LEGACY_PMC_TYPENAME_PRIORITY = (
    "amd64_pmc",
    "intel_8pmc3",
    "intel_4pmc3",
    "cpu_counter_metrics",
    "intel_skx",
    "intel_knl",
    "intel_bdw",
    "intel_hsw",
    "intel_ivb",
    "intel_snb",
)

LEGACY_CHA_TYPENAME_PRIORITY = ("intel_skx_cha", "intel_knl_cha")

LEGACY_AMD_PMC_TYPE = "amd64_pmc"
LEGACY_AMD_DF_TYPE = "amd64_df"
LEGACY_HOST_CPU_HW_TYPE = "cpu_counter_metrics"
LEGACY_HOST_ROOFLINE_PEAK_TYPE = "roofline_hw_peak"

# Host / FSIO legacy typenames
LEGACY_HOST_CPU_TYPE = "cpu"
LEGACY_HOST_MEM_TYPE = "mem"
LEGACY_HOST_BLOCK_TYPE = "block"
LEGACY_HOST_NET_TYPE = "net"
LEGACY_HOST_NUMA_TYPE = "numa"
LEGACY_HOST_NFS_TYPE = "nfs"
LEGACY_HOST_IB_EXT_TYPE = "ib_ext"
LEGACY_HOST_IB_SW_TYPE = "ib_sw"
LEGACY_HOST_LNET_TYPE = "lnet"
LEGACY_HOST_OPA_TYPE = "opa"
LEGACY_LUSTRE_LLITE_TYPE = "llite"

# DRAM CAS legacy events
LEGACY_DRAM_CAS_READS = "CAS_READS"
LEGACY_DRAM_CAS_WRITES = "CAS_WRITES"

LEGACY_INSTR_RETIRED = "INST_RETIRED"
LEGACY_APERF = "APERF"
LEGACY_MPERF = "MPERF"
LEGACY_FP_OPS_RETIRED = "FLOPS"
LEGACY_PKG_ENERGY = "MSR_PKG_ENERGY_STATUS"

LEGACY_DCGM_CPU_POWER_UTIL_W = "DCGM_CPU_POWER_UTIL_W"
LEGACY_DCGM_CPU_POWER_LIMIT_W = "DCGM_CPU_POWER_LIMIT_W"

LEGACY_ARM_EST_FLOPS = "ARM_EST_FLOPS"
LEGACY_ARM_DRAM_BW_BYTES = "ARM_DRAM_BW_BYTES"

# Host mem legacy proc field names (kernel-style)
LEGACY_MEM_TOTAL = "MemTotal"
LEGACY_MEM_USED = "MemUsed"
LEGACY_MEM_FREE = "MemFree"

_REMOVED = load_monitor_rename_map().get("removed_legacy") or []
REMOVED_LEGACY_SYMBOLS: frozenset[str] = frozenset(str(x) for x in _REMOVED)

# st_name values that used hex eventmaps + CTL/CTR decode in sync_timedb_parsing_legacy.
LEGACY_HARDWARE_DECODE_TYPES: frozenset[str] = frozenset({
    "amd64_pmc",
    "amd64_df",
    "intel_8pmc3",
    "intel_4pmc3",
    "intel_snb_imc",
    "intel_ivb_imc",
    "intel_hsw_imc",
    "intel_bdw_imc",
    INGEST_LEGACY_KNL_IMC_TYPE,
    MONITOR_LEGACY_KNL_IMC_TYPE,
    "intel_skx_imc",
})
