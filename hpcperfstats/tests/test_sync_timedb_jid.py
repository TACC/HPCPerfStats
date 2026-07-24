"""Unit tests for sync_timedb --jid ingest-only helpers and entry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hpcperfstats.dbload.lib import sync_timedb_jid_scope as jid_scope
from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
    FindStatsRecord,
    filter_host_scoped_window_records,
)
import hpcperfstats.dbload.sync_timedb as st


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
      ["sync_timedb.py", "--jid", "1", "backlog"],
  )
  assert jid is None
  assert "cannot be combined" in err
  jid2, err2 = jid_scope.parse_sync_timedb_jid_cli_arg(
      ["sync_timedb.py", "--jid", ""],
  )
  assert jid2 is None
  assert "empty" in err2
  jid3, err3 = jid_scope.parse_sync_timedb_jid_cli_arg(
      ["sync_timedb.py", "backlog"],
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


def test_filter_host_scoped_window_records_skips_outside_and_locks():
  start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
  end = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
  host = "c001.ex"
  host_dir = "/archive/%s" % host
  in_epoch = int(start.timestamp()) + 1800
  out_epoch = int(end.timestamp()) + 3600
  records = [
      FindStatsRecord(
          path="%s/%d" % (host_dir, in_epoch),
          mtime=float(in_epoch),
          size=10,
          inode=1,
      ),
      FindStatsRecord(
          path="%s/%d" % (host_dir, out_epoch),
          mtime=float(out_epoch),
          size=10,
          inode=2,
      ),
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
      FindStatsRecord(
          path="/archive/other.ex/%d" % in_epoch,
          mtime=float(in_epoch),
          size=10,
          inode=5,
      ),
  ]
  filtered = filter_host_scoped_window_records(
      records, [host], start, end, current_inodes={},
  )
  assert [r.path for r in filtered] == ["%s/%d" % (host_dir, in_epoch)]


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

  monkeypatch.setattr(st, "run_sync_timedb_supervisor_loop", _boom_supervisor)
  monkeypatch.setattr(
      st,
      "create_sync_timedb_spawn_pool",
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
