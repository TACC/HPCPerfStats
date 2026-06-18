"""Regression tests for sync_timedb ingest heap release and worker RSS logging."""

from __future__ import annotations

import gc

import pytest

from hpcperfstats.dbload import sync_timedb as st


def test_release_ingest_worker_heap_calls_malloc_trim_when_enabled(monkeypatch):
  trim_calls = []
  collect_calls = []

  class _Libc:
    @staticmethod
    def malloc_trim(_arg):
      trim_calls.append(_arg)
      return 1

  monkeypatch.setattr(st.cfg, "get_sync_ingest_malloc_trim_after_file", lambda: True)
  monkeypatch.setattr(st, "gc", gc)
  monkeypatch.setattr(st.gc, "collect", lambda: collect_calls.append(True))
  monkeypatch.setattr(st.ctypes, "CDLL", lambda _name: _Libc())
  st._HOST_ITIMES_CACHE["probe"] = set()
  st._HOST_SECOND_PRESENT_CACHE["probe"] = True

  st._release_ingest_worker_heap()

  assert collect_calls == [True]
  assert trim_calls == [0]
  assert st._HOST_ITIMES_CACHE == {}
  assert st._HOST_SECOND_PRESENT_CACHE == {}


def test_release_ingest_worker_heap_skips_malloc_trim_when_disabled(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_malloc_trim_after_file", lambda: False)
  monkeypatch.setattr(
      st.ctypes,
      "CDLL",
      lambda _name: pytest.fail("CDLL should not run when trim disabled"),
  )
  st._HOST_ITIMES_CACHE["x"] = {1}
  st._release_ingest_worker_heap()
  assert st._HOST_ITIMES_CACHE == {}


def test_log_ingest_worker_file_completion_includes_rss_and_rows(monkeypatch, capsys):
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 4_900_000_000)
  monkeypatch.setattr(st, "_worker_rss_mib", lambda: 8123.5)
  st._log_ingest_worker_file_completion(
      "/data/host.example/1775163056",
      elapsed_s=42.5,
      parse_elapsed_s=30.0,
      stats_rows=1200,
      proc_rows=34,
      stage="ingest",
  )
  out = capsys.readouterr().out
  assert "ingest file completed" in out
  assert "size_bytes=4900000000" in out
  assert "worker_rss_mib=8123.5" in out
  assert "stats_rows=1200" in out
  assert "proc_rows=34" in out
  assert "parse_elapsed_s=30.0" in out


def test_should_stream_stats_file_for_4_6gib_class_segment(monkeypatch, tmp_path):
  stats_file = tmp_path / "host.example.com" / "1775163056"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text("1 0 host.example.com\n")
  monkeypatch.setattr(st.cfg, "get_sync_ingest_max_file_read_bytes", lambda: 512 << 20)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_stream_duplicate_scan_bytes", lambda: 8 << 20)
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: int(4.6 * (1024 ** 3)))
  assert st._should_stream_stats_file(str(stats_file), None) is True


def test_conf_parser_ingest_memory_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_sync_ingest_pool_maxtasksperchild() == 1
  assert cfg.get_sync_ingest_malloc_trim_after_file() is True
  assert cfg.get_sync_pool_process_cap() == 8
  assert cfg.get_sync_process_tree_rss_limit_mb() == 96000
