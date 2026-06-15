"""Tests for sync_timedb DB sampled-timestamp readiness gate."""
from datetime import datetime, timezone

import pytest

import hpcperfstats.conf_parser as cfg
import hpcperfstats.dbload.sync_timedb_host_itimes as host_itimes
import hpcperfstats.dbload.sync_timedb_ingest_readiness as readiness
import hpcperfstats.dbload.sync_timedb_parsing as parsing
from hpcperfstats.site.machine.models import host_data


@pytest.fixture(autouse=True)
def _clear_readiness_caches():
  readiness.reset_sync_ingest_readiness_caches()
  host_itimes.reset_host_itimes_caches()
  yield
  readiness.reset_sync_ingest_readiness_caches()
  host_itimes.reset_host_itimes_caches()


def _write_stats_segment(path, host, base_ts, extra_timestamp_lines=0):
  path.parent.mkdir(parents=True, exist_ok=True)
  lines = ["%d job0 %s\n" % (base_ts, host)]
  for i in range(1, extra_timestamp_lines + 1):
    lines.append("%d job%d %s\n" % (base_ts + i, i, host))
  lines.append("block dev 1 2 3\n")
  path.write_text("".join(lines))
  return str(path)


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
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", _head_present)
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
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", lambda _h, _s: False)
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
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", lambda _h, _s: True)
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True


def test_head_timestamp_present_matches_subsecond_rows_in_same_second(monkeypatch):
  """Monitor head lines use fractional seconds; DB rows keep subsecond time."""
  ts_line = 1773864970.470903
  ts_sec = int(ts_line)
  head_second = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
  stored_time = datetime.fromtimestamp(ts_line, tz=timezone.utc)
  seen = {}

  class _QS:
    def exists(self):
      return bool(seen.get("in_window"))

  class _Mgr:
    def filter(self, *, host, time__gte=None, time__lt=None, time=None, **kwargs):
      del host, kwargs
      if time is not None:
        seen["exact"] = time
        seen["in_window"] = time == stored_time
      else:
        seen["in_window"] = time__gte <= stored_time < time__lt
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  assert readiness.head_timestamp_present_in_db("c571-001.stampede3.tacc.utexas.edu", head_second)
  assert seen.get("in_window") is True
  assert "exact" not in seen


def test_stats_file_head_ingested_fractional_head_line_after_subsecond_ingest(
    monkeypatch, tmp_path,
):
  arch_suffix = "cluster.readiness.test"
  host_dir = tmp_path / ("n." + arch_suffix)
  host_dir.mkdir()
  ts_line = 1773864970.470903
  seg = host_dir / str(int(ts_line))
  seg.write_text("%f job1 cn001\nblock dev 1 2 3\n" % ts_line)

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", lambda _h, _s: True)
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
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", lambda h, _s: h == short_host)
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True


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


def test_stats_file_head_ingested_in_db_closes_connections(monkeypatch, tmp_path):
  close_calls = []

  class _FakeSyncWorkerDbTask:
    def __enter__(self):
      close_calls.append("enter")
      return self

    def __exit__(self, exc_type, exc, tb):
      close_calls.append("exit")
      return False

  seg = tmp_path / "seg"
  seg.write_text("not-a-stats-file\n")
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: False)
  import hpcperfstats.dbload.sync_timedb as sync_timedb
  monkeypatch.setattr(sync_timedb, "_sync_worker_db_task", lambda: _FakeSyncWorkerDbTask())
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True
  assert close_calls == ["enter", "exit"]


def test_gate_sample_stride_tracks_bulk_create_batch_size(monkeypatch):
  monkeypatch.setattr(cfg, "get_sync_bulk_create_batch_size", lambda: 10000)
  assert cfg.get_sync_archive_db_ingest_gate_sample_stride() == 5000
  monkeypatch.setattr(cfg, "get_sync_bulk_create_batch_size", lambda: 3)
  assert cfg.get_sync_archive_db_ingest_gate_sample_stride() == 1


