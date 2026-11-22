"""Synthetic monitor messages (listend archive format) for pipeline E2E.

Emits canonical typenames/events (post monitor naming scheme) for
``Metrics.compute_metrics`` to populate the full job metric catalog.
"""
from __future__ import annotations

_LLITE_EVENTS = [
    "vfs_open_ops", "vfs_close_ops", "vfs_mmap_ops", "vfs_fsync_ops",
    "vfs_setattr_ops", "vfs_truncate_ops", "vfs_flock_ops", "vfs_getattr_ops",
    "vfs_statfs_ops", "vfs_alloc_inode_ops", "vfs_setxattr_ops", "vfs_listxattr_ops",
    "vfs_removexattr_ops", "vfs_readdir_ops", "vfs_create_ops", "vfs_lookup_ops",
    "vfs_link_ops", "vfs_unlink_ops", "vfs_symlink_ops", "vfs_mkdir_ops",
    "vfs_rmdir_ops", "vfs_mknod_ops", "vfs_rename_ops",
    "vfs_read_bytes", "vfs_write_bytes",
]

_LLITE_CAPACITY_EVENTS = [
    ("fs_bytes_total", "U=B"),
    ("fs_bytes_free", "U=B"),
    ("fs_bytes_avail", "U=B"),
    ("fs_files_total", ""),
    ("fs_files_free", ""),
]

_INTEL_GPR8_EVENTS = [
    "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,E,W=48,U=1",
    "FP_ARITH_INST_RETIRED_SCALAR_SINGLE,E,W=48,U=1",
    "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,E,W=48,U=2",
    "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,E,W=48,U=4",
    "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,E,W=48,U=4",
    "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,E,W=48,U=8",
    "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,E,W=48,U=8",
    "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,E,W=48,U=16",
    "instr_retired,E,W=48",
    "aperf,E,W=48",
    "mperf,E,W=48",
]


def monitor_schema_header(fqdn: str) -> str:
  """``$`` schema block: software counters + Intel PMC/IMC + GPU + fabric types."""
  llite_schema = " ".join("%s,E" % e for e in _LLITE_EVENTS)
  llite_cap = " ".join(
      ("%s,%s" % (name, flags) if flags else name)
      for name, flags in _LLITE_CAPACITY_EVENTS
  )
  llite_schema = "%s %s" % (llite_schema, llite_cap)
  intel_pmc_schema = " ".join(_INTEL_GPR8_EVENTS)
  return """$tacc_stats 2.3.5
$hostname {fqdn}
$uname Linux x86_64 5.14.0 test
$uptime 1
!host_block rd_sectors,E,U=512B wr_sectors,E,U=512B
!host_cpu user,E,U=cs nice,E,U=cs system,E,U=cs idle,E,U=cs iowait,E,U=cs irq,E,U=cs softirq,E,U=cs
!host_mem mem_total,U=KB mem_used,U=KB slab,U=KB file_pages,U=KB
!host_net rx_bytes,E,U=B tx_bytes,E,U=B rx_packets,E tx_packets,E
!host_numa numa_hit,E numa_miss,E numa_foreign,E interleave_hit,E local_node,E other_node,E
!lustre_llite {llite}
!host_ib port_xmit_data,E port_rcv_data,E port_xmit_packets,E port_rcv_packets,E
!host_lnet tx_bytes,E,U=B rx_bytes,E,U=B
!host_opa PortXmitData,E PortRcvData,E PortXmitPkts,E PortRcvPkts,E PortXmitWait,E SwPortCongestion,E PortRcvFECN,E PortRcvBECN,E
!host_nfs read_ops,E write_ops,E
!nvidia_gpu gpu_util,E tensor_active,E tensor_imma_active,E tensor_hmma_active,E tensor_dfma_active,E fp16_active,E fp32_active,E fp64_active,E integer_active,E tensor_dmma_active,E gpu_mem_bw_bytes_rate,E power_usage,E gpu_io_link_total_bytes,E clocks_event_reasons,E gpu_count,E gpu_mem_util,E,R=S gpu_mem_used_mb,E,R=S
!amd_gpu gpu_util,E tensor_active,E gpu_mem_bw_bytes_rate,E power_usage,E gpu_count,E
!intel_x86_pmc_gpr8 {intel_pmc_schema}
!intel_x86_uncore_imc_skx dram_cas_reads,E dram_cas_writes,E
!amd_x86_pmc fp_ops_retired,E fp_ops_merge,E instr_retired,E aperf,E mperf,E
!amd_x86_uncore_df MBW_CHANNEL_0,E MBW_CHANNEL_1,E MBW_CHANNEL_2,E MBW_CHANNEL_3,E MBW_CHANNEL_4,E MBW_CHANNEL_5,E MBW_CHANNEL_6,E MBW_CHANNEL_7,E
!arm_aarch64_imc dram_cas_reads,E dram_cas_writes,E
!host_cpu_hw pmu_enabled_time,E,W=64,U=ns pmu_running_time,E,W=64,U=ns pmu_solo_running_time,E,W=64,U=ns
!intel_x86_rapl pkg_energy,E,W=32,U=mJ
!host_power_socket cpu_power_w,U=W socket_power_w,U=W sysio_power_w,U=W module_power_w,U=W
""".format(
      fqdn=fqdn,
      llite=llite_schema,
      intel_pmc_schema=intel_pmc_schema,
  )


