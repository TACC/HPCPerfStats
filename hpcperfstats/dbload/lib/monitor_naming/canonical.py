"""Canonical monitor st_name and event keys (current hpcperfstatsd emission)."""
from __future__ import annotations

# Intel IMC types exposing dram_cas_reads / dram_cas_writes (roofline, mbw).
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
AMD_DF_TYPE = "amd_x86_uncore_df"
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
    if not typename:
        return 2.7
    return _PMC_FREQ_BY_TYPENAME.get(typename, 2.7)
