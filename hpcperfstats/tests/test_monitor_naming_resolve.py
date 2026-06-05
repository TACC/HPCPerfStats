"""Dual-read helpers for canonical + legacy monitor names."""
from hpcperfstats.monitor_naming.legacy import (
    INGEST_LEGACY_KNL_IMC_TYPE,
    MONITOR_LEGACY_KNL_IMC_TYPE,
)
from hpcperfstats.monitor_naming.resolve import (
    dram_cas_read_write_pairs,
    events_probe_names,
    imc_types_probe_order,
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


def test_imc_types_probe_order_includes_legacy_knl_after_canonical():
  order = imc_types_probe_order()
  assert "intel_x86_uncore_imc_skx" in order
  assert INGEST_LEGACY_KNL_IMC_TYPE in order
  assert MONITOR_LEGACY_KNL_IMC_TYPE in order
  assert order.index("intel_x86_uncore_imc_skx") < order.index(INGEST_LEGACY_KNL_IMC_TYPE)


def test_type_probe_names_host_ib_includes_retired_collectors():
  names = type_probe_names("host_ib")
  assert names[0] == "host_ib"
  assert "host_ib_ext" in names
  assert "ib_ext" in names
  assert "host_ib_sw" in names
  assert "ib_sw" in names
  assert "ib" in names
