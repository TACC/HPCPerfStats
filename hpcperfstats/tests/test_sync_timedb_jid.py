"""Unit tests for sync_timedb --jid ingest-only helpers and entry."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hpcperfstats.dbload.lib import sync_timedb_jid_scope as jid_scope
from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
    FindStatsRecord,
    expand_sorted_records_with_window_neighbors,
    filter_host_scoped_window_records,
)
import hpcperfstats.dbload.sync_timedb as st


def _rec(host_dir: str, epoch: int, inode: int = 1) -> FindStatsRecord:
  return FindStatsRecord(
      path="%s/%d" % (host_dir, epoch),
      mtime=float(epoch),
      size=10,
      inode=inode,
  )


def test_parse_sync_timedb_jid_cli_arg_happy_space_and_equals():
  jid, err = jid_scope.parse_sync_timedb_jid_cli_arg(
      ["sync_timedb.py", "--jid", "12345"],
  )
  assert err is None
  assert jid == "12345"
  jid2, err2 = jid_scope.parse_sync_timedb_jid_cli_arg(
      ["sync_timedb.py", "--jid=99"],
  )
  assert err2 is None
  assert jid2 == "99"


def test_parse_sync_timedb_jid_cli_arg_mutual_exclusion_and_empty():
  jid, err = jid_scope.parse_sync_timedb_jid_cli_arg(
      ["sync_timedb.py", "--jid", "1", "2026-01-01"],
  )
  assert jid is None
  assert "cannot be combined" in err
  jid2, err2 = jid_scope.parse_sync_timedb_jid_cli_arg(
      ["sync_timedb.py", "--jid", ""],
  )
  assert jid2 is None
  assert "empty" in err2
  jid3, err3 = jid_scope.parse_sync_timedb_jid_cli_arg(
      ["sync_timedb.py"],
  )
  assert jid3 is None and err3 is None


def test_padded_job_window_plus_minus_one_hour_and_null_end():
  start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
  end = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
  w0, w1 = jid_scope.padded_job_window(start, end)
  assert w0 == start - timedelta(hours=1)
  assert w1 == end + timedelta(hours=1)

  now = datetime(2026, 7, 2, 0, 0, 0, tzinfo=timezone.utc)
  w2, w3 = jid_scope.padded_job_window(start, None, now=now)
  assert w2 == start - timedelta(hours=1)
  assert w3 == now + timedelta(hours=1)


def test_build_acct_host_fqdns_avoids_duplicate_suffix(monkeypatch):
  monkeypatch.setattr(
      jid_scope.cfg, "get_host_name_ext", lambda: ".cluster.example",
  )
  hosts = jid_scope.build_acct_host_fqdns(
      ["c001", "c002.cluster.example", "c001"],
  )
  assert hosts == ["c001.cluster.example", "c002.cluster.example"]


def test_resolve_job_ingest_scope_missing_and_empty_hosts(monkeypatch):
  import hpcperfstats.site.lib.machine.models as models

  class _JD:
    DoesNotExist = type("DoesNotExist", (Exception,), {})

    class objects:
      @staticmethod
      def only(*_a):
        class _Q:
          def get(self, jid):
            raise _JD.DoesNotExist()

        return _Q()

  monkeypatch.setattr(models, "job_data", _JD)
  with pytest.raises(jid_scope.JobIngestScopeError, match="not found"):
    jid_scope.resolve_job_ingest_scope("missing")

  class _EmptyHosts:
    DoesNotExist = type("DoesNotExist", (Exception,), {})

    class objects:
      @staticmethod
      def only(*_a):
        class _Q:
          def get(self, jid):
            return SimpleNamespace(
                jid=jid,
                host_list=[],
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            )

        return _Q()

  monkeypatch.setattr(models, "job_data", _EmptyHosts)
  monkeypatch.setattr(
      jid_scope.cfg, "get_host_name_ext", lambda: ".ex",
  )
  with pytest.raises(jid_scope.JobIngestScopeError, match="empty host_list"):
    jid_scope.resolve_job_ingest_scope("j1")


def test_expand_neighbors_with_in_window_core():
  start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
  end = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
  host_dir = "/archive/c001.ex"
  e_before = int(start.timestamp()) - 100
  e_core0 = int(start.timestamp()) + 100
  e_core1 = int(end.timestamp()) - 100
  e_after = int(end.timestamp()) + 100
  e_far = int(end.timestamp()) + 10_000
  items = [
      (_rec(host_dir, e_before, 1), e_before),
      (_rec(host_dir, e_core0, 2), e_core0),
      (_rec(host_dir, e_core1, 3), e_core1),
      (_rec(host_dir, e_after, 4), e_after),
      (_rec(host_dir, e_far, 5), e_far),
  ]
  selected = expand_sorted_records_with_window_neighbors(items, start, end)
  assert [int(os.path.basename(r.path)) for r in selected] == [
      e_before, e_core0, e_core1, e_after,
  ]


def test_expand_neighbors_empty_core_nearest_outside():
  start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
  end = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
  host_dir = "/archive/c001.ex"
  e_before = int(start.timestamp()) - 500
  e_earlier = int(start.timestamp()) - 50
  e_after = int(end.timestamp()) + 50
  e_later = int(end.timestamp()) + 500
  items = [
      (_rec(host_dir, e_before, 1), e_before),
      (_rec(host_dir, e_earlier, 2), e_earlier),
      (_rec(host_dir, e_after, 3), e_after),
      (_rec(host_dir, e_later, 4), e_later),
  ]
  selected = expand_sorted_records_with_window_neighbors(items, start, end)
  assert [int(os.path.basename(r.path)) for r in selected] == [
      e_earlier, e_after,
  ]


def test_expand_neighbors_edges_sole_file_and_no_before_after():
  start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
  end = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
  host_dir = "/archive/c001.ex"
  e_only = int(start.timestamp()) + 60
  sole = expand_sorted_records_with_window_neighbors(
      [(_rec(host_dir, e_only), e_only)], start, end,
  )
  assert [r.path for r in sole] == ["%s/%d" % (host_dir, e_only)]

  e_core = int(start.timestamp()) + 60
  no_before = expand_sorted_records_with_window_neighbors(
      [(_rec(host_dir, e_core, 1), e_core)], start, end,
  )
  assert len(no_before) == 1

  empty = expand_sorted_records_with_window_neighbors([], start, end)
  assert empty == []


def test_filter_host_scoped_window_records_skips_and_adds_neighbors():
  start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
  end = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
  host = "c001.ex"
  host_dir = "/archive/%s" % host
  e_before = int(start.timestamp()) - 30
  in_epoch = int(start.timestamp()) + 1800
  e_after = int(end.timestamp()) + 30
  e_far = int(end.timestamp()) + 3600
  records = [
      _rec(host_dir, e_before, 10),
      _rec(host_dir, in_epoch, 1),
      _rec(host_dir, e_after, 2),
      _rec(host_dir, e_far, 6),
      FindStatsRecord(
          path="%s/%d.fnctl.lock" % (host_dir, in_epoch),
          mtime=float(in_epoch),
          size=1,
          inode=3,
      ),
      FindStatsRecord(
          path="%s/current" % host_dir,
          mtime=float(in_epoch),
          size=1,
          inode=4,
      ),
      _rec("/archive/other.ex", in_epoch, 5),
  ]
  filtered = filter_host_scoped_window_records(
      records, [host], start, end, current_inodes={},
  )
  assert [os.path.basename(r.path) for r in filtered] == [
      str(e_before), str(in_epoch), str(e_after),
  ]


def test_filter_host_scoped_neighbors_independent_per_host():
  start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
  end = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
  h1, h2 = "c001.ex", "c002.ex"
  d1, d2 = "/archive/%s" % h1, "/archive/%s" % h2
  e_core = int(start.timestamp()) + 100
  e_before_h1 = int(start.timestamp()) - 10
  e_before_h2 = int(start.timestamp()) - 20
  e_after_h1 = int(end.timestamp()) + 10
  records = [
      _rec(d1, e_before_h1, 1),
      _rec(d1, e_core, 2),
      _rec(d1, e_after_h1, 3),
      _rec(d2, e_before_h2, 4),
      _rec(d2, e_core, 5),
  ]
  filtered = filter_host_scoped_window_records(
      records, [h1, h2], start, end, current_inodes={},
  )
  by_host = {}
  for r in filtered:
    by_host.setdefault(os.path.basename(os.path.dirname(r.path)), []).append(
        int(os.path.basename(r.path)),
    )
  assert by_host[h1] == [e_before_h1, e_core, e_after_h1]
  assert by_host[h2] == [e_before_h2, e_core]


def test_run_sync_timedb_jid_ingest_no_archive_or_janitor(monkeypatch, tmp_path):
  """jid path must not start ArchiveJanitor / archive pool / supervisor loop."""
  archive = tmp_path / "archive"
  archive.mkdir()
  host = "c001.example"
  host_dir = archive / host
  host_dir.mkdir()
  start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
  end = datetime(2026, 7, 1, 13, 0, 0, tzinfo=timezone.utc)
  epoch = int(start.timestamp()) + 60
  stats_path = host_dir / str(epoch)
  stats_path.write_text("x\n", encoding="utf-8")

  scope = jid_scope.JobIngestScope(
      jid="42",
      hosts=(host,),
      window_start=start - timedelta(hours=1),
      window_end=end + timedelta(hours=1),
      start_time=start,
      end_time=end,
  )
  monkeypatch.setattr(st, "resolve_job_ingest_scope", scope, raising=False)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_jid_scope.resolve_job_ingest_scope",
      lambda jid, **k: scope,
  )
  monkeypatch.setattr(st.cfg, "get_host_name_ext", lambda: ".example")
  monkeypatch.setattr(st.cfg, "get_archive_dir_path", lambda: str(archive))
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_persistence.ensure_persistence_contract",
      lambda *a, **k: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.load_checkpoint_path_set",
      lambda *_a, **_k: set(),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_stats_find.collect_host_scoped_stats_paths",
      lambda *a, **k: [str(stats_path)],
  )

  called = {"archive_pool": False, "supervisor": False, "janitor": False}

  def _boom_supervisor(*a, **k):
    called["supervisor"] = True
    raise AssertionError("supervisor must not run for --jid")

  monkeypatch.setattr(st, "run_sync_timedb_queue_orchestrator", _boom_supervisor)
  if hasattr(st, "create_sync_timedb_thread_pool"):
    monkeypatch.setattr(
        st,
        "create_sync_timedb_thread_pool",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("archive pool must not start"),
        ),
    )

  ingest_calls = []

  def _fake_ingest(lock, path, stats_file_contents=None):
    ingest_calls.append(path)
    assert st.should_archive is False
    return (path, False, True, 0.01, {"outcome": "ingested"})

  monkeypatch.setattr(st, "add_stats_file_to_db", _fake_ingest)
  monkeypatch.setattr(st, "_load_sync_checkpoint", lambda *_a, **_k: [])
  saved = []

  def _save(path, entries):
    saved.append((path, list(entries)))

  monkeypatch.setattr(st, "_save_sync_checkpoint", _save)

  code = st.run_sync_timedb_jid_ingest("42")
  assert code == 0
  assert ingest_calls == [str(stats_path)]
  assert saved
  assert called["supervisor"] is False
  assert st.should_archive is True  # restored


def test_need_archival_skips_when_should_archive_false(monkeypatch):
  monkeypatch.setattr(st, "should_archive", False)
  need, meta = st._need_archival_and_archive_skip_meta("/a/b/1", None)
  assert need is False
  assert meta.get("archive_skip") == "should_archive_false"
