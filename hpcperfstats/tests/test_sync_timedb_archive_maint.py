"""Tests for archive maintenance snapshot, hints, and parallel head metadata."""
from datetime import datetime, timezone

import pytest

import hpcperfstats.dbload.lib.conf_parser as cfg
import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
import hpcperfstats.dbload.lib.sync_timedb_archive_maint as maint
import hpcperfstats.dbload.lib.sync_timedb_ingest_readiness as readiness


@pytest.fixture(autouse=True)
def _clear_readiness_caches():
  readiness.reset_sync_ingest_readiness_caches()
  yield
  readiness.reset_sync_ingest_readiness_caches()


def _write_stats_segment(host_dir, epoch, host_token="cn001"):
  host_dir.mkdir(parents=True, exist_ok=True)
  seg = host_dir / str(epoch)
  seg.write_text("%d job1 %s\npayload\n" % (epoch, host_token))
  return str(seg)


def test_collect_head_metadata_parallel_matches_serial(monkeypatch, tmp_path):
  arch_suffix = "cluster.maint.parallel"
  host = tmp_path / ("n." + arch_suffix)
  paths = []
  for epoch in (1700000100, 1700000200, 1700000300):
    paths.append(_write_stats_segment(host, epoch))

  serial_first = helpers.collect_first_timestamps_by_path(paths)
  serial_identity = {}
  for path in paths:
    host_name, ts = helpers.read_stats_file_head_identity(path)
    if host_name and ts is not None:
      serial_identity[path] = (host_name, int(ts.timestamp()))

  monkeypatch.setattr(cfg, "get_sync_pool_process_cap", lambda: 2)
  parallel_first, parallel_identity, _stats = maint.collect_head_metadata_for_paths(
      paths, hints_data=None, log_fn=None)

  assert parallel_first == serial_first
  assert parallel_identity == serial_identity


def test_maint_hints_skip_reread_when_unchanged(monkeypatch, tmp_path):
  arch_suffix = "cluster.maint.hints"
  host = tmp_path / ("n." + arch_suffix)
  path = _write_stats_segment(host, 1700000400)
  fp = maint._path_fingerprint(path)
  assert fp is not None
  hints = {
      "version": 1,
      "host_dirs": {
          str(host): {"mtime": maint._host_dir_fingerprint(str(host))[0], "file_count": 1},
      },
      "paths": {
          path: {
              "mtime": fp[0],
              "size": fp[1],
              "first_ts": "1700000400",
              "host": "cn001",
              "unix_second": 1700000400,
          },
      },
      "validated_days": {},
  }
  read_paths = []
  real_read = helpers.read_stats_file_head_identity

  def _track_read(p, *a, **k):
    read_paths.append(p)
    return real_read(p, *a, **k)

  monkeypatch.setattr(helpers, "read_stats_file_head_identity", _track_read)
  first_ts, head_id, stats = maint.collect_head_metadata_for_paths(
      [path], hints_data=hints, log_fn=None)
  assert first_ts[path] == "1700000400"
  assert head_id[path] == ("cn001", 1700000400)
  assert stats["read"] == 0
  assert read_paths == []


def test_save_and_load_archive_maint_hints_roundtrip(tmp_path, monkeypatch):
  monkeypatch.setattr(cfg, "get_sync_archive_maint_hints", lambda: True)
  archive_dir = str(tmp_path)
  maint.save_archive_maint_hints(
      archive_dir,
      host_dirs={"/h/host": {"mtime": 1, "file_count": 2}},
      paths={"/h/host/1": {"mtime": 2, "size": 3, "first_ts": "9", "host": "h", "unix_second": 9}},
      validated_days={"/z/day.tar.zst": {"mtime_ns": 1, "size": 2, "ok": True, "member_count": 1, "member_byte_sum": 10}},
  )
  loaded = maint.load_archive_maint_hints(archive_dir)
  assert loaded["version"] == 2
  assert "/h/host" in loaded["host_dirs"]
  assert "/z/day.tar.zst" in loaded["validated_days"]


@pytest.mark.skipif(not __import__("shutil").which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_skips_when_tar_and_zst_equivalent(tmp_path):
  import subprocess
  import tarfile

  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import atomic_seal_tar_to_zst
  from hpcperfstats.dbload.lib.zstd_cli import zstd_executable

  tar_path = tmp_path / "2021-04-01.tar"
  zst_path = tmp_path / "2021-04-01.tar.zst"
  member = tmp_path / "m.txt"
  member.write_text("data")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="m.txt")
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  first_size = zst_path.stat().st_size
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert zst_path.stat().st_size == first_size
  subprocess.run(
      [zstd_executable(), "-t", "-T1", "-q", str(zst_path)],
      check=True,
  )