def test_collect_sampled_timestamps_stride_and_last(tmp_path):
  seg = tmp_path / "seg"
  host = "cn001"
  base = 1_700_000_000
  lines = []
  for i in range(6002):
    lines.append("%d job%d %s\n" % (base + i, i, host))
  lines.append("block\n")
  seg.write_text("".join(lines))
  sampled = parsing.collect_stats_file_sampled_timestamp_identities_streaming(
      str(seg),
      sample_stride=5000,
  )
  assert sampled[host] == {base, base + 5000, base + 6001}


def test_collect_sampled_timestamps_last_on_stride_grid(tmp_path):
  seg = tmp_path / "seg"
  host = "cn001"
  base = 1_700_000_000
  lines = ["%d job0 %s\n" % (base, host), "block\n"]
  seg.write_text("".join(lines))
  sampled = parsing.collect_stats_file_sampled_timestamp_identities_streaming(
      str(seg),
      sample_stride=5000,
  )
  assert sampled[host] == {base}


def test_gate_false_when_sampled_second_missing(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  seg = host_dir / "seg"
  base = 1_700_000_000
  _write_stats_segment(seg, "cn001", base, extra_timestamp_lines=5001)
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(cfg, "get_sync_archive_db_ingest_gate_sample_stride", lambda: 5000)
  missing = base + 5000

  def _present(host, seconds):
    del host
    return missing not in seconds

  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", _present)
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is False


def test_gate_true_when_gap_between_samples_but_samples_present(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  seg = host_dir / "seg"
  _write_stats_segment(seg, "cn001", 1_700_000_000, extra_timestamp_lines=2500)
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", lambda _h, _s: True)
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True


def test_gate_false_when_last_timestamp_missing(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  seg = host_dir / "seg"
  base = 1_700_000_000
  _write_stats_segment(seg, "cn001", base, extra_timestamp_lines=6001)
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(cfg, "get_sync_archive_db_ingest_gate_sample_stride", lambda: 5000)
  last_second = base + 6001

  def _present(host, seconds):
    return last_second not in seconds

  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", _present)
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is False


def test_host_sampled_batch_uses_single_range_query(monkeypatch):
  query_calls = {"n": 0}

  class _QS:
    def distinct(self):
      return self

    def values_list(self, *_args, **_kwargs):
      return self

    def iterator(self):
      return iter([])

  class _Mgr:
    def filter(self, **_kwargs):
      query_calls["n"] += 1
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  seconds = {100, 200, 300}
  assert host_itimes.host_sampled_timestamp_seconds_all_present("cn001", seconds) is False
  assert query_calls["n"] == 1


def test_host_sampled_overflow_falls_back_to_per_second_exists(monkeypatch):
  monkeypatch.setattr(
      host_itimes,
      "host_recent_timestamps_cached",
      lambda *_a, **_k: host_itimes.HOST_ITIMES_SET_OVERFLOW,
  )
  exists_calls = {"n": 0}

  def _exists(host, unix_second):
    del host
    exists_calls["n"] += 1
    return True

  monkeypatch.setattr(host_itimes, "host_timestamp_second_present_in_db", _exists)
  assert host_itimes.host_sampled_timestamp_seconds_all_present("cn001", {100, 200}) is True
  assert exists_calls["n"] == 2


def test_build_head_ingest_ready_set_one_batch_probe_per_host(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  paths = [
      _write_stats_segment(host_dir / "a", "cn001", 1_700_000_000),
      _write_stats_segment(host_dir / "b", "cn001", 1_700_000_001),
  ]
  sampled = {
      paths[0]: {"cn001": {1_700_000_000}},
      paths[1]: {"cn001": {1_700_000_001}},
  }
  calls = {"n": 0}

  def _all_present(host, seconds):
    del host, seconds
    calls["n"] += 1
    return True

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", _all_present)
  ready = readiness.build_head_ingest_ready_set(paths, sampled, log_fn=None)
  assert paths[0] in ready
  assert paths[1] in ready
  assert calls["n"] == 1
