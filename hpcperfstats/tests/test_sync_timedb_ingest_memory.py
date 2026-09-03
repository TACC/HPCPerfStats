"""Regression tests for sync_timedb ingest heap release and worker RSS logging."""

from __future__ import annotations

import gc

import pytest
import ctypes

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as archive_helpers
from hpcperfstats.dbload.lib import sync_timedb_worker_memory as worker_memory


def test_release_spawn_pool_worker_memory_clears_caches_and_trims(monkeypatch):
  trim_calls = []
  stage_clear = []

  class _Libc:
    @staticmethod
    def malloc_trim(_arg):
      trim_calls.append(_arg)
      return 1

  archive_helpers._DAILY_ARCHIVE_MEMBERS_CACHE["day"] = {"m": True}
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_malloc_trim_after_file",
      lambda: True,
  )
  monkeypatch.setattr(gc, "collect", lambda: None)
  monkeypatch.setattr(ctypes, "CDLL", lambda _name: _Libc())
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_host_itimes.reset_host_itimes_caches",
      lambda: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics.clear_worker_stage",
      lambda: stage_clear.append(True),
  )

  worker_memory.release_spawn_pool_worker_memory()

  assert trim_calls == [0]
  assert archive_helpers._DAILY_ARCHIVE_MEMBERS_CACHE == {}
  assert stage_clear == [True]


def test_release_ingest_worker_heap_calls_malloc_trim_when_enabled(monkeypatch):
  trim_calls = []
  collect_calls = []
  l1_clear_calls = []

  class _Libc:
    @staticmethod
    def malloc_trim(_arg):
      trim_calls.append(_arg)
      return 1

  monkeypatch.setattr(st.cfg, "get_sync_ingest_malloc_trim_after_file", lambda: True)
  monkeypatch.setattr(st, "gc", gc)
  monkeypatch.setattr(st.gc, "collect", lambda: collect_calls.append(True))
  monkeypatch.setattr(st.ctypes, "CDLL", lambda _name: _Libc())
  monkeypatch.setattr(
      st,
      "clear_daily_archive_members_cache",
      lambda: l1_clear_calls.append(True),
  )
  st._HOST_ITIMES_CACHE["probe"] = set()
  st._HOST_SECOND_PRESENT_CACHE["probe"] = True

  st._release_ingest_worker_heap()

  assert collect_calls == [True]
  assert trim_calls == [0]
  assert st._HOST_ITIMES_CACHE == {}
  assert st._HOST_SECOND_PRESENT_CACHE == {}
  assert l1_clear_calls == []


def test_release_ingest_worker_memory_clears_l1_and_returns_meta(monkeypatch):
  trim_calls = []
  archive_helpers._DAILY_ARCHIVE_MEMBERS_CACHE["day"] = {"m": True}
  monkeypatch.setattr(st.cfg, "get_sync_ingest_malloc_trim_after_file", lambda: True)
  monkeypatch.setattr(st, "gc", gc)
  monkeypatch.setattr(st.gc, "collect", lambda: None)

  class _Libc:
    @staticmethod
    def malloc_trim(_arg):
      trim_calls.append(_arg)
      return 1

  monkeypatch.setattr(st.ctypes, "CDLL", lambda _name: _Libc())
  monkeypatch.setattr(st, "measure_worker_rss_after_release", lambda _p: {
      "worker_pid": 999,
      "tasks_on_worker": 1,
      "rss_mib_after_release": 42.0,
      "giant": "no",
  })
  meta = st._release_ingest_worker_memory("/data/host/1")
  assert trim_calls == [0]
  assert archive_helpers._DAILY_ARCHIVE_MEMBERS_CACHE == {}
  assert meta["worker_pid"] == 999


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


def test_log_ingest_file_outcome_includes_rows_and_size(monkeypatch, capsys):
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 4_900_000_000)
  outcome = st.IngestFileOutcome(
      path="/data/host.example/1775163056",
      elapsed_s=42.5,
      ingest_ok=True,
      need_archival=True,
      outcome="ingested",
      parse_elapsed_s=30.0,
      stats_rows=1200,
      proc_rows=34,
  )
  st._log_ingest_file_outcome(outcome)
  out = capsys.readouterr().out
  assert "ingest file path=" in out
  assert "outcome=ingested" in out
  assert "size_bytes=4900000000" in out
  assert "stats_rows=1200" in out
  assert "proc_rows=34" in out
  assert "parse_elapsed_s=30.0" in out
  assert "archive=yes" in out
  assert "ingest file completed" not in out


