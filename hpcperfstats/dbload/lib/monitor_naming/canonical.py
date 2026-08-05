"""
Canonical monitor st_name and event keys (current hpcperfstatsd emission).

Attributes:
  AMD_DF_STATS_TYPES: Attribute.
  AMD_DF_TYPE: Attribute.
  AMD_PMC_TYPE: Attribute.
  AMD_RAPL_STATS_TYPES: Attribute.
  APERF: Attribute.
  ARM_DRAM_BW_BYTES: Attribute.
  ARM_EST_FLOPS: Attribute.
  ARM_IMC_STATS_TYPES: Attribute.
  ARM_INT16_OPS: Attribute.
  ARM_INT8_OPS: Attribute.
  CHA_TYPENAME_PRIORITY: Attribute.
  DCGM_CPU_POWER_LIMIT_W: Attribute.
  DCGM_CPU_POWER_UTIL_W: Attribute.
  DRAM_CAS_READS: Attribute.
  DRAM_CAS_WRITES: Attribute.
  DRAM_CHAN_BYTES_EVENTS: Attribute.
  FP_OPS_RETIRED: Attribute.
  GRACE_FP_ARITH_SCALAR_DOUBLE: Attribute.
  GRACE_FP_ARITH_SCALAR_SINGLE: Attribute.
  HBM_CAS_READS: Attribute.
  HBM_CAS_WRITES: Attribute.
  HOST_BLOCK_TYPE: Attribute.
  HOST_CPU_HW_TYPE: Attribute.
  HOST_CPU_TYPE: Attribute.
  HOST_IB_EXT_TYPE: Attribute.
  HOST_IB_TYPE: Attribute.
  HOST_LNET_TYPE: Attribute.
  HOST_MEM_TYPE: Attribute.
  HOST_NET_TYPE: Attribute.
  HOST_NFS_TYPE: Attribute.
  HOST_NUMA_TYPE: Attribute.
  HOST_OPA_TYPE: Attribute.
  HOST_ROOFLINE_PEAK_TYPE: Attribute.
  INSTR_RETIRED: Attribute.
  INTEL_CORE_PMC_TYPES_ORDERED: Attribute.
  INTEL_FP_ARITH_ALL_EVENTS: Attribute.
  INTEL_FP_ARITH_DOUBLE_EVENTS: Attribute.
  INTEL_FP_ARITH_SINGLE_EVENTS: Attribute.
  INTEL_IMC_STATS_TYPES: Attribute.
  INTEL_LEGACY_SSE_FLOP_EVENTS: Attribute.
  INTEL_RAPL_STATS_TYPES: Attribute.
  LUSTRE_LLITE_TYPE: Attribute.
  MEM_FREE: Attribute.
  MEM_TOTAL: Attribute.
  MEM_USED: Attribute.
  MPERF: Attribute.
  PKG_ENERGY: Attribute.
  PMC_TYPENAME_PRIORITY: Attribute.
  _PMC_FREQ_BY_TYPENAME: Attribute.
"""
from __future__ import annotations

# Intel IMC types exposing dram_cas_* (and on SPR also hbm_cas_*) for measured BW.
INTEL_IMC_STATS_TYPES = (
    "intel_x86_uncore_imc_skx",
    "intel_x86_uncore_imc_icx",
    "intel_x86_uncore_imc_spr",
)

ARM_IMC_STATS_TYPES = ("arm_aarch64_imc",)

INTEL_CORE_PMC_TYPES_ORDERED = (
    "intel_x86_pmc_gpr8",
    "intel_x86_pmc_gpr4",
    "host_cpu_hw",
)

AMD_PMC_TYPE = "amd_x86_pmc"
# Historical bare typename (removed from live monitor emit; dual-read only).
AMD_DF_TYPE = "amd_x86_uncore_df"
# Live LIKWID family DF collectors (rome → turin).
AMD_DF_STATS_TYPES = (
    "amd_x86_uncore_df_rome",
    "amd_x86_uncore_df_milan",
    "amd_x86_uncore_df_genoa",
    "amd_x86_uncore_df_turin",
)
INTEL_RAPL_STATS_TYPES = ("intel_x86_rapl",)
AMD_RAPL_STATS_TYPES = ("amd_x86_rapl",)
DRAM_CHAN_BYTES_EVENTS = tuple(f"dram_chan{i}_bytes" for i in range(4))
HOST_CPU_HW_TYPE = "host_cpu_hw"
HOST_ROOFLINE_PEAK_TYPE = "host_roofline_peak"