def _block_line(scale: int) -> str:
  b = 100000 + scale * 8000
  w = 90000 + scale * 7000
  return "host_block sda %d %d\n" % (b, w)


def _cpu_lines(scale: int) -> str:
  u0 = 1782420 + scale
  u1 = 2000652 + scale
  return (
      "host_cpu 60 %d 573 1054362 169625696 11810 75091 69629\n"
      "host_cpu 61 %d 403 1069570 169394113 12687 75144 70234\n"
  ) % (u0, u1)


def _mem_line(scale: int) -> str:
  return "host_mem global 128000 %d 1200 8000\n" % (64000 + scale * 100)


def _net_line(scale: int) -> str:
  b = int(1e9) + scale * 100000
  p = int(2e6) + scale * 1000
  return "host_net eth0 %d %d %d %d\n" % (b, b, p, p)


def _numa_line(scale: int) -> str:
  return "host_numa node0 %d %d %d 10 %d %d\n" % (
      100000 + scale * 500,
      50 + scale,
      40 + scale,
      8000 + scale * 10,
      200 + scale * 5,
  )


def _llite_line(scale: int) -> str:
  vals = [str(500 + i * 10 + scale) for i in range(len(_LLITE_EVENTS))]
  # Capacity gauges (sysfs): large fixed totals with free/avail slightly below.
  cap = [
      str(10**12),
      str(10**12 - 10**9 - scale),
      str(10**12 - 2 * 10**9 - scale),
      str(10**8),
      str(10**8 - 1000 - scale),
  ]
  return "lustre_llite scratch %s\n" % " ".join(vals + cap)


def _ib_line(scale: int) -> str:
  d = 800000 + scale * 50000
  p = 900 + scale * 20
  return "host_ib mlx5_0 %d %d %d %d\n" % (d, d, p, p)


def _lnet_line(scale: int) -> str:
  b = 600000 + scale * 40000
  return "host_lnet ib0 %d %d\n" % (b, b)


def _opa_line(scale: int) -> str:
  d = 700000 + scale * 45000
  p = 800 + scale * 15
  c = 5 + scale
  return "host_opa hfi1_0 %d %d %d %d %d %d %d %d\n" % (d, d, p, p, c, c, c, c)


def _nfs_line(scale: int) -> str:
  return "host_nfs nfs4 %d %d\n" % (3000 + scale * 20, 2800 + scale * 18)


def _nvidia_fast_line(scale: int) -> str:
  """Fast-tier GPU sample (slow mem counters omitted)."""
  return (
      "nvidia_gpu 0 @fast "
      "%.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.2f %.1f %.0f %d 1\n"
      % (
          55.0 + (scale % 20),  # gpu_util
          12.0 + (scale % 5),  # tensor_active
          4.0 + (scale % 3),  # tensor_imma_active
          5.0 + (scale % 3),  # tensor_hmma_active
          2.0 + (scale % 2),  # tensor_dfma_active
          8.0 + (scale % 4),  # fp16_active
          10.0 + (scale % 4),  # fp32_active
          6.0 + (scale % 3),  # fp64_active
          50.0 + (scale % 5),  # integer_active (Hopper: busy under FP work)
          3.0 + (scale % 2),  # tensor_dmma_active (GPM path)
          80.0 + scale * 0.1,  # gpu_mem_bw_bytes_rate
          180.0 + scale,  # power_usage
          1e8 + scale * 1e6,  # gpu_io_link_total_bytes
          3 + (scale % 4),  # clocks_event_reasons
      )
  )


