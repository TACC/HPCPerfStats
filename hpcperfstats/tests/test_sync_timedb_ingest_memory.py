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


def test_conf_parser_ingest_memory_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_sync_ingest_pool_maxtasksperchild() == 1
  assert cfg.get_sync_ingest_malloc_trim_after_file() is True
  assert cfg.get_sync_pool_process_cap() == 8
  assert cfg.get_sync_process_tree_rss_limit_mb() == 96000
