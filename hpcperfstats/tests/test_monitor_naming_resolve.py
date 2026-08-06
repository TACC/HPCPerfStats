"""Dual-read helpers for canonical + legacy monitor names."""
from pathlib import Path

from hpcperfstats.dbload.lib.monitor_naming.legacy import (
    INGEST_LEGACY_KNL_IMC_TYPE,
    MONITOR_LEGACY_KNL_IMC_TYPE,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    dram_cas_read_write_pairs,
    events_probe_names,
    hbm_cas_read_write_pairs,
    imc_types_probe_order,
    type_probe_names,
)


def test_monitor_variable_rename_map_yaml_drift():
  repo_root = Path(__file__).resolve().parents[2]
  docs_yaml = repo_root / "docs" / "monitor_variable_rename_map.yaml"
  pkg_yaml = (
      repo_root / "hpcperfstats" / "dbload" / "lib" / "monitor_naming"
      / "monitor_variable_rename_map.yaml"
  )
  assert docs_yaml.is_file()
  assert pkg_yaml.is_file()
  assert docs_yaml.read_bytes() == pkg_yaml.read_bytes()


def test_type_probe_names_canonical_then_legacy():
  names = type_probe_names("host_cpu")
  assert names[0] == "host_cpu"
  assert "cpu" in names


def test_arm_int_ops_event_names_dual_read():
  from hpcperfstats.dbload.lib.monitor_naming.resolve import (
      arm_dram_bw_event_names,
      arm_int16_ops_event_names,
      arm_int8_ops_event_names,
      grace_fp_scalar_double_event_names,
      grace_fp_scalar_single_event_names,
  )

  assert arm_int8_ops_event_names()[0] == "arm_int8_ops"
  assert "ARM_INT8_OPS" in arm_int8_ops_event_names()
  assert arm_int16_ops_event_names()[0] == "arm_int16_ops"
  assert "ARM_INT16_OPS" in arm_int16_ops_event_names()
  assert arm_dram_bw_event_names()[0] == "arm_dram_bw_bytes"
  assert "ARM_DRAM_BW_BYTES" in arm_dram_bw_event_names()
  assert grace_fp_scalar_double_event_names() == (
      "fp_arith_inst_retired_scalar_double",
  )
  assert grace_fp_scalar_single_event_names() == (
      "fp_arith_inst_retired_scalar_single",
  )


def test_events_probe_names_arm_int8_includes_legacy_alias():
  names = events_probe_names(["arm_int8_ops"])
  assert "arm_int8_ops" in names
  assert "ARM_INT8_OPS" in names


def test_events_probe_names_gpu_mem_util_includes_legacy_alias():
  """Summary plots request legacy mem_util; canonical ingest stores gpu_mem_util."""
  names = events_probe_names(["mem_util"])
  assert "mem_util" in names
  assert "gpu_mem_util" in names


def test_lustre_llite_type_scoped_event_probes():
  """type_events.lustre_llite dual-reads without polluting global open/read."""
  from hpcperfstats.dbload.lib.monitor_naming.resolve import (
      event_probe_names,
      event_probe_names_for_type,
  )

  names = event_probe_names_for_type("lustre_llite", "read_bytes")
  assert names[0] == "vfs_read_bytes"
  assert "read_bytes" in names

  names_llite = event_probe_names_for_type("llite", "getattr")
  assert names_llite[0] == "vfs_getattr_ops"
  assert "getattr" in names_llite

  names_canon = event_probe_names_for_type("lustre_llite", "vfs_open_ops")
  assert names_canon[0] == "vfs_open_ops"
  assert "open" in names_canon

  # Global open must not steal llite mapping (collides with other types).
  global_open = event_probe_names("open")
  assert "vfs_open_ops" not in global_open
  assert events_probe_names(["open"]) == list(global_open)

  # Capacity gauges are type-local but have no legacy aliases.
  cap = event_probe_names_for_type("lustre_llite", "fs_bytes_total")
  assert cap[0] == "fs_bytes_total"


def test_events_probe_names_with_typ_expands_llite_bytes():
  names = events_probe_names(["vfs_read_bytes", "vfs_write_bytes"], typ="lustre_llite")
  assert "vfs_read_bytes" in names
  assert "read_bytes" in names
  assert "vfs_write_bytes" in names
  assert "write_bytes" in names


def test_dram_cas_read_write_pairs_includes_legacy():
  pairs = dram_cas_read_write_pairs()
  assert ("dram_cas_reads", "dram_cas_writes") in pairs
  assert ("CAS_READS", "CAS_WRITES") in pairs


def test_hbm_cas_read_write_pairs():
  pairs = hbm_cas_read_write_pairs()
  assert pairs == (("hbm_cas_reads", "hbm_cas_writes"),)


def test_imc_types_probe_order_includes_legacy_knl_after_canonical():
  order = imc_types_probe_order()
  assert "intel_x86_uncore_imc_skx" in order
  assert INGEST_LEGACY_KNL_IMC_TYPE in order
  assert MONITOR_LEGACY_KNL_IMC_TYPE in order
  assert order.index("intel_x86_uncore_imc_skx") < order.index(INGEST_LEGACY_KNL_IMC_TYPE)


def test_imc_types_probe_order_includes_icx_spr_short_forms():
  order = imc_types_probe_order()
  assert "intel_icx_imc" in order
  assert "intel_spr_imc" in order


def test_amd_df_types_probe_order_family_before_historical():
  from hpcperfstats.dbload.lib.monitor_naming.resolve import amd_df_types_probe_order

  order = amd_df_types_probe_order()
  assert order[0] == "amd_x86_uncore_df_rome"
  assert "amd_x86_uncore_df_milan" in order
  assert "amd_x86_uncore_df_genoa" in order
  assert "amd_x86_uncore_df_turin" in order
  assert "amd_x86_uncore_df" in order
  assert "amd64_df" in order
  assert order.index("amd_x86_uncore_df_rome") < order.index("amd_x86_uncore_df")


def test_type_probe_names_bare_amd_df_expands_to_family():
  names = type_probe_names("amd_x86_uncore_df")
  assert "amd_x86_uncore_df_rome" in names
  assert names[0] == "amd_x86_uncore_df_rome"


def test_type_probe_names_family_amd_df_stays_exact():
  """Family DF must not alias onto historical bare amd_x86_uncore_df rows."""
  names = type_probe_names("amd_x86_uncore_df_rome")
  assert names == ("amd_x86_uncore_df_rome",)
  assert "amd_x86_uncore_df" not in names
  assert "amd64_df" not in names


def test_mbw_channel_event_probe_includes_dram_chan():
  names = events_probe_names(["MBW_CHANNEL_0"])
  assert "MBW_CHANNEL_0" in names
  assert "dram_chan0_bytes" in names


def test_type_probe_names_host_ib_includes_retired_collectors():
  names = type_probe_names("host_ib")
  assert names[0] == "host_ib"
  assert "host_ib_ext" in names
  assert "ib_ext" in names
  assert "host_ib_sw" in names
  assert "ib_sw" in names
  assert "ib" in names