def _nvidia_full_line(scale: int) -> str:
  """Full-tier GPU sample including slow-tier memory counters."""
  return (
      "nvidia_gpu 0 @full "
      "%.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.2f %.1f %.0f %d 1 %.1f %.0f\n"
      % (
          55.0 + (scale % 20),
          12.0 + (scale % 5),
          4.0 + (scale % 3),
          5.0 + (scale % 3),
          2.0 + (scale % 2),
          8.0 + (scale % 4),
          10.0 + (scale % 4),
          6.0 + (scale % 3),
          50.0 + (scale % 5),
          3.0 + (scale % 2),
          80.0 + scale * 0.1,
          180.0 + scale,
          1e8 + scale * 1e6,
          3 + (scale % 4),
          62.0 + (scale % 15),
          12000.0 + scale * 50,
      )
  )


def _amd_gpu_line(scale: int) -> str:
  return (
      "amd_gpu 0 %.1f %.1f %.2f %.1f 1\n"
      % (
          45.0 + (scale % 25),
          8.0 + (scale % 6),
          50.0 + scale * 0.08,
          120.0 + scale,
      )
  )


def _intel_pmc_line(scale: int) -> str:
  base = 100000 + scale * 20000
  fp_vals = [str(base + i * 10000 + scale * 17) for i in range(8)]
  fixed = (
      1_000_000_000 + scale * 1_000_000,
      2_500_000_000_000 + scale * 500_000_000,
      1_000_000_000_000 + scale * 200_000_000,
  )
  return "intel_x86_pmc_gpr8 cpu %s\n" % " ".join(fp_vals + [str(x) for x in fixed])


def _intel_imc_line(scale: int) -> str:
  r = 2_000_000 + scale * 100_000
  w = 1_800_000 + scale * 90_000
  return "intel_x86_uncore_imc_skx imc %d %d\n" % (r, w)


def _intel_rapl_line(scale: int) -> str:
  return "intel_x86_rapl pkg %d\n" % (8_000_000 + scale * 50_000)


def _amd64_pmc_line(scale: int) -> str:
  flops = 900000 + scale * 15000
  merge = 10000 + scale * 100
  inst = 2_000_000 + scale * 25_000
  aperf = 3_000_000 + scale * 20_000
  mperf = 2_500_000 + scale * 18_000
  return "amd_x86_pmc cpu %d %d %d %d %d\n" % (flops, merge, inst, aperf, mperf)


def _amd64_df_line(scale: int) -> str:
  vals = [str(200000 + scale * 4000 + i * 1500) for i in range(8)]
  return "amd_x86_uncore_df df0 %s\n" % " ".join(vals)


def _arm_imc_line(scale: int) -> str:
  reads = 700000 + scale * 12000
  writes = 650000 + scale * 10000
  return "arm_aarch64_imc imc %d %d\n" % (reads, writes)


# Grace PMU sharing counters (monitor 3.1 Phase 1b, D6): per-step deltas give running
# 70% of enabled against a solo baseline of 77.8%, so the job shows 10% of
# counter time taken by other programs. Pipeline asserts read these ratios.
PMU_SHARING_E2E_RUNNING_PCT = 70.0
PMU_SHARING_E2E_OUTSIDE_PCT = 10.0


def _grace_pmu_sharing_lines(scale: int) -> str:
  """Per-CPU ``host_cpu_hw`` PMU enabled/running/solo lifetime ns (Grace, 3.1 Phase 1b)."""
  lines = []
  for cpu, base in (("60", 0), ("61", 500_000)):
    enabled = 10_000_000 + base + scale * 9_000
    running = 7_000_000 + base + scale * 6_300
    solo = 7_700_000 + base + scale * 7_000
    lines.append("host_cpu_hw %s %d %d %d\n" % (cpu, enabled, running, solo))
  return "".join(lines)