PMC_TYPENAME_PRIORITY = (
    AMD_PMC_TYPE,
    "intel_x86_pmc_gpr8",
    "intel_x86_pmc_gpr4",
    HOST_CPU_HW_TYPE,
    "intel_x86_uncore_imc_skx",
    "intel_x86_uncore_imc_bdw",
    "intel_x86_uncore_imc_hsw",
    "intel_x86_uncore_imc_ivb",
    "intel_x86_uncore_imc_snb",
)

CHA_TYPENAME_PRIORITY = ("intel_x86_uncore_cha_skx",)

# FP_ARITH events (unchanged monitor mnemonics).
INTEL_FP_ARITH_DOUBLE_EVENTS = (
    "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE",
    "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE",
    "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE",
    "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE",
)
INTEL_FP_ARITH_SINGLE_EVENTS = (
    "FP_ARITH_INST_RETIRED_SCALAR_SINGLE",
    "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE",
    "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE",
    "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE",
)
INTEL_FP_ARITH_ALL_EVENTS = INTEL_FP_ARITH_DOUBLE_EVENTS + INTEL_FP_ARITH_SINGLE_EVENTS

INTEL_LEGACY_SSE_FLOP_EVENTS = (
    ("SSE_DOUBLE_SCALAR", 1),
    ("SSE_DOUBLE_PACKED", 2),
    ("SIMD_DOUBLE_256", 4),
)

# Canonical event names
DRAM_CAS_READS = "dram_cas_reads"
DRAM_CAS_WRITES = "dram_cas_writes"
HBM_CAS_READS = "hbm_cas_reads"
HBM_CAS_WRITES = "hbm_cas_writes"
INSTR_RETIRED = "instr_retired"
APERF = "aperf"
MPERF = "mperf"
FP_OPS_RETIRED = "fp_ops_retired"
PKG_ENERGY = "pkg_energy"
DCGM_CPU_POWER_UTIL_W = "dcgm_cpu_power_util_w"
DCGM_CPU_POWER_LIMIT_W = "dcgm_cpu_power_limit_w"
ARM_EST_FLOPS = "arm_est_flops"
ARM_INT8_OPS = "arm_int8_ops"
ARM_INT16_OPS = "arm_int16_ops"
# Grace host_cpu_hw PAPI scalars (lowercase; Intel FP_ARITH_* stay uppercase).
GRACE_FP_ARITH_SCALAR_DOUBLE = "fp_arith_inst_retired_scalar_double"
GRACE_FP_ARITH_SCALAR_SINGLE = "fp_arith_inst_retired_scalar_single"
ARM_DRAM_BW_BYTES = "ARM_DRAM_BW_BYTES"

MEM_TOTAL = "mem_total"
MEM_USED = "mem_used"
MEM_FREE = "mem_free"

HOST_CPU_TYPE = "host_cpu"
HOST_MEM_TYPE = "host_mem"
HOST_BLOCK_TYPE = "host_block"
HOST_NET_TYPE = "host_net"
HOST_NUMA_TYPE = "host_numa"
HOST_NFS_TYPE = "host_nfs"
HOST_IB_TYPE = "host_ib"
# Retired separate collectors merged into HOST_IB_TYPE (monitor IB driver merge).
HOST_IB_EXT_TYPE = "host_ib_ext"
HOST_LNET_TYPE = "host_lnet"
HOST_OPA_TYPE = "host_opa"
LUSTRE_LLITE_TYPE = "lustre_llite"

# Nominal GHz for APERF/MPERF ratio in avg_freq when typename matches.
_PMC_FREQ_BY_TYPENAME = {
    "intel_snb": 2.7,
    "intel_ivb": 2.8,
    "intel_hsw": 2.3,
    "intel_bdw": 2.6,
    "intel_skx": 2.1,
    "intel_x86_pmc_gpr8": 2.7,
    "intel_x86_pmc_gpr4": 2.7,
    AMD_PMC_TYPE: 2.7,
    HOST_CPU_HW_TYPE: 2.7,
    # Legacy keys still probed via resolve dual-read in utils._pick_pmc_typename
    "intel_8pmc3": 2.7,
    "intel_4pmc3": 2.7,
    "amd64_pmc": 2.7,
    "cpu_counter_metrics": 2.7,
}


def pmc_freq_for_typename(typename: str | None) -> float:
    """
    Pmc freq for typename.
    
    Args:
      typename (str | None): One of ``str``, ``None``.
    
    Returns:
      float: float produced by this call.
    
    Examples:
      >>> pmc_freq_for_typename(None)  # doctest: +SKIP
    """
    if not typename:
        return 2.7
    return _PMC_FREQ_BY_TYPENAME.get(typename, 2.7)
