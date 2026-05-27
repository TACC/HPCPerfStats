"""Tests for sync_timedb DB head-timestamp readiness gate."""
from datetime import datetime, timezone

import pytest

import hpcperfstats.conf_parser as cfg
import hpcperfstats.dbload.sync_timedb_ingest_readiness as readiness
from hpcperfstats.site.machine.models import host_data


@pytest.fixture(autouse=True)
def _clear_readiness_caches():
  readiness.reset_sync_ingest_readiness_caches()
  yield
  readiness.reset_sync_ingest_readiness_caches()


def test_head_timestamp_cache_reuses_recent_lookup(monkeypatch):
  calls = {"n": 0}

  class _QS:
    def exists(self):
      calls["n"] += 1
      return True

  class _Mgr:
    def filter(self, **_kwargs):
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  ts = datetime.now(timezone.utc)
  assert readiness.head_timestamp_present_in_db("h1", ts)
  assert readiness.head_timestamp_present_in_db("h1", ts)
  assert calls["n"] == 1


def test_path_cache_reuses_recent_lookup(monkeypatch, tmp_path):
  arch_suffix = "cluster.readiness.test"
  host_dir = tmp_path / ("n." + arch_suffix)
  host_dir.mkdir()
  ts = int(datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc).timestamp())
  seg = host_dir / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  calls = {"n": 0}

  def _head_present(hostname, timestamp_utc):
    del hostname, timestamp_utc
    calls["n"] += 1
    return True

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _head_present)
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True
  assert calls["n"] == 1


def test_stats_file_head_ingested_false_without_db_row(monkeypatch, tmp_path):
  arch_suffix = "cluster.readiness.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)

  class _QS:
    def exists(self):
      return False

  class _Mgr:
    def filter(self, **_kwargs):
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is False


def test_stats_file_head_ingested_true_with_db_row(monkeypatch, tmp_path):
  arch_suffix = "cluster.readiness.test"
  host_dir = tmp_path / ("n." + arch_suffix)
  host_dir.mkdir()
  ts_dt = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
  ts = int(ts_dt.timestamp())
  seg = host_dir / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(
      readiness,
      "head_timestamp_present_in_db",
      lambda hostname, timestamp_utc: (
          hostname == "cn001" and timestamp_utc == ts_dt
      ),
  )
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True


def test_stats_file_head_ingested_uses_host_from_file_not_path_dirname(
    monkeypatch, tmp_path,
):
  """Regression: path dirname (FQDN) must not be used for host_data host lookup."""
  fqdn_dir = "c641-072.vista.tacc.utexas.edu"
  host_dir = tmp_path / fqdn_dir
  host_dir.mkdir()
  ts_dt = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
  ts = int(ts_dt.timestamp())
  seg = host_dir / str(ts)
  short_host = "c641-072"
  seg.write_text("%d job1 %s\nline\n" % (ts, short_host))

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  seen = {}

  class _QS:
    def exists(self):
      return seen.get("host") == short_host

  class _Mgr:
    def filter(self, *, host, time):
      seen["host"] = host
      seen["time"] = time
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True
  assert seen["host"] == short_host
  assert seen["host"] != fqdn_dir


def test_filter_paths_head_ingested_partitions(monkeypatch, tmp_path):
  a = tmp_path / "a"
  b = tmp_path / "b"
  a.write_text("1\n")
  b.write_text("2\n")
  monkeypatch.setattr(
      readiness,
      "stats_file_head_ingested_in_db",
      lambda path, **_: path == str(a),
  )
  ready, skipped = readiness.filter_paths_head_ingested([str(a), str(b)], log_fn=None)
  assert ready == [str(a)]
  assert skipped == [str(b)]


def test_gate_disabled_passes_without_db(monkeypatch, tmp_path):
  seg = tmp_path / "seg"
  seg.write_text("not-a-stats-file\n")
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: False)
  readiness.reset_sync_ingest_readiness_caches()
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True


def test_conf_parser_sync_archive_require_db_head_ingest_default(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  importlib.reload(cfg)
  assert cfg.get_sync_archive_require_db_head_ingest() is True

  with open(temp_ini) as fh:
    content = fh.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\nsync_archive_require_db_head_ingest = no",
  )
  with open(temp_ini, "w") as fh:
    fh.write(content)
  importlib.reload(cfg)
  assert cfg.get_sync_archive_require_db_head_ingest() is False
