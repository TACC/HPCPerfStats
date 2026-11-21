"""
Canonical monitor st_name and event keys (current hpcperfstatsd emission).

Attributes:
  AMD_DF_BEAT_EVENTS: AMD DF DRAM beat keys, summed over every row of a
    family DF type: the per-channel ``chan<N>`` rows' ``dram_data_beats``
    (Zen2 / Zen3, read + write) or ``dram_read_beats`` /
    ``dram_write_beats`` (Zen4 / Zen5), and the older single ``df`` row's
    ``dram_chan0_bytes`` .. ``dram_chan3_bytes`` (beats despite the name).
    A row carries one family; the keys it lacks are sentinels.
  AMD_DF_BYTES_PER_BEAT: Bytes per ``AMD_DF_BEAT_EVENTS`` count; the keys
    hold DRAM data beats in every monitor era, never bytes.
  AMD_DF_STATS_TYPES: Attribute.
  AMD_DF_TYPE: Attribute.
  AMD_FP_DP_OPS: AMD Zen5 ``host_cpu_hw`` double-precision FLOP lifetime
    (direct perf, ``E,W=64,U=FLOP``); other CPU classes store a sentinel.
  AMD_PMC_TYPE: Attribute.
  AMD_RAPL_STATS_TYPES: Attribute.
  APERF: Attribute.
  ARM_DRAM_BW_BYTES: Attribute.
  ARM_EST_FLOPS: Attribute.
  ARM_FP_DP_SPEC: Grace ``host_cpu_hw`` FP64 speculative instruction count
    (lifetime, monitor 3.1).
  ARM_FP_HP_SPEC: Grace ``host_cpu_hw`` FP16 speculative instruction count
    (lifetime, monitor 3.1).
  ARM_FP_SP_SPEC: Grace ``host_cpu_hw`` FP32 speculative instruction count
    (lifetime, monitor 3.1).
  ARM_IMC_STATS_TYPES: Attribute.
  ARM_INT16_OPS: Attribute.
  ARM_INT8_OPS: Attribute.
  CHA_TYPENAME_PRIORITY: Attribute.
  DCGM_CPU_POWER_LIMIT_W: Attribute.
  DCGM_CPU_POWER_UTIL_W: Attribute.
  DRAM_CAS_READS: Attribute.
  DRAM_CAS_WRITES: Attribute.
  FP_OPS_RETIRED: AMD ``host_cpu_hw`` total FLOP lifetime (Zen3, Zen4 and
    Zen5; ``E,W=64,U=FLOP``).
  GRACE_FP_ARITH_SCALAR_DOUBLE: Attribute.
  GPU_STATS_TYPES: GPU ``host_data.type`` names (keep hardware ``dev``;
    shared-node stamping skips them).
  GRACE_FP_ARITH_SCALAR_SINGLE: Attribute.
  HBM_CAS_READS: Attribute.
  HBM_CAS_WRITES: Attribute.
  HOST_BLOCK_TYPE: Attribute.
  HOST_CPU_HW_TYPE: Attribute.
  HOST_CPU_TYPE: Attribute.
  HOST_IB_EXT_TYPE: Attribute.
  HOST_IB_TYPE: Attribute.
  HOST_LNET_TYPE: Attribute.
  HOST_MEM_JOB_TYPE: Attribute.
  HOST_MEM_TYPE: Attribute.
  HOST_NET_TYPE: Attribute.
  HOST_NFS_TYPE: Attribute.
  HOST_NUMA_TYPE: Attribute.
  HOST_OPA_TYPE: Attribute.
  HOST_POWER_SOCKET_EVENTS: ``host_power_socket`` keys (all watts).
  HOST_POWER_SOCKET_TYPE: Per-socket power type (``dev`` = socket id).
  HOST_ROOFLINE_PEAK_TYPE: Attribute.
  INSTR_RETIRED: Attribute.
  INTEL_CORE_PMC_TYPES_ORDERED: Attribute.
  INTEL_FP_ARITH_ALL_EVENTS: Attribute.
  INTEL_FP_ARITH_DOUBLE_EVENTS: Attribute.
  INTEL_FP_ARITH_KEYS: Lowercase direct-perf Intel ``host_cpu_hw``
    ``fp_arith_inst_retired_*`` keys (retired instructions), double then
    single precision.
  INTEL_FP_ARITH_SINGLE_EVENTS: Attribute.
  INTEL_GPU_COMPUTE_ENGINE_UTIL: ``intel_gpu`` (PVC) compute engine group
    busy percent, max over tiles (XPU Manager).
  INTEL_IMC_STATS_TYPES: Attribute.
  INTEL_LEGACY_SSE_FLOP_EVENTS: Attribute.
  INTEL_RAPL_STATS_TYPES: Attribute.
  KEEP_DEV_COLLAPSE_TYPES: Types whose ingest collapse keeps ``dev`` (GPU
    types plus ``host_power_socket``).
  LUSTRE_LLITE_TYPE: Attribute.
  MEM_FREE: Attribute.
  MEM_TOTAL: Attribute.
  MEM_USED: Attribute.
  MPERF: Attribute.
  NVIDIA_GPU_INTEGER_ACTIVE: ``nvidia_gpu`` integer pipe busy percent
    (absent on A100).
  NVIDIA_GPU_TENSOR_DMMA_ACTIVE: ``nvidia_gpu`` FP64 tensor-core (DMMA)
    busy percent; NOT_PRESENT on the DCGM path, real on the GPM path.
  PKG_ENERGY: Attribute.
  PMC_TYPENAME_PRIORITY: Attribute.
  PMU_ENABLED_TIME: PMU enabled-time lifetime in ns on ``host_cpu_hw`` (Grace
    and x86) and on the IMC / DF uncore rows (monitor 3.1).
  PMU_RUNNING_TIME: PMU running-time lifetime in ns on ``host_cpu_hw`` (Grace
    and x86) and on the IMC / DF uncore rows (monitor 3.1).
  PMU_SOLO_RUNNING_TIME: ``host_cpu_hw`` (Grace and x86) time the core events
    would run with no other PMU user, in ns (monitor 3.1).
  RETIRED_HOST_CPU_HW_POWER_EVENTS: ``host_cpu_hw`` power keys retired in
    monitor round 2 (history only).
  RETIRED_NVIDIA_GPU_POWER_EVENTS: ``nvidia_gpu`` module/SysIO power keys
    retired in monitor round 2 (history only).
  SOCKET_CPU_POWER_W: ``host_power_socket`` CPU core rail watts.
  SOCKET_MODULE_POWER_W: ``host_power_socket`` GH200 module watts.
  SOCKET_POWER_W: ``host_power_socket`` whole-socket watts.
  SOCKET_SYSIO_POWER_W: ``host_power_socket`` SoC (SysIO) rail watts.
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
# Live per-family DF collectors (rome -> turin).
AMD_DF_STATS_TYPES = (
  "amd_x86_uncore_df_rome",
  "amd_x86_uncore_df_milan",
  "amd_x86_uncore_df_genoa",
  "amd_x86_uncore_df_turin",
)
INTEL_RAPL_STATS_TYPES = ("intel_x86_rapl",)
AMD_RAPL_STATS_TYPES = ("amd_x86_rapl",)
AMD_DF_BEAT_EVENTS = (
  "dram_data_beats",
  "dram_read_beats",
  "dram_write_beats",
  *(f"dram_chan{i}_bytes" for i in range(4)),
)
AMD_DF_BYTES_PER_BEAT = 64
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

# Historical uppercase FP_ARITH mnemonics: the schema U= token carries the
# lanes, so ingest already stored FLOPs.
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
INTEL_FP_ARITH_ALL_EVENTS = (
  INTEL_FP_ARITH_DOUBLE_EVENTS + INTEL_FP_ARITH_SINGLE_EVENTS
)
# Direct-perf Intel host_cpu_hw keys (monitor 3.1 Phases 2+3, E,W=48, no
# schema unit): the same events lowercased, double then single precision in
# the order above. Values are retired instructions, not FLOPs; consumers
# multiply by the lanes per width. On Grace rows the two scalar keys exist
# but never carry data.
INTEL_FP_ARITH_KEYS = tuple(e.lower() for e in INTEL_FP_ARITH_ALL_EVENTS)

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
# AMD host_cpu_hw FLOP lifetimes (direct perf, E,W=64,U=FLOP): total FLOPs
# on Zen3 / Zen4 / Zen5, double-precision FLOPs on Zen5 only.
FP_OPS_RETIRED = "fp_ops_retired"
AMD_FP_DP_OPS = "amd_fp_dp_ops"
PKG_ENERGY = "pkg_energy"
DCGM_CPU_POWER_UTIL_W = "dcgm_cpu_power_util_w"
DCGM_CPU_POWER_LIMIT_W = "dcgm_cpu_power_limit_w"
ARM_EST_FLOPS = "arm_est_flops"
ARM_INT8_OPS = "arm_int8_ops"
ARM_INT16_OPS = "arm_int16_ops"
# Grace host_cpu_hw FP instruction counts by width (monitor 3.1, direct perf
# FP_DP_SPEC / FP_SP_SPEC / FP_HP_SPEC, scaled, E,W=64). Since 3.1
# arm_est_flops is measured FLOPs (FP_FIXED_OPS_SPEC + FP_SCALE_OPS_SPEC).
ARM_FP_DP_SPEC = "arm_fp_dp_spec"
ARM_FP_SP_SPEC = "arm_fp_sp_spec"
ARM_FP_HP_SPEC = "arm_fp_hp_spec"
# PMU sharing counter-time lifetimes in ns (monitor 3.1, E,W=64,U=ns) on
# Grace and x86. host_cpu_hw carries all three; arm_aarch64_imc,
# intel_x86_uncore_imc_* and amd_x86_uncore_df_* rows the first two.
PMU_ENABLED_TIME = "pmu_enabled_time"
PMU_RUNNING_TIME = "pmu_running_time"
PMU_SOLO_RUNNING_TIME = "pmu_solo_running_time"
# Grace host_cpu_hw scalar FP_ARITH keys (always 0 there); the same names
# carry real counts on Intel rows (INTEL_FP_ARITH_KEYS).
GRACE_FP_ARITH_SCALAR_DOUBLE = "fp_arith_inst_retired_scalar_double"
GRACE_FP_ARITH_SCALAR_SINGLE = "fp_arith_inst_retired_scalar_single"
# Monitor KEY (cpu_counter_metrics.h); uppercase form is legacy-only dual-read.
ARM_DRAM_BW_BYTES = "arm_dram_bw_bytes"

MEM_TOTAL = "mem_total"
MEM_USED = "mem_used"
MEM_FREE = "mem_free"

HOST_CPU_TYPE = "host_cpu"
# Per-socket power (monitor round 2, D31/D32): one row per CPU socket,
# ``dev`` = socket id, unit W. Node-wide data (D38): never ``j:`` stamped.
HOST_POWER_SOCKET_TYPE = "host_power_socket"
SOCKET_CPU_POWER_W = "cpu_power_w"
SOCKET_POWER_W = "socket_power_w"
SOCKET_SYSIO_POWER_W = "sysio_power_w"
SOCKET_MODULE_POWER_W = "module_power_w"
HOST_POWER_SOCKET_EVENTS = (
  SOCKET_CPU_POWER_W,
  SOCKET_POWER_W,
  SOCKET_SYSIO_POWER_W,
  SOCKET_MODULE_POWER_W,
)
# Retired monitor power keys (D33/D36): no longer emitted, still in archive
# history; analysis dual-reads them for old rows only.
RETIRED_HOST_CPU_HW_POWER_EVENTS = (
  DCGM_CPU_POWER_UTIL_W,
  DCGM_CPU_POWER_LIMIT_W,
)
RETIRED_NVIDIA_GPU_POWER_EVENTS = ("module_power_usage", "sysio_power_usage")
# nvidia_gpu pipe busy ratios (percent). integer_active is absent on A100;
# tensor_dmma_active (FP64 tensor core) is NOT_PRESENT on the DCGM path and
# real on the GPM path (Hopper / Blackwell, monitor 3.1).
NVIDIA_GPU_INTEGER_ACTIVE = "integer_active"
NVIDIA_GPU_TENSOR_DMMA_ACTIVE = "tensor_dmma_active"
# intel_gpu (PVC) compute engine group busy percent, max over tiles. The PVC
# busy signal that works while the EU metrics (sm_active) are NOT_PRESENT on
# GFX firmware below the XPU Manager minimum.
INTEL_GPU_COMPUTE_ENGINE_UTIL = "compute_engine_util"
# GPU stats types keep their hardware ``dev`` in ingest collapse, shared-node
# stamping and the per-core repair. Only Python copy (D34); the Codon compile
# unit mirrors it as a literal (``codon/feed_collapse.py`` ``GPU_TYPES``),
# locked by ``hpcperfstats/tests/test_keep_dev_type_set_parity.py``.
GPU_STATS_TYPES = frozenset({"nvidia_gpu", "amd_gpu", "intel_gpu"})
# Types whose ingest collapse keeps ``dev`` (per-device rows in host_data):
# GPU types plus ``host_power_socket``; consumers sum them by ``dev``.
# Shared-node stamping still keys on ``GPU_STATS_TYPES`` only (D38). Codon
# mirror: ``feed_collapse.KEEP_DEV_TYPES`` (same parity test).
KEEP_DEV_COLLAPSE_TYPES = GPU_STATS_TYPES | frozenset({HOST_POWER_SOCKET_TYPE})
HOST_MEM_TYPE = "host_mem"
# Job-scoped cgroup memory (shared-node); not NUMA host_mem dual-read.
HOST_MEM_JOB_TYPE = "host_mem_job"
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
