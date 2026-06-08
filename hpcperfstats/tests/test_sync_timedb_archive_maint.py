"""Tests for archive maintenance snapshot, hints, and parallel head metadata."""
from datetime import datetime, timezone
import json
import os

import pytest

import hpcperfstats.conf_parser as cfg
import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers
import hpcperfstats.dbload.sync_timedb_archive_maint as maint
import hpcperfstats.dbload.sync_timedb_ingest_readiness as readiness


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

  monkeypatch.setenv("SYNC_ARCHIVE_DISCOVERY_WORKERS", "2")
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
  import shutil
  import subprocess
  import tarfile

  from hpcperfstats.dbload.sync_timedb_archive_helpers import atomic_seal_tar_to_zst
  from hpcperfstats.dbload.zstd_cli import zstd_executable

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
  head_identity = {
      paths[0]: ("cn001", ts),
      paths[1]: ("cn001", ts),
  }
  calls = {"n": 0}

  def _present(hostname, timestamp_utc):
    del hostname, timestamp_utc
    calls["n"] += 1
    return True

  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(readiness, "head_timestamp_present_in_db", _present)
  ready = readiness.build_head_ingest_ready_set(paths, head_identity, log_fn=None)
  assert paths[0] in ready
  assert paths[1] in ready
  assert calls["n"] == 1


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