def _power_socket_lines(scale: int) -> str:
  """Two equal Grace sockets (GB200 shape: CPU, Grace, SysIO; module 0)."""
  cpu = 45 + (scale % 3)
  sock = 90 + (scale % 3)
  return "".join(f"host_power_socket {s} {cpu} {sock} 6 0\n" for s in (0, 1))


def full_stats_snapshot(epoch: float, jid: str, fqdn: str, scale: int) -> str:
  """One timestamp block with rows for every schema type we declare."""
  parts = [
      "%.6f %s %s\n" % (epoch, jid, fqdn),
      _block_line(scale),
      _cpu_lines(scale),
      _mem_line(scale),
      _net_line(scale),
      _numa_line(scale),
      _llite_line(scale),
      _ib_line(scale),
      _lnet_line(scale),
      _opa_line(scale),
      _nfs_line(scale),
      _nvidia_fast_line(scale),
      _nvidia_full_line(scale),
      _amd_gpu_line(scale),
      _intel_pmc_line(scale),
      _intel_imc_line(scale),
      _amd64_pmc_line(scale),
      _amd64_df_line(scale),
      _arm_imc_line(scale),
      _grace_pmu_sharing_lines(scale),
      _intel_rapl_line(scale),
      _power_socket_lines(scale),
  ]
  return "".join(parts)


def rotation_dollar_schema(fqdn: str) -> str:
  """Final ``$`` rotate message (same header as initial schema)."""
  return monitor_schema_header(fqdn)


def pipeline_e2e_publish_bodies_multihost(
    *,
    fqdns: list[str],
    jid: str,
    epoch_samples: list[float],
) -> list[str]:
  """Emit schema/stats/rotate bodies for multiple hosts in one synthetic job."""
  bodies = []
  for fqdn in fqdns:
    bodies.append(monitor_schema_header(fqdn))
  for i, ep in enumerate(epoch_samples):
    for hidx, fqdn in enumerate(fqdns):
      bodies.append(full_stats_snapshot(ep, jid, fqdn, i * 800 + hidx * 50))
  for fqdn in fqdns:
    bodies.append(rotation_dollar_schema(fqdn))
  return bodies


