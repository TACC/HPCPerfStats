"""Synthetic monitor messages (listend archive format) for pipeline E2E.

Emits enough typenames/events for ``Metrics.compute_metrics`` to populate the
full job metric catalog (simple + complex + GPU detail + FSIO detail) where
telemetry allows.
"""
from __future__ import annotations

_INTEL_FP_CTL_DEC = (
    4391367,  # 0x4301c7 -> FP_ARITH_INST_RETIRED_SCALAR_DOUBLE
    4391623,  # 0x4302c7 -> FP_ARITH_INST_RETIRED_SCALAR_SINGLE
    4392135,  # 0x4304c7 -> FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE
    4393159,  # 0x4308c7 -> FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE
    4395207,  # 0x4310c7 -> FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE
    4399303,  # 0x4320c7 -> FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE
    4407495,  # 0x4340c7 -> FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE
    4423879,  # 0x4380c7 -> FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE
)


def _intel_8pmc3_schema_tokens() -> str:
  parts = []
  for i in range(8):
    parts.append("CTL%d" % i)
    parts.append("CTR%d" % i)
  parts.extend(["FIXED_CTR0,W=48", "FIXED_CTR1,W=48", "FIXED_CTR2,W=48"])
  return " ".join(parts)


_LLITE_EVENTS = [
    "open", "close", "mmap", "fsync", "setattr", "truncate", "flock", "getattr",
    "statfs", "alloc_inode", "setxattr", "listxattr", "removexattr", "readdir",
    "create", "lookup", "link", "unlink", "symlink", "mkdir", "rmdir", "mknod",
    "rename", "read_bytes", "write_bytes",
]


def monitor_schema_header(fqdn: str) -> str:
  """``$`` schema block: software counters + Intel PMC/IMC + GPU + fabric types."""
  llite_schema = " ".join("%s,E" % e for e in _LLITE_EVENTS)
  return """$tacc_stats 2.3.5
$hostname {fqdn}
$uname Linux x86_64 5.14.0 test
$uptime 1
!block rd_sectors,E,U=512B wr_sectors,E,U=512B
!cpu user,E,U=cs nice,E,U=cs system,E,U=cs idle,E,U=cs iowait,E,U=cs irq,E,U=cs softirq,E,U=cs
!mem MemTotal,U=KB MemUsed,U=KB Slab,U=KB FilePages,U=KB
!net rx_bytes,E,U=B tx_bytes,E,U=B rx_packets,E tx_packets,E
!numa numa_hit,E numa_miss,E numa_foreign,E interleave_hit,E local_node,E other_node,E
!llite {llite}
!ib_ext port_xmit_data,E port_rcv_data,E port_xmit_pkts,E port_rcv_pkts,E
!lnet tx_bytes,E,U=B rx_bytes,E,U=B
!opa PortXmitData,E PortRcvData,E PortXmitPkts,E PortRcvPkts,E PortXmitWait,E SwPortCongestion,E PortRcvFECN,E PortRcvBECN,E
!nfs READ_ops,E WRITE_ops,E
!nvidia_gpu gpu_util,E tensor_active,E gpu_mem_bw_bytes_rate,E power_usage,E gpu_io_link_total_bytes,E clocks_event_reasons,E module_power_usage,E gpu_count,E
!intel_8pmc3 {intel_pmc_schema}
!intel_skx_imc CTL0 CTR0 CTL1 CTR1
!intel_rapl MSR_PKG_ENERGY_STATUS,E,U=J
""".format(
      fqdn=fqdn,
      llite=llite_schema,
      intel_pmc_schema=_intel_8pmc3_schema_tokens(),
  )


def _block_line(scale: int) -> str:
  b = 100000 + scale * 8000
  w = 90000 + scale * 7000
  return "block sda %d %d\n" % (b, w)


def _cpu_lines(jid: str, fqdn: str, scale: int) -> str:
  u0 = 1782420 + scale
  u1 = 2000652 + scale
  return (
      "cpu 60 %d 573 1054362 169625696 11810 75091 69629\n"
      "cpu 61 %d 403 1069570 169394113 12687 75144 70234\n"
  ) % (u0, u1)


def _mem_line(scale: int) -> str:
  return "mem global 128000 %d 1200 8000\n" % (64000 + scale * 100)


def _net_line(scale: int) -> str:
  b = int(1e9) + scale * 100000
  p = int(2e6) + scale * 1000
  return "net eth0 %d %d %d %d\n" % (b, b, p, p)


def _numa_line(scale: int) -> str:
  return "numa node0 %d %d %d 10 %d %d\n" % (
      100000 + scale * 500,
      50 + scale,
      40 + scale,
      8000 + scale * 10,
      200 + scale * 5,
  )


def _llite_line(scale: int) -> str:
  vals = [str(500 + i * 10 + scale) for i in range(len(_LLITE_EVENTS))]
  return "llite scratch %s\n" % " ".join(vals)


def _ib_line(scale: int) -> str:
  d = 800000 + scale * 50000
  p = 900 + scale * 20
  return "ib_ext mlx5_0 %d %d %d %d\n" % (d, d, p, p)


def _lnet_line(scale: int) -> str:
  b = 600000 + scale * 40000
  return "lnet ib0 %d %d\n" % (b, b)


def _opa_line(scale: int) -> str:
  d = 700000 + scale * 45000
  p = 800 + scale * 15
  c = 5 + scale
  return "opa hfi1_0 %d %d %d %d %d %d %d %d\n" % (d, d, p, p, c, c, c, c)


def _nfs_line(scale: int) -> str:
  return "nfs nfs4 %d %d\n" % (3000 + scale * 20, 2800 + scale * 18)


def _nvidia_line(scale: int) -> str:
  return (
      "nvidia_gpu 0 %.1f %.1f %.2f %.1f %.0f %d %.1f 1\n"
      % (
          55.0 + (scale % 20),
          12.0 + (scale % 5),
          80.0 + scale * 0.1,
          180.0 + scale,
          1e8 + scale * 1e6,
          3 + (scale % 4),
          95.0 + (scale % 10),
      )
  )


def _intel_pmc_line(scale: int) -> str:
  base = 100000 + scale * 20000
  pairs = []
  for i in range(8):
    pairs.append(str(_INTEL_FP_CTL_DEC[i]))
    pairs.append(str(base + i * 10000 + scale * 17))
  fixed = (
      1_000_000_000 + scale * 1_000_000,
      2_500_000_000_000 + scale * 500_000_000,
      1_000_000_000_000 + scale * 200_000_000,
  )
  return "intel_8pmc3 cpu %s\n" % " ".join(pairs + [str(x) for x in fixed])


def _intel_imc_line(scale: int) -> str:
  r = 2_000_000 + scale * 100_000
  w = 1_800_000 + scale * 90_000
  # CTL ids as decimal so sync_timedb parser's int() maps them to CAS_* events.
  return "intel_skx_imc imc 4195076 %d 4197380 %d\n" % (r, w)


def _intel_rapl_line(scale: int) -> str:
  return "intel_rapl pkg %d\n" % (8_000_000 + scale * 50_000)


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
      _nvidia_line(scale),
      _intel_pmc_line(scale),
      _intel_imc_line(scale),
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
