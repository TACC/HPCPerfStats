"""Canonical monitor stats parsing (semantic event keys, no CTL/CTR)."""
from hpcperfstats.dbload.sync_timedb_parsing import parse_stats_lines


def test_parse_stats_lines_canonical_intel_pmc_and_imc():
  lines = [
      "$\n",
      "1709123456 12345 host01\n",
      "!intel_x86_pmc_gpr8 instr_retired,E,W=48 aperf,E,W=48 mperf,E,W=48\n",
      "!intel_x86_uncore_imc_skx dram_cas_reads,E dram_cas_writes,E\n",
      "intel_x86_pmc_gpr8 cpu 1000 2000 3000\n",
      "intel_x86_uncore_imc_skx imc 4000 5000\n",
  ]
  stats, proc_stats = parse_stats_lines(lines, start_idx=0)
  assert proc_stats == []
  events = {(r["type"], r["event"], r["value"]) for r in stats}
  assert ("intel_x86_pmc_gpr8", "instr_retired", 1000.0) in events
  assert ("intel_x86_pmc_gpr8", "aperf", 2000.0) in events
  assert ("intel_x86_uncore_imc_skx", "dram_cas_reads", 4000.0) in events
  assert ("intel_x86_uncore_imc_skx", "dram_cas_writes", 5000.0) in events


def test_parse_stats_lines_host_cpu_and_mem():
  lines = [
      "1709123456 1 host01\n",
      "!host_cpu user,E system,E idle,E\n",
      "!host_mem mem_total,U=KB mem_used,U=KB\n",
      "host_cpu global 10 20 70\n",
      "host_mem global 100000 50000\n",
  ]
  stats, _ = parse_stats_lines(lines, start_idx=0)
  assert ("host_cpu", "user", 10.0) in {(r["type"], r["event"], r["value"]) for r in stats}
  assert ("host_mem", "mem_used", 50000.0) in {(r["type"], r["event"], r["value"]) for r in stats}


def test_parse_stats_lines_host_ib_not_excluded_by_default():
  lines = [
      "1709123456 1 host01\n",
      "!host_ib port_xmit_data,E,U=4B port_rcv_data,E,U=4B\n",
      "host_ib mlx5_0 1000 2000\n",
  ]
  stats, _ = parse_stats_lines(lines, start_idx=0)
  events = {(r["type"], r["event"], r["value"]) for r in stats}
  assert ("host_ib", "port_xmit_data", 1000.0) in events
  assert ("host_ib", "port_rcv_data", 2000.0) in events