# x86 direct-perf hosts (monitor 3.1 Phases 2+3, ``$build`` slug ``x86perf``):
# one Intel SPR Max host (lowercase FP_ARITH instruction counts, HBM CAS on
# ``hbm<N>`` rows, RAPL mJ running totals that wrap at 2^32) and one AMD Zen5
# host (FLOP lifetimes). Each runs its own synthetic job so the CPU FLOP
# source of one family never masks the other. Counters are rate x seconds
# since the first sample, so every interval has the same rate; a key the CPU
# class does not program carries the NOT_PRESENT sentinel, as the monitor
# emits it.
X86_E2E_NOT_PRESENT = -2
X86_E2E_CPUS = ("0", "1")
X86_E2E_HBM_ROWS = ("hbm0", "hbm1")
X86_E2E_SOCKETS = ("0", "1")
X86_E2E_INTEL_FP_LANES = (
    ("fp_arith_inst_retired_scalar_double", 1),
    ("fp_arith_inst_retired_128b_packed_double", 2),
    ("fp_arith_inst_retired_256b_packed_double", 4),
    ("fp_arith_inst_retired_512b_packed_double", 8),
    ("fp_arith_inst_retired_scalar_single", 1),
    ("fp_arith_inst_retired_128b_packed_single", 4),
    ("fp_arith_inst_retired_256b_packed_single", 8),
    ("fp_arith_inst_retired_512b_packed_single", 16),
)
_X86_HOST_CPU_HW_SCHEMA = (
    ("instr_retired", "E,W=48"),
    ("aperf", "E,W=48"),
    ("mperf", "E,W=48"),
    *((key, "E,W=48") for key, _lanes in X86_E2E_INTEL_FP_LANES),
    ("fp_ops_retired", "E,W=64,U=FLOP"),
    ("amd_fp_dp_ops", "E,W=64,U=FLOP"),
    ("pmu_enabled_time", "E,W=64,U=ns"),
    ("pmu_running_time", "E,W=64,U=ns"),
    ("pmu_solo_running_time", "E,W=64,U=ns"),
)
# Per-CPU rates (instructions or FLOPs per second, ns per second).
_X86_PMU_RATES = {
    "pmu_enabled_time": 9.0e8,
    "pmu_running_time": 6.3e8,
    "pmu_solo_running_time": 7.0e8,
}
_X86_INTEL_CPU_RATES = {
    "instr_retired": 2.0e9,
    "aperf": 2.5e9,
    "mperf": 2.0e9,
    "fp_arith_inst_retired_scalar_double": 1.0e8,
    "fp_arith_inst_retired_128b_packed_double": 0.0,
    "fp_arith_inst_retired_256b_packed_double": 2.0e8,
    "fp_arith_inst_retired_512b_packed_double": 1.0e8,
    "fp_arith_inst_retired_scalar_single": 1.0e8,
    "fp_arith_inst_retired_128b_packed_single": 0.0,
    "fp_arith_inst_retired_256b_packed_single": 0.0,
    "fp_arith_inst_retired_512b_packed_single": 5.0e7,
    **_X86_PMU_RATES,
}
_X86_AMD_CPU_RATES = {
    "instr_retired": 2.0e9,
    "aperf": 2.5e9,
    "fp_ops_retired": 2.0e9,
    "amd_fp_dp_ops": 1.5e9,
    **_X86_PMU_RATES,
}
# Per hbm<N> row CAS per second; the dram_cas_* pair is NOT_PRESENT there.
_X86_HBM_RATES = {"hbm_cas_reads": 1.0e8, "hbm_cas_writes": 5.0e7}
_X86_HBM_PMU_RATES = {"pmu_enabled_time": 1.0e9, "pmu_running_time": 1.0e9}
# Per-socket package power; the counter starts near 2^32 so it wraps once.
X86_E2E_PKG_W_PER_SOCKET = 200.0
_X86_RAPL_START_MJ = 4_000_000_000
_RAPL_MJ_MODULUS = 2**32


def _x86_cpu_total(rates: dict[str, float], key: str) -> float:
  """Rate of one ``host_cpu_hw`` key summed over the synthetic CPUs."""
  return rates.get(key, 0.0) * len(X86_E2E_CPUS)


def _x86_intel_rate(precision: str, weighted: bool) -> float:
  """Node FP_ARITH rate of one precision: instructions, or FLOPs if weighted."""
  return sum(
      _x86_cpu_total(_X86_INTEL_CPU_RATES, key) * (lanes if weighted else 1)
      for key, lanes in X86_E2E_INTEL_FP_LANES
      if key.endswith(precision)
  )


# Each HBM CAS moves 32 bytes (measured on SPR Max); avg_mbw is GiB/s.
_HBM_GB = sum(_X86_HBM_RATES.values()) * len(X86_E2E_HBM_ROWS) * 32 / 1024**3
# Job metrics the x86 jobs must produce (GFLOP/s, Ginstr/s, GB/s, %, W).
X86_E2E_INTEL_EXPECTED = {
    "avg_flops": (
        _x86_intel_rate("double", True) + _x86_intel_rate("single", True)
    ) * 1e-9,
    "avg_flops64b": _x86_intel_rate("double", True) * 1e-9,
    "avg_flops32b": _x86_intel_rate("single", True) * 1e-9,
    "avg_fp64_instr": _x86_intel_rate("double", False) * 1e-9,
    "avg_fp32_instr": _x86_intel_rate("single", False) * 1e-9,
    "avg_mbw": _HBM_GB,
    "avg_pmu_running_pct": PMU_SHARING_E2E_RUNNING_PCT,
    "avg_pmu_outside_pct": PMU_SHARING_E2E_OUTSIDE_PCT,
    "max_node_power_est_w": X86_E2E_PKG_W_PER_SOCKET * len(X86_E2E_SOCKETS),
}
_AMD_TOTAL = _x86_cpu_total(_X86_AMD_CPU_RATES, "fp_ops_retired") * 1e-9
_AMD_DP = _x86_cpu_total(_X86_AMD_CPU_RATES, "amd_fp_dp_ops") * 1e-9
X86_E2E_AMD_EXPECTED = {
    "avg_flops": _AMD_TOTAL,
    "avg_flops64b": _AMD_DP,
    "avg_flops32b": _AMD_TOTAL - _AMD_DP,
    "avg_fp64_instr": _AMD_DP,
    "avg_fp32_instr": _AMD_TOTAL - _AMD_DP,
    "avg_pmu_running_pct": PMU_SHARING_E2E_RUNNING_PCT,
    "avg_pmu_outside_pct": PMU_SHARING_E2E_OUTSIDE_PCT,
}


