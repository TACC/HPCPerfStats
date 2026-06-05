"""Dual-read helpers for canonical + legacy monitor names."""
from hpcperfstats.monitor_naming.resolve import (
    dram_cas_read_write_pairs,
    events_probe_names,
    type_probe_names,
)


def test_type_probe_names_canonical_then_legacy():
  names = type_probe_names("host_cpu")
  assert names[0] == "host_cpu"
  assert "cpu" in names


def test_events_probe_names_fp_ops_and_flops():
  names = events_probe_names(["fp_ops_retired"])
  assert "fp_ops_retired" in names
  assert "FLOPS" in names


def test_dram_cas_read_write_pairs_includes_legacy():
  pairs = dram_cas_read_write_pairs()
  assert ("dram_cas_reads", "dram_cas_writes") in pairs
  assert ("CAS_READS", "CAS_WRITES") in pairs