def test_build_head_ingest_ready_set_dedupes_db_lookups(monkeypatch, tmp_path):
  arch_suffix = "cluster.maint.gate"
  host_dir = tmp_path / ("n." + arch_suffix)
  ts = int(datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
  paths = [
      _write_stats_segment(host_dir, ts),
      _write_stats_segment(host_dir, ts + 1),
  ]
  sampled = {
      paths[0]: {"cn001": {ts}},
      paths[1]: {"cn001": {ts}},
  }
  calls = {"n": 0}

  def _all_present(host, seconds):
    del host, seconds
    calls["n"] += 1
    return True

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_ingest", lambda: True)
  monkeypatch.setattr(readiness, "host_timestamp_seconds_all_present", _all_present)
  ready = readiness.build_head_ingest_ready_set(paths, sampled, log_fn=None)
  assert paths[0] in ready
  assert paths[1] in ready
  assert calls["n"] == 1


def test_build_archive_maintenance_snapshot_skips_gate_when_build_ready_set_false(
    monkeypatch, tmp_path,
):
  arch_suffix = "cluster.maint.skip_gate"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir(parents=True, exist_ok=True)
  seg_path = host / "1700000500"
  seg_path.write_text("1700000500 job1 cn001\npayload\n")
  tgz = tmp_path / "daily"
  tgz.mkdir()
  gate_calls = {"n": 0}

  def _forbidden_gate(*args, **kwargs):
    gate_calls["n"] += 1
    raise AssertionError("gate collect must not run when build_ready_set=False")

  monkeypatch.setattr(cfg, "get_sync_archive_maint_hints", lambda: False)
  monkeypatch.setattr(maint, "collect_gate_identities_for_paths", _forbidden_gate)
  snap = maint.build_archive_maintenance_snapshot(
      str(tmp_path),
      arch_suffix,
      str(tgz),
      build_ready_set=False,
      log_fn=None,
  )
  assert gate_calls["n"] == 0
  assert snap.gate_identities_by_path == {}
  assert str(seg_path) in snap.closed_paths


def test_build_archive_maintenance_snapshot_collects_head_tail_gate_identities(
    monkeypatch, tmp_path,
):
  arch_suffix = "cluster.maint.head_tail"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir(parents=True, exist_ok=True)
  seg_path = host / "1700000500"
  seg_path.write_text(
      "1700000500 job1 cn001\n"
      "1700000501 job2 cn001\n"
      "1700000502 job3 cn001\n"
      "payload\n"
  )
  seg = str(seg_path)
  tgz = tmp_path / "daily"
  tgz.mkdir()
  gate_calls = {"n": 0}
  real_gate = maint.collect_gate_identities_for_paths

  def _counting_gate(*args, **kwargs):
    gate_calls["n"] += 1
    return real_gate(*args, **kwargs)

  monkeypatch.setattr(cfg, "get_sync_archive_maint_hints", lambda: False)
  monkeypatch.setattr(maint, "collect_gate_identities_for_paths", _counting_gate)
  monkeypatch.setattr(
      readiness,
      "build_head_ingest_ready_set",
      lambda closed, _identities, **kw: set(),
  )
  snap = maint.build_archive_maintenance_snapshot(
      str(tmp_path), arch_suffix, str(tgz), log_fn=None)
  assert gate_calls["n"] == 1
  assert seg in snap.closed_paths
  assert snap.gate_identities_by_path[seg] == {
      "cn001": {1700000500, 1700000502},
  }
  assert not hasattr(maint, "collect_sampled_timestamp_identities_for_paths")


def test_build_archive_maintenance_snapshot_once_collects(monkeypatch, tmp_path):
  arch_suffix = "cluster.maint.snap"
  host = tmp_path / ("n." + arch_suffix)
  seg = _write_stats_segment(host, 1700000500)
  tgz = tmp_path / "daily"
  tgz.mkdir()
  collect_calls = {"n": 0}
  real_collect = helpers.collect_stats_files_in_range

  def _counting_collect(*args, **kwargs):
    collect_calls["n"] += 1
    return real_collect(*args, **kwargs)

  monkeypatch.setattr(maint, "collect_stats_files_in_range", _counting_collect)
  monkeypatch.setattr(cfg, "get_sync_archive_maint_hints", lambda: False)
  monkeypatch.setattr(
      readiness,
      "build_head_ingest_ready_set",
      lambda closed, head, **kw: set(),
  )
  snap = maint.build_archive_maintenance_snapshot(
      str(tmp_path), arch_suffix, str(tgz), log_fn=None)
  assert collect_calls["n"] == 1
  assert seg in snap.closed_paths
  assert seg in snap.first_timestamp_by_path


def test_archive_discovery_worker_count_uses_sync_pool_process_cap(monkeypatch):
  monkeypatch.setattr(cfg, "get_sync_pool_process_cap", lambda: 24)
  assert maint._get_archive_discovery_worker_count(100) == 24
  monkeypatch.setattr(cfg, "get_sync_pool_process_cap", lambda: 3)
  assert maint._get_archive_discovery_worker_count(2) == 2


def test_gate_tail_metadata_logs_begin_and_progress(monkeypatch):
  paths = ["/fake/path/%d" % i for i in range(5001)]
  head_identity = {path: ("cn001", 1700000000) for path in paths}
  logs = []

  def _fake_read(path):
    return path, "cn001", 1700000001

  monkeypatch.setattr(maint, "_read_tail_metadata_one", _fake_read)
  monkeypatch.setattr(cfg, "get_sync_pool_process_cap", lambda: 4)
  maint.collect_gate_identities_for_paths(
      paths,
      head_identity,
      log_fn=lambda msg, **kw: logs.append(msg),
  )
  joined = "\n".join(logs)
  assert "Gate tail metadata: begin" in joined
  assert "Gate tail metadata: progress" in joined
  assert "Gate tail metadata: paths=5001" in joined


def test_head_metadata_logs_begin_and_progress_for_large_read_set(monkeypatch):
  paths = ["/fake/path/%d" % i for i in range(5001)]
  logs = []

  def _fake_read(path):
    return path, "1700000000", "cn001", 1700000000

  monkeypatch.setattr(maint, "_read_head_metadata_one", _fake_read)
  monkeypatch.setattr(cfg, "get_sync_pool_process_cap", lambda: 4)
  maint.collect_head_metadata_for_paths(
      paths,
      hints_data=None,
      log_fn=lambda msg, **kw: logs.append(msg),
  )
  joined = "\n".join(logs)
  assert "Head metadata: begin" in joined
  assert "Head metadata: progress" in joined
  assert "Head metadata: paths=5001" in joined
