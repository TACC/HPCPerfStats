"""Tests for exhaustive sync_timedb_mem_telemetry."""

from __future__ import annotations

from hpcperfstats.dbload.lib import process_memory as pm
from hpcperfstats.dbload.lib import sync_timedb_mem_telemetry as mt


def test_cgroup_admit_headroom_ok_fail_open_and_block(monkeypatch):
  assert pm.cgroup_admit_headroom_ok(0) is True
  monkeypatch.setattr(pm, "read_cgroup_memory_max_bytes", lambda: None)
  assert pm.cgroup_admit_headroom_ok(16384) is True
  monkeypatch.setattr(pm, "read_cgroup_memory_max_bytes", lambda: 128 * 1024 ** 3)
  monkeypatch.setattr(
      pm, "read_cgroup_memory_current_bytes", lambda: 127 * 1024 ** 3,
  )
  assert pm.cgroup_admit_headroom_ok(16384) is False
  monkeypatch.setattr(
      pm, "read_cgroup_memory_current_bytes", lambda: 100 * 1024 ** 3,
  )
  assert pm.cgroup_admit_headroom_ok(16384) is True


def test_cgroup_admit_file_cache_ok_fail_open_and_block(monkeypatch):
  assert pm.cgroup_admit_file_cache_ok(0) is True
  monkeypatch.setattr(pm, "read_cgroup_memory_stat", lambda: {})
  assert pm.cgroup_admit_file_cache_ok(81920) is True
  monkeypatch.setattr(
      pm,
      "read_cgroup_memory_stat",
      lambda: {"file": 90 * 1024 ** 3},
  )
  assert pm.cgroup_admit_file_cache_ok(81920) is False
  assert pm.cgroup_admit_file_cache_ok(65536) is False
  monkeypatch.setattr(
      pm,
      "read_cgroup_memory_stat",
      lambda: {"file": 80 * 1024 ** 3},
  )
  assert pm.cgroup_admit_file_cache_ok(81920) is True


def test_format_includes_contract_tokens(monkeypatch):
  mt.reset_mem_telemetry_state_for_tests()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_timedb_mem_telemetry",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_process_tree_rss_limit_cgroup_pct",
      lambda: 40,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_process_tree_rss_exit_cgroup_pct",
      lambda: 0,
  )
  monkeypatch.setattr(
      pm,
      "effective_process_tree_rss_limit_mib",
      lambda: 52428,
  )
  monkeypatch.setattr(
      pm,
      "effective_process_tree_rss_exit_mib",
      lambda: 0,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_headroom_mib",
      lambda: 16384,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_archive_zstd_drop_page_cache",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_pool_processes",
      lambda: 48,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_listend_db_ingest_pool_processes",
      lambda: 32,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_metrics_pool_processes",
      lambda: 48,
  )
  monkeypatch.setattr(
      mt, "compute_ingest_inflight_raw_bytes_budget", lambda: 20000 * 1024 * 1024,
  )
  monkeypatch.setattr(pm, "read_cgroup_memory_current_bytes", lambda: 80 * 1024 ** 3)
  monkeypatch.setattr(pm, "read_cgroup_memory_peak_bytes", lambda: 100 * 1024 ** 3)
  monkeypatch.setattr(pm, "read_cgroup_memory_max_bytes", lambda: 128 * 1024 ** 3)
  monkeypatch.setattr(
      pm,
      "read_cgroup_memory_events",
      lambda: {
          "low": 0, "high": 0, "max": 10, "oom": 1,
          "oom_kill": 2, "oom_group_kill": 0,
      },
  )
  monkeypatch.setattr(
      pm,
      "read_cgroup_memory_stat",
      lambda: {
          "anon": 34 * 1024 ** 3,
          "file": 90 * 1024 ** 3,
          "file_mapped": 0,
          "shmem": 0,
          "active_anon": 30 * 1024 ** 3,
          "inactive_anon": 4 * 1024 ** 3,
          "active_file": 40 * 1024 ** 3,
          "inactive_file": 50 * 1024 ** 3,
          "unevictable": 0,
          "slab": 3 * 1024 ** 3,
          "pgfault": 1,
          "pgmajfault": 2,
      },
  )
  monkeypatch.setattr(
      pm,
      "format_tree_rss_breakdown_mb",
      lambda *_a, **_k: {
          "supervisor_mb": 10.0,
          "ingest_pool_mb": 20.0,
          "archive_pool_mb": 5.0,
          "tree_total_mb": 35.0,
      },
  )
  monkeypatch.setattr(pm, "read_process_nlwp", lambda pid=None: 100)
  monkeypatch.setattr(
      pm,
      "read_daemon_rss_by_cmdline",
      lambda: {
          "sync": 29 * 1024 ** 3,
          "listend": 3 * 1024 ** 3,
          "metrics": 2 * 1024 ** 3,
      },
  )
  monkeypatch.setattr(
      pm,
      "read_other_cgroup_rss",
      lambda exclude_pids=None: {"other_rss_bytes": 0, "other_top": ""},
  )
  sizes = {"/a/big.stats": 5 * 1024 * 1024, "/a/small.stats": 1024 * 1024}
  line = mt.format_sync_timedb_mem_telemetry_line(
      "census",
      inflight_sizes=sizes,
      ingest_q="48/100",
      append_q="2/50",
      discover_q="1/0",
      day_close_q="0/0",
      append_inflight_n=2,
      day_close_inflight_n=0,
  )
  assert line.startswith("INFO: sync_timedb_mem_telemetry: event=census")
  for tok in (
      "rss_limit_cgroup_pct=", "rss_exit_cgroup_pct=",
      "rss_limit_mib=", "budget_mib=", "headroom_cfg_mib=", "file_cache_cfg_mib=",
      "file_cache_ok=", "drop_page_cache=yes",
      "cgroup_mib=", "ev_oom_kill=", "ev_max=", "anon_mib=", "file_mib=",
      "sync_rss_mib=", "listend_rss_mib=", "metrics_rss_mib=", "gap_mib=",
      "inflight_n=", "top_inflight=", "largest_inflight=",
      "ingest_q=", "append_q=", "d_cgroup_mib=", "d_file_mib=",
  ):
    assert tok in line, tok
  line2 = mt.format_sync_timedb_mem_telemetry_line(
      "census",
      inflight_sizes=sizes,
  )
  assert "d_cgroup_mib=" in line2


def test_telem_silent_when_knob_off(monkeypatch):
  mt.reset_mem_telemetry_state_for_tests()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_timedb_mem_telemetry",
      lambda: False,
  )
  logged = []
  mt.maybe_emit_mem_telemetry("census", logged.append, inflight_sizes={})
  assert logged == []


def test_edge_oom_kill_delta(monkeypatch):
  mt.reset_mem_telemetry_state_for_tests()
  snap1 = {"ev_oom_kill": 2, "ev_max": 10}
  assert mt.detect_edge_events(snap1) == []
  snap2 = {"ev_oom_kill": 3, "ev_max": 12}
  edges = mt.detect_edge_events(snap2)
  assert "oom_kill_delta" in edges
  assert "cgroup_max_delta" in edges