def x86_schema_header(fqdn: str, vendor: str) -> str:
  """``$`` block of a direct-perf x86 host (``vendor`` is intel or amd)."""
  cpu_hw = " ".join(f"{key},{flags}" for key, flags in _X86_HOST_CPU_HW_SCHEMA)
  if vendor == "intel":
    typed = (
        "!intel_x86_uncore_imc_spr dram_cas_reads,E,W=48 dram_cas_writes,E,W=48"
        " hbm_cas_reads,E,W=48 hbm_cas_writes,E,W=48"
        " pmu_enabled_time,E,W=64,U=ns pmu_running_time,E,W=64,U=ns\n"
        "!intel_x86_rapl pkg_energy,E,W=32,U=mJ\n"
    )
  else:
    typed = "!amd_x86_rapl pkg_energy,E,W=32,U=mJ\n"
  return (
      f"$hpcperfstats 3.1\n$hostname {fqdn}\n$uname Linux x86_64 5.14.0 test\n"
      "$uptime 1\n$build x86_64-ver3.1-hw-ib-x86perf-full\n"
      f"!host_cpu_hw {cpu_hw}\n{typed}"
  )


def _counter(rate: float | None, seconds: float) -> int:
  """Lifetime counter value, or the NOT_PRESENT code when ``rate`` is None."""
  if rate is None:
    return X86_E2E_NOT_PRESENT
  return round(rate * seconds)


def x86_stats_snapshot(
    epoch: float, seconds: float, jid: str, fqdn: str, vendor: str
) -> str:
  """One sample of an x86 host ``seconds`` after its first sample."""
  cpu_rates = _X86_INTEL_CPU_RATES if vendor == "intel" else _X86_AMD_CPU_RATES
  lines = [f"{epoch:.6f} {jid} {fqdn}\n"]
  for cpu in X86_E2E_CPUS:
    vals = (
        _counter(cpu_rates.get(key), seconds)
        for key, _flags in _X86_HOST_CPU_HW_SCHEMA
    )
    lines.append(f"host_cpu_hw {cpu} {' '.join(map(str, vals))}\n")
  if vendor == "intel":
    for row in X86_E2E_HBM_ROWS:
      vals = (
          X86_E2E_NOT_PRESENT,
          X86_E2E_NOT_PRESENT,
          *(_counter(r, seconds) for r in _X86_HBM_RATES.values()),
          *(_counter(r, seconds) for r in _X86_HBM_PMU_RATES.values()),
      )
      lines.append(
          f"intel_x86_uncore_imc_spr {row} {' '.join(map(str, vals))}\n"
      )
  energy = (
      _X86_RAPL_START_MJ + int(X86_E2E_PKG_W_PER_SOCKET * 1000 * seconds)
  ) % _RAPL_MJ_MODULUS
  rapl_type = "intel_x86_rapl" if vendor == "intel" else "amd_x86_rapl"
  lines.extend(f"{rapl_type} {sock} {energy}\n" for sock in X86_E2E_SOCKETS)
  return "".join(lines)


def pipeline_e2e_x86_publish_bodies(
    *, fqdn: str, jid: str, vendor: str, epoch_samples: list[float]
) -> list[str]:
  """Schema, samples and rotate ``$`` block of one x86 host in its own job."""
  header = x86_schema_header(fqdn, vendor)
  t0 = epoch_samples[0]
  return [
      header,
      *(
          x86_stats_snapshot(ep, ep - t0, jid, fqdn, vendor)
          for ep in epoch_samples
      ),
      header,
  ]
