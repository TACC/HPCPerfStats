"""Tests for sync_timedb DB head+tail readiness gate."""
from datetime import datetime, timezone

import pytest

import hpcperfstats.dbload.lib.conf_parser as cfg
import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
import hpcperfstats.dbload.lib.sync_timedb_host_itimes as host_itimes
import hpcperfstats.dbload.lib.sync_timedb_ingest_readiness as readiness
import hpcperfstats.dbload.lib.sync_timedb_parsing as parsing
from hpcperfstats.site.lib.machine.models import host_data


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
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _head_present)
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True
  # Single-line file: head==tail second, one DB probe then path cache.
  assert calls["n"] == 1


def test_stats_file_head_ingested_false_without_db_row(monkeypatch, tmp_path):
  arch_suffix = "cluster.readiness.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", lambda _h, _t: False)
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
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", lambda _h, _t: True)
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True


def test_gate_false_when_head_present_tail_absent(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  base = 1_700_000_000
  seg = _write_stats_segment(host_dir / "seg", "cn001", base, extra_timestamp_lines=5)
  present = {base}

  def _present(hostname, timestamp_utc):
    del hostname
    return int(timestamp_utc.timestamp()) in present

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _present)
  assert readiness.stats_file_head_ingested_in_db(seg) is False


def test_gate_false_when_tail_present_head_absent(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  base = 1_700_000_000
  seg = _write_stats_segment(host_dir / "seg", "cn001", base, extra_timestamp_lines=5)
  present = {base + 5}

  def _present(hostname, timestamp_utc):
    del hostname
    return int(timestamp_utc.timestamp()) in present

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _present)
  assert readiness.stats_file_head_ingested_in_db(seg) is False


def test_gate_true_when_head_and_tail_present_distinct_seconds(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  base = 1_700_000_000
  seg = _write_stats_segment(host_dir / "seg", "cn001", base, extra_timestamp_lines=5)
  present = {base, base + 5}

  def _present(hostname, timestamp_utc):
    del hostname
    return int(timestamp_utc.timestamp()) in present

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _present)
  assert readiness.stats_file_head_ingested_in_db(seg) is True


def test_gate_true_single_line_head_equals_tail(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  base = 1_700_000_000
  seg = _write_stats_segment(host_dir / "seg", "cn001", base, extra_timestamp_lines=0)
  calls = {"n": 0}

  def _present(hostname, timestamp_utc):
    del hostname, timestamp_utc
    calls["n"] += 1
    return True

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _present)
  assert readiness.stats_file_head_ingested_in_db(seg) is True
  assert calls["n"] == 1


def test_tail_identity_uses_streaming_not_full_file_scan(monkeypatch, tmp_path):
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  base = 1_700_000_000
  seg = _write_stats_segment(host_dir / "seg", "cn001", base, extra_timestamp_lines=100)
  stream_calls = {"n": 0}
  real_stream = parsing.parse_last_timestamp_line_streaming

  def _counting_stream(path, **kwargs):
    stream_calls["n"] += 1
    return real_stream(path, **kwargs)

  monkeypatch.setattr(parsing, "parse_last_timestamp_line_streaming", _counting_stream)
  host, ts = helpers.read_stats_file_tail_identity(seg)
  assert host == "cn001"
  assert int(ts.timestamp()) == base + 100
  assert stream_calls["n"] == 1
  assert not hasattr(parsing, "collect_stats_file_sampled_timestamp_identities_streaming")


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
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", lambda _h, _t: True)
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

  def _head_present(hostname, timestamp_utc):
    del timestamp_utc
    return hostname == short_host

  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _head_present)
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


def test_filter_paths_head_identity_alone_does_not_batch_head_only(monkeypatch, tmp_path):
  """head_identity_by_path alone must not skip the per-path head+tail probe."""
  host_dir = tmp_path / "host.cluster"
  host_dir.mkdir()
  base = 1_700_000_000
  seg = _write_stats_segment(host_dir / "seg", "cn001", base, extra_timestamp_lines=3)
  present = {base}  # head only

  def _present(hostname, timestamp_utc):
    del hostname
    return int(timestamp_utc.timestamp()) in present

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _present)
  ready, skipped = readiness.filter_paths_head_ingested(
      [seg],
      log_fn=None,
      head_identity_by_path={seg: ("cn001", base)},
  )
  assert ready == []
  assert skipped == [seg]


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
  assert not hasattr(cfg, "get_sync_archive_db_ingest_gate_mode")
  assert not hasattr(cfg, "sync_archive_db_ingest_gate_uses_sample_mode")
  assert not hasattr(cfg, "get_sync_archive_db_ingest_gate_sample_stride")


def test_stats_file_head_ingested_in_db_closes_connections(monkeypatch, tmp_path):
  close_calls = []

  class _FakeSyncWorkerDbTask:
    def __enter__(self):
      close_calls.append("enter")
      return self

    def __exit__(self, _exc_type, exc, tb):
      close_calls.append("exit")
      return False

  seg = tmp_path / "seg"
  seg.write_text("not-a-stats-file\n")
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: False)
  import hpcperfstats.dbload.sync_timedb as sync_timedb
  monkeypatch.setattr(sync_timedb, "_sync_worker_db_task", lambda: _FakeSyncWorkerDbTask())
  assert readiness.stats_file_head_ingested_in_db(str(seg)) is True
  assert close_calls == ["enter", "exit"]


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
      _write_stats_segment(host_dir / "a", "cn001", 1_700_000_000, extra_timestamp_lines=2),
      _write_stats_segment(host_dir / "b", "cn001", 1_700_000_010, extra_timestamp_lines=2),
  ]
  gate = {
      paths[0]: {"cn001": {1_700_000_000, 1_700_000_002}},
      paths[1]: {"cn001": {1_700_000_010, 1_700_000_012}},
  }
  calls = {"n": 0}

  def _all_present(host, seconds):
    del host, seconds
    calls["n"] += 1
    return True

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", _all_present)
  ready = readiness.build_head_ingest_ready_set(paths, gate, log_fn=None)
  assert paths[0] in ready
  assert paths[1] in ready
  assert calls["n"] == 1


def test_head_tail_identity_as_gate_identities_merges_hosts():
  head = {"/p": ("cn001", 100)}
  tail = {"/p": ("cn002", 200)}
  gate = readiness.head_tail_identity_as_gate_identities(head, tail)
  assert gate["/p"] == {"cn001": {100}, "cn002": {200}}