def test_log_ingest_file_outcome_includes_sealed_archive_remaining(
    monkeypatch, capsys,
):
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 100)
  try:
    st.set_sealed_archive_ingest_progress(5)
    st._log_ingest_file_outcome(
        st.IngestFileOutcome(
            path="/spool/host/1",
            elapsed_s=1.0,
            ingest_ok=True,
            need_archival=False,
            outcome="ingested",
            stats_rows=10,
            proc_rows=1,
        ),
    )
    st._log_ingest_file_outcome(
        st.IngestFileOutcome(
            path="/spool/host/2",
            elapsed_s=2.0,
            ingest_ok=True,
            need_archival=False,
            outcome="ingested",
            stats_rows=20,
            proc_rows=2,
        ),
    )
  finally:
    st.clear_sealed_archive_ingest_progress()
  out = capsys.readouterr().out
  assert "sealed_remaining=4/5" in out
  assert "sealed_remaining=3/5" in out


def test_advance_sealed_archive_ingest_progress_counts_skipped_toward_remaining(
    monkeypatch, capsys,
):
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 100)
  try:
    st.set_sealed_archive_ingest_progress(5)
    st.advance_sealed_archive_ingest_progress()
    st.advance_sealed_archive_ingest_progress()
    st._log_ingest_file_outcome(
        st.IngestFileOutcome(
            path="/spool/host/1",
            elapsed_s=1.0,
            ingest_ok=True,
            need_archival=False,
            outcome="ingested",
            stats_rows=10,
            proc_rows=1,
        ),
    )
  finally:
    st.clear_sealed_archive_ingest_progress()
  out = capsys.readouterr().out
  assert "sealed_remaining=2/5" in out


def test_should_stream_stats_file_for_4_6gib_class_segment(monkeypatch, tmp_path):
  stats_file = tmp_path / "host.example.com" / "1775163056"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text("1 0 host.example.com\n")
  monkeypatch.setattr(st.cfg, "get_sync_ingest_max_file_read_bytes", lambda: 512 << 20)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_stream_duplicate_scan_bytes", lambda: 8 << 20)
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: int(4.6 * (1024 ** 3)))
  assert st._should_stream_stats_file(str(stats_file), None) is True


def test_mutable_tar_authority_cache_trims_to_max_entries(monkeypatch):
  archive_helpers._MUTABLE_TAR_AUTHORITY_MEMBERS_CACHE.clear()
  monkeypatch.setattr(
      archive_helpers.cfg,
      "get_sync_archive_members_cache_max_entries",
      lambda: 2,
  )
  monkeypatch.setattr(archive_helpers.os.path, "isfile", lambda _p: False)
  archive_helpers.get_mutable_tar_authority_member_map("/tars/a.tar")
  archive_helpers.get_mutable_tar_authority_member_map("/tars/b.tar")
  archive_helpers.get_mutable_tar_authority_member_map("/tars/c.tar")
  cached = archive_helpers._MUTABLE_TAR_AUTHORITY_MEMBERS_CACHE
  assert len(cached) == 2
  assert "/tars/a.tar" not in cached
  assert "/tars/b.tar" in cached
  assert "/tars/c.tar" in cached
  archive_helpers._MUTABLE_TAR_AUTHORITY_MEMBERS_CACHE.clear()


def test_conf_parser_ingest_memory_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_sync_ingest_pool_maxtasksperchild() == 0
  assert cfg.get_sync_ingest_malloc_trim_after_file() is True
  assert cfg.get_sync_ingest_worker_memory_telemetry() is False
  assert not hasattr(cfg, "get_sync_ingest_cooperative_recycle_after_giant")
  assert cfg.get_sync_ingest_recycle_worker_on_failure() is True
  assert cfg.get_sync_ingest_cooperative_recycle_rss_fraction() == 0.5
  assert cfg.get_sync_ingest_pool_processes() == 16
  assert cfg.get_sync_process_tree_rss_limit_mb() == 110000
