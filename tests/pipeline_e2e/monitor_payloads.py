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
!host_ib port_xmit_data,E port_rcv_data,E port_xmit_pkts,E port_rcv_pkts,E
!host_lnet tx_bytes,E,U=B rx_bytes,E,U=B
!host_opa PortXmitData,E PortRcvData,E PortXmitPkts,E PortRcvPkts,E PortXmitWait,E SwPortCongestion,E PortRcvFECN,E PortRcvBECN,E
!host_nfs read_ops,E write_ops,E
!nvidia_gpu gpu_util,E tensor_active,E tensor_imma_active,E tensor_hmma_active,E tensor_dfma_active,E fp16_active,E fp32_active,E fp64_active,E gpu_mem_bw_bytes_rate,E power_usage,E gpu_io_link_total_bytes,E clocks_event_reasons,E module_power_usage,E gpu_count,E gpu_mem_util,E,R=S gpu_mem_used_mb,E,R=S
!amd_gpu gpu_util,E tensor_active,E gpu_mem_bw_bytes_rate,E power_usage,E gpu_count,E
!intel_x86_pmc_gpr8 {intel_pmc_schema}
!intel_x86_uncore_imc_skx dram_cas_reads,E dram_cas_writes,E
!amd_x86_pmc fp_ops_retired,E fp_ops_merge,E instr_retired,E aperf,E mperf,E
!amd_x86_uncore_df MBW_CHANNEL_0,E MBW_CHANNEL_1,E MBW_CHANNEL_2,E MBW_CHANNEL_3,E MBW_CHANNEL_4,E MBW_CHANNEL_5,E MBW_CHANNEL_6,E MBW_CHANNEL_7,E
!arm_aarch64_imc dram_cas_reads,E dram_cas_writes,E
!intel_x86_rapl pkg_energy,E,U=J
""".format(
      fqdn=fqdn,
      llite=llite_schema,
      intel_pmc_schema=intel_pmc_schema,
  )


def _block_line(scale: int) -> str:
  b = 100000 + scale * 8000
  w = 90000 + scale * 7000
  return "host_block sda %d %d\n" % (b, w)


def _cpu_lines(jid: str, fqdn: str, scale: int) -> str:
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
      "%.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.2f %.1f %.0f %d %.1f 1\n"
      % (
          55.0 + (scale % 20),  # gpu_util
          12.0 + (scale % 5),  # tensor_active
          4.0 + (scale % 3),  # tensor_imma_active
          5.0 + (scale % 3),  # tensor_hmma_active
          2.0 + (scale % 2),  # tensor_dfma_active
          8.0 + (scale % 4),  # fp16_active
          10.0 + (scale % 4),  # fp32_active
          6.0 + (scale % 3),  # fp64_active
          80.0 + scale * 0.1,  # gpu_mem_bw_bytes_rate
          180.0 + scale,  # power_usage
          1e8 + scale * 1e6,  # gpu_io_link_total_bytes
          3 + (scale % 4),  # clocks_event_reasons
          95.0 + (scale % 10),  # module_power_usage
      )
  )


def _nvidia_full_line(scale: int) -> str:
  """Full-tier GPU sample including slow-tier memory counters."""
  return (
      "nvidia_gpu 0 @full "
      "%.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.2f %.1f %.0f %d %.1f 1 %.1f %.0f\n"
      % (
          55.0 + (scale % 20),
          12.0 + (scale % 5),
          4.0 + (scale % 3),
          5.0 + (scale % 3),
          2.0 + (scale % 2),
          8.0 + (scale % 4),
          10.0 + (scale % 4),
          6.0 + (scale % 3),
          80.0 + scale * 0.1,
          180.0 + scale,
          1e8 + scale * 1e6,
          3 + (scale % 4),
          95.0 + (scale % 10),
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


def full_stats_snapshot(epoch: float, jid: str, fqdn: str, scale: int) -> str:
  """One timestamp block with rows for every schema type we declare."""
  parts = [
      "%.6f %s %s\n" % (epoch, jid, fqdn),
      _block_line(scale),
      _cpu_lines(jid, fqdn, scale),
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
      _intel_rapl_line(scale),
  ]
  return "".join(parts)


def rotation_dollar_schema(fqdn: str) -> str:
  """Final ``$`` rotate message (same header as initial schema)."""
  return monitor_schema_header(fqdn)


def pipeline_e2e_publish_bodies(
    *,
    fqdn: str,
    jid: str,
    epoch_samples: list[float],
) -> list[str]:
  """Ordered RabbitMQ bodies: schema → one stats blob per epoch → rotate."""
  bodies = [monitor_schema_header(fqdn)]
  for i, ep in enumerate(epoch_samples):
    bodies.append(full_stats_snapshot(ep, jid, fqdn, i * 800))
  bodies.append(rotation_dollar_schema(fqdn))
  return bodies


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
