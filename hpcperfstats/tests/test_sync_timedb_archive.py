"""Unit tests for sync_timedb archive helpers and main-block helpers (no Django)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.archive_compress import (
    DAILY_ARCHIVE_ZST_SUFFIX,
    archive_gz_members_contained_in_zst,
    daily_tar_path_from_compressed,
    normalize_daily_compressed_path,
)
from hpcperfstats.dbload.lib.zstd_cli import zstd_executable, zstd_gzip_supported
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    STREAM_ARCHIVE_TASK,
    atomic_seal_tar_to_zst,
    drop_legacy_gz_if_equivalent_to_zst,
    build_archive_mapping,
    build_remaining_raw_stats_by_daily_gz,
    build_day_close_disqualified_daily_tars,
    collect_days_with_unmapped_closed_raw,
    merge_maintenance_skip_daily_tar_paths,
    remaining_raw_by_gz_has_paths_on_disk,
    daily_tar_paths_for_stats_paths,
    daily_tar_seal_calendar_eligible,
    daily_tar_needs_day_close_work,
    raw_stats_path_needs_tar_append,
    raw_stats_path_tar_append_decision,
    ARCHIVE_SKIP_MEMBER_EXISTS,
    ARCHIVE_SKIP_MISSING_PATH,
    ARCHIVE_SKIP_ACTIVE_SEGMENT,
    daily_tar_paths_from_pending_archive_tasks,
    collect_lock_sidecar_stats,
    collect_stats_files_in_range,
    collect_sealed_daily_archive_paths_in_range,
    dedupe_tar_keep_largest_file_per_member,
    filter_files_to_add_to_archive,
    find_immediate_day_close_candidates,
    ingest_stream_past_calendar_day,
    stats_path_ingest_sort_epoch,
    get_existing_archive_members,
    get_existing_archive_members_for_daily_archive,
    get_file_member_sizes_from_gzip_archive,
    get_stats_chunk,
    get_tar_file_tasks,
    iter_archive_ingest_tasks,
    iter_sealed_daily_archive_member_lines,
    iter_tar_file_tasks,
    get_tar_member_name,
    get_verified_files_to_remove,
    collect_first_timestamps_by_path,
    daily_tar_path_in_maintenance_scope,
    daily_tar_paths_for_archive_job_tasks,
    is_daily_tar_sealed_dirty,
    parse_archive_date_from_daily_tar_path,
    remove_verified_archived_raw_files,
    remove_verified_uncompressed_daily_tars,
    replace_corrupt_tar_from_compressed_backup,
    rescan_pending_stats_files,
    resolve_preferred_archive_path_for_read,
    resolve_sealed_archive_path_for_ingest,
    seal_dirty_daily_archives,
    should_seal_daily_tar,
    stats_file_is_active_segment,
    validate_sealed_daily_archive_for_raw_removal,
    tar_has_duplicate_file_members,
    verify_tar_archive_readable,
)
from hpcperfstats.dbload.sync_timedb_archive import (
    _iter_stream_tasks_chunked,
    parse_sync_timedb_archive_argv,
)
import hpcperfstats.dbload.sync_timedb_archive as sta
from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.sync_timedb import archive_stats_files
from hpcperfstats.dbload.lib.sync_timedb_parsing import parse_first_timestamp_line
from hpcperfstats.dbload.lib.file_locking import LOCK_SUFFIX
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested


# --- get_tar_member_name ---


def test_get_tar_member_name_absolute_path():
  """Path with leading slash returns path without leading slash."""
  assert get_tar_member_name("/var/stats/cn001/123") == "var/stats/cn001/123"


def test_get_tar_member_name_relative_path():
  """Relative path unchanged (no leading slash)."""
  assert get_tar_member_name("var/stats/cn001/123") == "var/stats/cn001/123"


def test_get_tar_member_name_multiple_slashes():
  """Only leading slash is stripped."""
  assert get_tar_member_name("/a/b/c") == "a/b/c"


# --- resolve_preferred_archive_path_for_read ---


def test_resolve_preferred_archive_path_prefers_tar_when_both_exist(tmp_path):
  """When .tar and .tar.gz exist, read from uncompressed tar."""
  tar_p = tmp_path / "2020-01-01.tar"
  gz_p = tmp_path / "2020-01-01.tar.gz"
  tar_p.write_text("t")
  gz_p.write_text("z")
  assert resolve_preferred_archive_path_for_read(str(gz_p)) == str(tar_p)


def test_resolve_preferred_archive_path_keeps_gz_when_no_tar(tmp_path):
  """Only .tar.gz present: use gzip path."""
  gz_p = tmp_path / "2020-01-01.tar.gz"
  gz_p.write_text("z")
  assert resolve_preferred_archive_path_for_read(str(gz_p)) == str(gz_p)


def test_resolve_preferred_archive_path_plain_tar_unchanged(tmp_path):
  p = tmp_path / "2020-01-01.tar"
  p.write_text("t")
  assert resolve_preferred_archive_path_for_read(str(p)) == str(p)


# --- verify_tar_archive_readable / replace_corrupt_tar_from_compressed_backup ---


def test_verify_tar_archive_readable_accepts_valid_tar(tmp_path):
  tar_path = tmp_path / "ok.tar"
  inner = tmp_path / "inner.txt"
  inner.write_text("hello")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(inner), arcname="inner.txt")
  assert verify_tar_archive_readable(str(tar_path))


def test_verify_tar_archive_readable_rejects_truncated(tmp_path):
  tar_path = tmp_path / "bad.tar"
  inner = tmp_path / "z.txt"
  inner.write_text("abcdefghij" * 500)
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(inner), arcname="z.txt")
  raw = tar_path.read_bytes()
  tar_path.write_bytes(raw[:200])
  assert not verify_tar_archive_readable(str(tar_path))


def test_verify_tar_archive_readable_rejects_garbage(tmp_path):
  tar_path = tmp_path / "garbage.tar"
  tar_path.write_bytes(b"not a tar archive" * 20)
  assert not verify_tar_archive_readable(str(tar_path))


def test_verify_tar_archive_readable_false_for_missing(tmp_path):
  assert not verify_tar_archive_readable(str(tmp_path / "nope.tar"))


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
@pytest.mark.skipif(not shutil.which("tar"), reason="tar not on PATH")
def test_verify_tar_archive_readable_accepts_valid_tgz_via_zstd_gzip_pipe(tmp_path):
  gz = tmp_path / "ok.tar.gz"
  inner = tmp_path / "inner.txt"
  inner.write_text("hello")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(inner), arcname="inner.txt")
  assert verify_tar_archive_readable(str(gz))


def test_verify_tar_gz_zstd_pipe_uses_helpers_thread_count(monkeypatch, tmp_path):
  """Regression: zstd -T matches ``get_archive_zstd_thread_count()``."""
  if not shutil.which("zstd") or not shutil.which("tar"):
    pytest.skip("need zstd and tar on PATH")
  if not zstd_gzip_supported():
    pytest.skip("zstd without gzip format support")
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz = tmp_path / "probe.tar.gz"
  inner = tmp_path / "x.txt"
  inner.write_text("z")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(inner), arcname="m")

  recorded = []
  orig_popen = subprocess.Popen

  def _wrap_popen(*args, **kwargs):
    cmd = list(args[0]) if args else list(kwargs.get("args", []))
    recorded.append(cmd)
    return orig_popen(*args, **kwargs)

  import hpcperfstats.dbload.lib.zstd_cli as zstd_cli

  monkeypatch.setattr(zstd_cli.subprocess, "Popen", _wrap_popen)
  monkeypatch.setattr(helpers, "get_archive_zstd_thread_count", lambda: 13)
  assert verify_tar_archive_readable(str(gz))
  zstd_cmds = [
      c for c in recorded
      if any("zstd" in part for part in c)
      and "-d" in c
      and "--format=gzip" in c
      and "-c" in c
  ]
  assert zstd_cmds, "expected zstd -d --format=gzip -c subprocess"
  assert "-T13" in zstd_cmds[0]


def test_get_file_member_sizes_from_gzip_uses_zstd_pipe_when_zstd_present(
    monkeypatch, tmp_path,
):
  if not shutil.which("zstd") or not zstd_gzip_supported():
    pytest.skip("zstd gzip support required")
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz = tmp_path / "sizes.tar.gz"
  inner = tmp_path / "body.txt"
  inner.write_text("zzz")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(inner), arcname="only")

  recorded = []
  orig_popen = subprocess.Popen

  def _wrap_popen(*args, **kwargs):
    cmd = list(args[0]) if args else list(kwargs.get("args", []))
    recorded.append(cmd)
    return orig_popen(*args, **kwargs)

  monkeypatch.setattr(helpers.subprocess, "Popen", _wrap_popen)
  monkeypatch.setattr(helpers, "get_archive_zstd_thread_count", lambda: 5)
  assert get_file_member_sizes_from_gzip_archive(str(gz)) == {"only": 3}
  zstd_cmds = [
      c for c in recorded
      if any("zstd" in part for part in c)
      and "-d" in c
      and "--format=gzip" in c
      and "-c" in c
  ]
  assert "-T5" in zstd_cmds[0]


def test_replace_corrupt_tar_from_compressed_backup_without_backup_removes_tar(tmp_path):
  tar_path = tmp_path / "2020-01-01.tar"
  tar_path.write_text("corrupt")
  zst, gz = str(tmp_path / "2020-01-01.tar.zst"), str(tmp_path / "2020-01-01.tar.gz")
  assert replace_corrupt_tar_from_compressed_backup(
      str(tar_path), zst, gz, 1)
  assert not tar_path.exists()


def test_replace_corrupt_tar_from_compressed_backup_restores_via_zstd(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-02.tar"
  zst_path = tmp_path / "2020-01-02.tar.zst"
  gz_path = tmp_path / "2020-01-02.tar.gz"
  tar_path.write_text("bad")
  zst_path.write_text("not-used")

  def _fake_decomp(compressed_path, out_tar_path, thread_count, *, remove_compressed=True):
    assert compressed_path == str(zst_path)
    assert out_tar_path == str(tar_path)
    inner = tmp_path / "inn.txt"
    inner.write_text("ok")
    with tarfile.open(str(tar_path), "w") as tf:
      tf.add(str(inner), arcname="only.txt")
    return True

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _fake_decomp)
  assert replace_corrupt_tar_from_compressed_backup(
      str(tar_path), str(zst_path), str(gz_path), 1)
  assert verify_tar_archive_readable(str(tar_path))


def test_replace_corrupt_tar_falls_back_to_gz_when_zst_bad(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-03.tar"
  zst_path = tmp_path / "2020-01-03.tar.zst"
  gz_path = tmp_path / "2020-01-03.tar.gz"
  tar_path.write_text("bad")
  zst_path.write_text("bad-zst")
  gz_path.write_text("good-gz-placeholder")

  calls = []

  def _fake_decomp(compressed_path, out_tar_path, thread_count, *, remove_compressed=True):
    del thread_count, remove_compressed
    calls.append(compressed_path)
    if str(compressed_path).endswith(".tar.zst"):
      return False
    inner = tmp_path / "inn.txt"
    inner.write_text("ok")
    with tarfile.open(str(out_tar_path), "w") as tf:
      tf.add(str(inner), arcname="only.txt")
    return True

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _fake_decomp)
  assert replace_corrupt_tar_from_compressed_backup(
      str(tar_path), str(zst_path), str(gz_path), 1)
  assert verify_tar_archive_readable(str(tar_path))
  assert str(zst_path) in calls
  assert str(gz_path) in calls


def test_replace_corrupt_tar_returns_false_when_zst_only_restore_fails(
    monkeypatch, tmp_path,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-03b.tar"
  zst_path = tmp_path / "2020-01-03b.tar.zst"
  gz_path = tmp_path / "2020-01-03b.tar.gz"
  tar_path.write_text("bad")
  zst_path.write_text("bad-zst")

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", lambda *a, **k: False)
  assert not replace_corrupt_tar_from_compressed_backup(
      str(tar_path), str(zst_path), str(gz_path), 1)
  assert not tar_path.exists()
  assert zst_path.is_file()


def test_seal_skip_rejects_same_aggregate_different_members(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-04.tar"
  zst_path = tmp_path / "2020-01-04.tar.zst"
  tar_path.write_text("tar")
  zst_path.write_text("zst")
  tar_members = {"a.txt": 10, "b.txt": 20}
  zst_members = {"c.txt": 30}

  monkeypatch.setattr(helpers, "zstd_test", lambda *a, **k: None)
  monkeypatch.setattr(
      helpers,
      "_sealed_archive_members_via_redis_or_scan",
      lambda path, **kwargs: (True, dict(zst_members)),
  )
  monkeypatch.setattr(helpers, "get_existing_archive_members", lambda path: dict(tar_members))
  skipped, members = helpers._seal_skip_existing_zst_equivalent(
      str(tar_path), str(zst_path), 0, log_fn=None)
  assert skipped is False
  assert members == zst_members


def test_atomic_seal_tar_to_zst_shrink_guard_one_zst_scan(monkeypatch, tmp_path):
  """Shrink guard must not rescan zst when skip_result already has member map."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  if not shutil.which("zstd"):
    pytest.skip("zstd not on PATH")

  tar_path = tmp_path / "2021-03-08.tar"
  zst_path = tmp_path / "2021-03-08.tar.zst"
  a = tmp_path / "a.txt"
  b = tmp_path / "b.txt"
  a.write_text("a")
  b.write_text("bb")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(a), arcname="a.txt")
    tf.add(str(b), arcname="b.txt")
  helpers.atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  first_size = zst_path.stat().st_size
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(a), arcname="a.txt")

  zst_members = {"a.txt": 1, "b.txt": 2}
  scan_calls = {"n": 0}
  real_scan = helpers._scan_compressed_archive_members_and_readable

  def counting_scan(path, **kwargs):
    scan_calls["n"] += 1
    return real_scan(path, **kwargs)

  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", counting_scan,
  )
  monkeypatch.setattr(
      helpers,
      "_sealed_archive_members_via_redis_or_scan",
      lambda *_a, **_k: (True, dict(zst_members)),
  )

  helpers.atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert zst_path.stat().st_size == first_size
  assert scan_calls["n"] == 0


def test_seal_skip_uses_redis_helper_not_local_scan(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-05.tar"
  zst_path = tmp_path / "2020-01-05.tar.zst"
  tar_path.write_text("tar")
  zst_path.write_text("zst")
  via_redis_calls = {"n": 0}
  scan_calls = {"n": 0}

  def _via_redis(path, **kwargs):
    via_redis_calls["n"] += 1
    return True, {"m.txt": 1}

  def _forbidden_scan(*_a, **_k):
    scan_calls["n"] += 1
    raise AssertionError("seal skip must use coordinated read, not direct scan")

  monkeypatch.setattr(helpers, "zstd_test", lambda *a, **k: None)
  monkeypatch.setattr(
      helpers, "_sealed_archive_members_via_redis_or_scan", _via_redis,
  )
  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", _forbidden_scan,
  )
  monkeypatch.setattr(
      helpers, "get_existing_archive_members", lambda _p: {"m.txt": 1},
  )
  skipped, _members = helpers._seal_skip_existing_zst_equivalent(
      str(tar_path), str(zst_path), 0, log_fn=None,
  )
  assert skipped is True
  assert via_redis_calls["n"] == 1
  assert scan_calls["n"] == 0


def test_seal_skip_redis_single_flight_cold(monkeypatch, tmp_path, _clear_daily_archive_members_cache):
  import subprocess
  import threading

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

  if not shutil.which("zstd"):
    pytest.skip("zstd not on PATH")

  tar_path = tmp_path / "2024-06-20.tar"
  zst_path = tmp_path / "2024-06-20.tar.zst"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(inner), arcname="host/raw")
  subprocess.run(
      [shutil.which("zstd"), "-q", "-f", str(tar_path), "-o", str(zst_path)],
      check=True,
  )
  tar_path.unlink()
  stream_calls = {"n": 0}
  stream_lock = threading.Lock()
  original_stream = helpers._stream_compressed_archive_members
  fake = FakeRedis()

  def _counting_stream(compressed_path, on_member=None, **kwargs):
    with stream_lock:
      stream_calls["n"] += 1
    return original_stream(compressed_path, on_member, **kwargs)

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 30,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      helpers, "_stream_compressed_archive_members", _counting_stream,
  )
  monkeypatch.setattr(helpers, "zstd_test", lambda *a, **k: None)

  errors = []
  results = []

  def _seal_skip_worker():
    try:
      results.append(
          helpers._seal_skip_existing_zst_equivalent(
              str(tar_path), str(zst_path), 0, log_fn=None,
          ),
      )
    except Exception as exc:
      errors.append(exc)

  def _lookup_worker():
    try:
      helpers.get_existing_archive_members_for_daily_archive(str(zst_path))
    except Exception as exc:
      errors.append(exc)

  threads = [
      threading.Thread(target=_seal_skip_worker),
      threading.Thread(target=_lookup_worker),
  ]
  for t in threads:
    t.start()
  for t in threads:
    t.join(timeout=10)
  assert not errors
  assert stream_calls["n"] == 1
  assert len(results) == 1


def test_seal_skip_propagates_redis_unavailable(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
  )

  tar_path = tmp_path / "2020-01-06.tar"
  zst_path = tmp_path / "2020-01-06.tar.zst"
  tar_path.write_text("tar")
  zst_path.write_text("zst")

  def _raise_unavailable(*_a, **_k):
    raise ArchiveMembersRedisUnavailableError("redis down")

  monkeypatch.setattr(helpers, "zstd_test", lambda *a, **k: None)
  monkeypatch.setattr(
      helpers, "_sealed_archive_members_via_redis_or_scan", _raise_unavailable,
  )
  with pytest.raises(ArchiveMembersRedisUnavailableError, match="redis down"):
    helpers._seal_skip_existing_zst_equivalent(
        str(tar_path), str(zst_path), 0, log_fn=None,
    )


def test_archive_stats_files_returns_false_when_corrupt_tar_restore_fails(monkeypatch, tmp_path):
  from hpcperfstats.dbload.sync_timedb import _archive_stats_files_body

  raw_dir = tmp_path / "host.hpc"
  raw_dir.mkdir()
  raw_file = raw_dir / "stats.txt"
  raw_file.write_text("1234567890 host event 1\n")
  gz_path = str(tmp_path / "daily" / "2020-01-05.tar.gz")
  os.makedirs(os.path.dirname(gz_path), exist_ok=True)
  tar_path = gz_path.replace(".tar.gz", ".tar")
  open(tar_path, "wb").write(b"corrupt")
  open(gz_path, "wb").write(b"bad")

  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb.filter_paths_head_ingested",
      lambda paths, log_fn=None: (paths, []),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb.verify_tar_archive_readable",
      lambda path: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb.replace_corrupt_tar_from_compressed_backup",
      lambda *a, **k: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb._append_to_tar",
      lambda *a, **k: None,
  )
  result = _archive_stats_files_body((gz_path, [str(raw_file)]))
  assert result is False


def test_archive_stats_files_returns_false_when_sealed_without_restored_tar(
    monkeypatch, tmp_path,
):
  """Sealed zst with failed restore must not raise through the archive pool worker."""
  from hpcperfstats.dbload.sync_timedb import _archive_stats_files_body

  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-02.tar.zst")
  tar_path = daily_tar_path_from_compressed(archive_key)
  open(tar_path, "wb").write(b"corrupt")
  open(archive_key, "wb").write(b"sealed-placeholder")

  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb.verify_tar_archive_readable",
      lambda path: False,
  )
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(
      helpers, "decompress_compressed_to_tar", lambda *a, **k: False)

  result = _archive_stats_files_body((archive_key, [str(raw_file)]))
  assert result is False
  assert raw_file.exists()


# --- parse_archive_date_from_daily_tar_path ---


def test_parse_archive_date_from_daily_tar_path_ok():
  assert parse_archive_date_from_daily_tar_path("/x/2020-06-15.tar") == date(2020, 6, 15)


def test_parse_archive_date_from_daily_tar_path_rejects_non_daily_name():
  assert parse_archive_date_from_daily_tar_path("/x/other.tar") is None


# --- drop_legacy_gz / gz vs zst member compare ---


def test_archive_gz_members_contained_in_zst_allows_extra_zst_files():
  gz_members = {"a.txt": 10, "b.txt": 20}
  zst_members = {"a.txt": 10, "b.txt": 20, "c.txt": 5}
  assert archive_gz_members_contained_in_zst(gz_members, zst_members)


def test_archive_gz_members_contained_in_zst_rejects_missing_or_wrong_size():
  gz_members = {"a.txt": 10}
  assert not archive_gz_members_contained_in_zst(gz_members, {})
  assert not archive_gz_members_contained_in_zst(gz_members, {"a.txt": 11})


def test_drop_legacy_gz_removes_when_zst_is_superset(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-01.tar.gz"
  zst_path = tmp_path / "2024-03-01.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")
  gz_members = {"a.txt": 10, "b.txt": 20}
  zst_members = {"a.txt": 10, "b.txt": 20, "c.txt": 5}

  def _scan(path, **_kwargs):
    if str(path).endswith(".tar.gz"):
      return True, dict(gz_members)
    return True, dict(zst_members)

  monkeypatch.setattr(helpers, "_scan_compressed_archive_members_and_readable", _scan)
  monkeypatch.setattr(
      helpers,
      "_sealed_archive_members_via_redis_or_scan",
      lambda path, **kwargs: _scan(path),
  )
  drop_legacy_gz_if_equivalent_to_zst(str(gz_path), str(zst_path), log_fn=None)
  assert not gz_path.exists()


def test_drop_legacy_gz_keeps_when_zst_missing_gz_member(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-02.tar.gz"
  zst_path = tmp_path / "2024-03-02.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")

  def _scan(path, **_kwargs):
    if str(path).endswith(".tar.gz"):
      return True, {"a.txt": 10, "b.txt": 20}
    return True, {"a.txt": 10}

  monkeypatch.setattr(helpers, "_scan_compressed_archive_members_and_readable", _scan)
  monkeypatch.setattr(
      helpers,
      "_sealed_archive_members_via_redis_or_scan",
      lambda path, **kwargs: _scan(path),
  )
  drop_legacy_gz_if_equivalent_to_zst(str(gz_path), str(zst_path), log_fn=None)
  assert gz_path.exists()


def test_drop_legacy_gz_keeps_when_zst_member_size_differs(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-03.tar.gz"
  zst_path = tmp_path / "2024-03-03.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")

  def _scan(path, **_kwargs):
    if str(path).endswith(".tar.gz"):
      return True, {"a.txt": 10, "b.txt": 20}
    return True, {"a.txt": 10, "b.txt": 21, "c.txt": 5}

  monkeypatch.setattr(helpers, "_scan_compressed_archive_members_and_readable", _scan)
  monkeypatch.setattr(
      helpers,
      "_sealed_archive_members_via_redis_or_scan",
      lambda path, **kwargs: _scan(path),
  )
  drop_legacy_gz_if_equivalent_to_zst(str(gz_path), str(zst_path), log_fn=None)
  assert gz_path.exists()


def test_drop_legacy_gz_uses_provided_zst_members_no_zst_scan(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-04.tar.gz"
  zst_path = tmp_path / "2024-03-04.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")
  zst_members = {"a.txt": 10, "b.txt": 20, "c.txt": 5}
  zst_scan_calls = {"n": 0}

  def _forbidden_zst(path, **kwargs):
    zst_scan_calls["n"] += 1
    return False, {}

  def _scan_gz_only(path, **kwargs):
    if str(path).endswith(".tar.gz"):
      return True, {"a.txt": 10, "b.txt": 20}
    raise AssertionError("zst must not scan when snapshot provided")

  monkeypatch.setattr(
      helpers, "_sealed_archive_members_via_redis_or_scan", _forbidden_zst,
  )
  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", _scan_gz_only,
  )
  drop_legacy_gz_if_equivalent_to_zst(
      str(gz_path),
      str(zst_path),
      log_fn=None,
      zst_members=zst_members,
  )
  assert zst_scan_calls["n"] == 0
  assert not gz_path.exists()


def test_compare_compressed_members_coordinated_zst_cold(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-06-14.tar.gz"
  zst_path = tmp_path / "2024-06-14.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")
  zst_calls = {"n": 0}

  def _sealed(path, **kwargs):
    zst_calls["n"] += 1
    return True, {"host/raw": 4, "extra": 5}

  def _scan(path, **kwargs):
    if str(path).endswith(".tar.gz"):
      return True, {"host/raw": 4}
    raise AssertionError("zst must use coordinated read, not direct scan")

  monkeypatch.setattr(
      helpers, "_sealed_archive_members_via_redis_or_scan", _sealed,
  )
  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", _scan,
  )
  contained, gz_members, zst_members = helpers.compare_compressed_archive_members(
      str(gz_path),
      str(zst_path),
  )
  assert contained is True
  assert gz_members == {"host/raw": 4}
  assert zst_members == {"host/raw": 4, "extra": 5}
  assert zst_calls["n"] == 1


def test_drop_legacy_propagates_redis_unavailable(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
  )

  gz_path = tmp_path / "2020-01-07.tar.gz"
  zst_path = tmp_path / "2020-01-07.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")

  def _raise_unavailable(*_a, **_k):
    raise ArchiveMembersRedisUnavailableError("redis down")

  monkeypatch.setattr(
      helpers, "_sealed_archive_members_via_redis_or_scan", _raise_unavailable,
  )
  with pytest.raises(ArchiveMembersRedisUnavailableError, match="redis down"):
    helpers.drop_legacy_gz_if_equivalent_to_zst(
        str(gz_path), str(zst_path), log_fn=None,
    )


def test_migrate_legacy_gz_drop_reuses_compare_snapshot(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-12.tar.gz"
  zst_path = tmp_path / "2024-03-12.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")
  compare_calls = {"n": 0}
  real_compare = helpers.compare_compressed_archive_members
  drop_snapshots = []

  def _scan(path, **kwargs):
    if str(path).endswith(".tar.gz"):
      return True, {"a.txt": 10}
    return True, {"a.txt": 10, "b.txt": 5}

  def counting_compare(*args, **kwargs):
    compare_calls["n"] += 1
    return real_compare(*args, **kwargs)

  def capturing_drop(gz, zst, log_fn=None, **kwargs):
    drop_snapshots.append(dict(kwargs))
    gz_path.unlink()

  monkeypatch.setattr(helpers, "_scan_compressed_archive_members_and_readable", _scan)
  monkeypatch.setattr(
      helpers,
      "_sealed_archive_members_via_redis_or_scan",
      lambda path, **kwargs: _scan(path),
  )
  monkeypatch.setattr(
      helpers, "compare_compressed_archive_members", counting_compare,
  )
  monkeypatch.setattr(
      helpers, "drop_legacy_gz_if_equivalent_to_zst", capturing_drop,
  )
  status = helpers.migrate_one_daily_legacy_gz(
      str(gz_path),
      zstd_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert status == helpers.MIGRATE_GZ_STATUS_DROPPED_ONLY
  assert compare_calls["n"] == 1
  assert drop_snapshots[0]["gz_members"] == {"a.txt": 10}
  assert drop_snapshots[0]["zst_members"] == {"a.txt": 10, "b.txt": 5}


# --- is_daily_tar_sealed_dirty / should_seal_daily_tar ---


def test_is_daily_tar_sealed_dirty_missing_zst(tmp_path):
  tar_p = tmp_path / "2020-01-01.tar"
  tar_p.write_text("x")
  zst_p = tmp_path / "2020-01-01.tar.zst"
  gz_p = tmp_path / "2020-01-01.tar.gz"
  assert is_daily_tar_sealed_dirty(str(tar_p), str(zst_p), str(gz_p))


def test_is_daily_tar_sealed_dirty_tar_newer_than_zst(tmp_path):
  tar_p = tmp_path / "2020-01-01.tar"
  zst_p = tmp_path / "2020-01-01.tar.zst"
  gz_p = tmp_path / "2020-01-01.tar.gz"
  tar_p.write_text("x")
  zst_p.write_text("y")
  old = datetime(2010, 1, 1).timestamp()
  os.utime(zst_p, (old, old))
  newer = datetime(2011, 1, 1).timestamp()
  os.utime(tar_p, (newer, newer))
  assert is_daily_tar_sealed_dirty(str(tar_p), str(zst_p), str(gz_p))


def test_should_seal_prior_calendar_day_without_waiting_idle(tmp_path, monkeypatch):
  """Dirty archive for a date before *today* seals even if idle_seconds is huge."""
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.time.time",
      lambda: 1_600_000_000.0,
  )
  tar_p = tmp_path / "2019-12-31.tar"
  gz_p = tmp_path / "2019-12-31.tar.gz"
  tar_p.write_text("t")
  gz_p.write_text("g")
  os.utime(tar_p, (1_600_000_000, 1_600_000_000))
  os.utime(gz_p, (1_500_000_000, 1_500_000_000))
  today = date(2020, 1, 5)
  assert should_seal_daily_tar(
      str(tar_p), str(gz_p), idle_seconds=999_999, today_local_date=today)


def test_should_seal_today_respects_idle(monkeypatch, tmp_path):
  base = 1_700_000_000
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.time.time",
      lambda: base + 30,
  )
  tar_p = tmp_path / "2024-01-15.tar"
  gz_p = tmp_path / "2024-01-15.tar.gz"
  tar_p.write_text("t")
  gz_p.write_text("g")
  os.utime(tar_p, (base, base))
  os.utime(gz_p, (base, base - 100))
  today = date(2024, 1, 15)
  assert not should_seal_daily_tar(
      str(tar_p), str(gz_p), idle_seconds=60, today_local_date=today)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.time.time",
      lambda: base + 120,
  )
  assert should_seal_daily_tar(
      str(tar_p), str(gz_p), idle_seconds=60, today_local_date=today)


def test_daily_tar_path_in_maintenance_scope_skip_and_only(tmp_path):
  tar_a = os.path.normpath(str(tmp_path / "2024-01-10.tar"))
  tar_b = os.path.normpath(str(tmp_path / "2024-01-11.tar"))
  assert daily_tar_path_in_maintenance_scope(tar_a)
  assert not daily_tar_path_in_maintenance_scope(
      tar_a, skip_daily_tar_paths=frozenset([tar_a]))
  assert daily_tar_path_in_maintenance_scope(
      tar_b, skip_daily_tar_paths=frozenset([tar_a]))
  assert not daily_tar_path_in_maintenance_scope(
      tar_b, only_daily_tar_paths=frozenset([tar_a]))
  assert daily_tar_path_in_maintenance_scope(
      tar_a, only_daily_tar_paths=frozenset([tar_a]))


def test_daily_tar_paths_for_archive_job_tasks():
  import hpcperfstats.dbload.sync_timedb as st_mod

  zst_path = "/daily/2024-03-01.tar.zst"
  tar_path = os.path.normpath(daily_tar_path_from_compressed(zst_path))
  deferred = [{
      "task": st_mod.ArchiveTask(archive_info=(zst_path, ["/raw/a"]), attempt=1),
      "paths": ["/raw/a"],
  }]
  assert daily_tar_paths_for_archive_job_tasks(deferred) == frozenset([tar_path])
  assert daily_tar_paths_for_archive_job_tasks([]) == frozenset()


def test_should_seal_immediately_if_dirty_bypasses_idle(monkeypatch, tmp_path):
  base = 1_700_000_000
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.time.time",
      lambda: base + 1,
  )
  tar_p = tmp_path / "2024-01-15.tar"
  gz_p = tmp_path / "2024-01-15.tar.gz"
  tar_p.write_text("t")
  gz_p.write_text("g")
  os.utime(tar_p, (base, base))
  os.utime(gz_p, (base, base - 1))
  today = date(2024, 1, 15)
  assert should_seal_daily_tar(
      str(tar_p),
      str(gz_p),
      idle_seconds=99999,
      today_local_date=today,
      seal_immediately_if_dirty=True,
  )


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_seal_dirty_daily_archives_continues_after_timeout_on_first_day(
    monkeypatch, tmp_path
):
  """TimeoutError on one day must not abort sealing other days."""
  from zoneinfo import ZoneInfo

  calls = []

  def fake_atomic_seal(tar_path, zst_path, *_a, **_k):
    calls.append(os.path.normpath(tar_path))
    if tar_path.endswith("2024-01-10.tar"):
      raise TimeoutError("Timed out waiting for write lock: %s" % tar_path)
    member = tmp_path / "member.txt"
    member.write_text("x")
    with tarfile.open(tar_path, "w") as tf:
      tf.add(str(member), arcname="member.txt")
    atomic_seal_tar_to_zst(
        tar_path,
        zst_path,
        num_threads=1,
        compress_level=6,
        keep_uncompressed_tar=True,
        log_fn=None,
    )

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.atomic_seal_tar_to_zst",
      fake_atomic_seal,
  )
  for day in ("2024-01-10", "2024-01-11"):
    tar_p = tmp_path / ("%s.tar" % day)
    zst_p = tmp_path / ("%s.tar.zst" % day)
    tar_p.write_text("dirty")
    if zst_p.exists():
      zst_p.unlink()
  seal_dirty_daily_archives(
      str(tmp_path),
      local_tz=ZoneInfo("UTC"),
      zstd_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      idle_seconds=0,
      seal_immediately_if_dirty=True,
      log_fn=None,
  )
  assert len(calls) == 2
  assert (tmp_path / "2024-01-11.tar.zst").is_file()


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_seal_dirty_daily_archives_respects_skip_and_only_scope(
    monkeypatch, tmp_path
):
  from zoneinfo import ZoneInfo

  sealed = []

  def fake_atomic_seal(tar_path, zst_path, *_a, **_k):
    sealed.append(os.path.normpath(tar_path))
    member = tmp_path / "m.txt"
    member.write_text("x")
    with tarfile.open(tar_path, "w") as tf:
      tf.add(str(member), arcname="m.txt")
    atomic_seal_tar_to_zst(
        tar_path, zst_path, num_threads=1, compress_level=6,
        keep_uncompressed_tar=True, log_fn=None,
    )

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.atomic_seal_tar_to_zst",
      fake_atomic_seal,
  )
  tar_skip = tmp_path / "2024-01-10.tar"
  tar_only = tmp_path / "2024-01-11.tar"
  for tar_p in (tar_skip, tar_only):
    tar_p.write_text("d")
    zst_p = tmp_path / (tar_p.name + ".zst")
    if zst_p.exists():
      zst_p.unlink()
  seal_dirty_daily_archives(
      str(tmp_path),
      local_tz=ZoneInfo("UTC"),
      zstd_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      idle_seconds=0,
      seal_immediately_if_dirty=True,
      log_fn=None,
      skip_daily_tar_paths=frozenset([os.path.normpath(str(tar_skip))]),
  )
  assert sealed == [os.path.normpath(str(tar_only))]
  sealed.clear()
  seal_dirty_daily_archives(
      str(tmp_path),
      local_tz=ZoneInfo("UTC"),
      zstd_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      idle_seconds=0,
      seal_immediately_if_dirty=True,
      log_fn=None,
      only_daily_tar_paths=frozenset([os.path.normpath(str(tar_skip))]),
  )
  assert sealed == [os.path.normpath(str(tar_skip))]


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_tar_to_zst_creates_valid_zstd(tmp_path):
  """Integration: temp zst + zstd -t + replace; keeps .tar when requested."""
  tar_path = tmp_path / "2021-03-01.tar"
  zst_path = tmp_path / "2021-03-01.tar.zst"
  member = tmp_path / "a.txt"
  member.write_text("hello")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="a.txt")
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert zst_path.is_file()
  subprocess.run(
      [zstd_executable(), "-t", "-T1", "-q", str(zst_path)],
      check=True,
  )
  assert tar_path.is_file()
  assert not (tmp_path / "2021-03-01.tar.zst.tmp").exists()


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_returns_members_after_compress(tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2021-03-10.tar"
  zst_path = tmp_path / "2021-03-10.tar.zst"
  member = tmp_path / "c.txt"
  member.write_text("payload")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="host/raw")
  expected = helpers.get_existing_archive_members(str(tar_path))
  returned = helpers.atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert returned == expected
  assert zst_path.is_file()


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_tar_to_zst_can_drop_uncompressed(tmp_path):
  tar_path = tmp_path / "2021-03-02.tar"
  zst_path = tmp_path / "2021-03-02.tar.zst"
  member = tmp_path / "b.txt"
  member.write_text("x")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="b.txt")
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=False,
      log_fn=None,
      remaining_raw_by_gz={},
  )
  assert zst_path.is_file()
  assert not tar_path.exists()


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_tar_to_zst_retains_tar_when_raw_remains_for_day(tmp_path):
  tar_path = tmp_path / "2021-03-05.tar"
  zst_path = tmp_path / "2021-03-05.tar.zst"
  member = tmp_path / "b.txt"
  member.write_text("x")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="b.txt")
  remaining = {str(zst_path): ["/archive/host/segment"]}
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=False,
      log_fn=None,
      remaining_raw_by_gz=remaining,
  )
  assert zst_path.is_file()
  assert tar_path.is_file()


def test_build_remaining_raw_stats_by_daily_gz_groups_closed_segments(tmp_path):
  arch_suffix = "cluster.integration.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2026, 4, 20, 10, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  zst_key = str(tgz_dir / "2026-04-20.tar.zst")
  remaining = build_remaining_raw_stats_by_daily_gz(
      str(tmp_path), arch_suffix, str(tgz_dir))
  assert remaining == {zst_key: [str(seg)]}
  assert remaining_raw_by_gz_has_paths_on_disk(remaining, zst_key)
  assert not remaining_raw_by_gz_has_paths_on_disk(
      remaining, str(tgz_dir / "2026-04-21.tar.zst"))


def test_remaining_raw_by_gz_has_paths_ghost_paths_not_on_disk(tmp_path):
  """Ghost accrual entries (deleted paths) must not block seal/scheduling gates."""
  zst_key = str(tmp_path / "2026-04-20.tar.zst")
  ghost_path = str(tmp_path / "missing.raw")
  remaining = {zst_key: [ghost_path]}
  assert not remaining_raw_by_gz_has_paths_on_disk(remaining, zst_key)
  live_path = tmp_path / "live.raw"
  live_path.write_text("x")
  remaining_live = {zst_key: [str(live_path)]}
  assert remaining_raw_by_gz_has_paths_on_disk(remaining_live, zst_key)


def test_atomic_seal_tar_to_zst_passes_thread_count_to_compress_and_test(monkeypatch, tmp_path):
  """zstd compress and zstd -t should use the requested -T count."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2021-03-04.tar"
  zst_path = tmp_path / "2021-03-04.tar.zst"
  member = tmp_path / "d.txt"
  member.write_text("x")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="d.txt")

  calls = []

  def _fake_compress(tar, out, threads, level):
    with open(out, "wb") as f:
      f.write(b"fake-zst-bytes")

  def _fake_test(path, threads):
    calls.append([zstd_executable(), "-t", "-T%d" % threads, "-q", path])

  monkeypatch.setattr(helpers, "zstd_compress_tar_to_file", _fake_compress)
  monkeypatch.setattr(helpers, "zstd_test", _fake_test)
  monkeypatch.setattr(helpers.shutil, "which", lambda name: "/usr/bin/zstd")

  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=3,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )

  assert zst_path.is_file()
  assert [zstd_executable(), "-t", "-T3", "-q", str(zst_path) + ".tmp"] in calls


def test_seal_dirty_daily_archives_seals_multiple_days_in_parallel(monkeypatch, tmp_path):
  """Regression: maintenance seals more than one day concurrently when workers > 1."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from zoneinfo import ZoneInfo

  archive_dir = tmp_path / "daily"
  archive_dir.mkdir()
  sealed = []
  lock = threading.Lock()
  active = {"count": 0, "max": 0}

  def _fake_atomic_seal(
      tar_path,
      zst_path,
      num_threads,
      compress_level,
      keep_uncompressed_tar,
      log_fn=None,
      remaining_raw_by_gz=None,
      force_remove_uncompressed_tar=False,
  ):
    del num_threads, compress_level, keep_uncompressed_tar, log_fn
    del remaining_raw_by_gz, force_remove_uncompressed_tar
    with lock:
      active["count"] += 1
      active["max"] = max(active["max"], active["count"])
    time.sleep(0.05)
    with lock:
      active["count"] -= 1
    sealed.append(tar_path)
    Path(zst_path).write_bytes(b"fake-zst")

  monkeypatch.setattr(helpers, "atomic_seal_tar_to_zst", _fake_atomic_seal)
  monkeypatch.setattr(helpers, "drop_legacy_gz_if_equivalent_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(helpers, "_get_archive_seal_worker_count", lambda n: min(3, n))
  monkeypatch.setattr(helpers, "should_seal_daily_tar", lambda *a, **k: True)

  for day in (1, 2, 3, 4):
    (archive_dir / ("2024-01-%02d.tar" % day)).write_bytes(b"tar")

  seal_dirty_daily_archives(
      str(archive_dir),
      local_tz=ZoneInfo("UTC"),
      zstd_threads=0,
      compress_level=6,
      keep_uncompressed_tar=True,
      idle_seconds=60,
  )

  assert len(sealed) == 4
  assert active["max"] >= 2


def test_seal_dirty_daily_archives_isolates_per_day_failures(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from zoneinfo import ZoneInfo

  archive_dir = tmp_path / "daily"
  archive_dir.mkdir()
  ok_tar = str(archive_dir / "2024-02-01.tar")
  bad_tar = str(archive_dir / "2024-02-02.tar")
  Path(ok_tar).write_bytes(b"tar")
  Path(bad_tar).write_bytes(b"tar")
  sealed = []

  def _fake_atomic_seal(tar_path, zst_path, *args, **kwargs):
    del args, kwargs
    if tar_path == bad_tar:
      raise RuntimeError("boom")
    sealed.append(tar_path)
    Path(zst_path).write_bytes(b"zst")

  monkeypatch.setattr(helpers, "atomic_seal_tar_to_zst", _fake_atomic_seal)
  monkeypatch.setattr(helpers, "drop_legacy_gz_if_equivalent_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(helpers, "should_seal_daily_tar", lambda *a, **k: True)

  seal_dirty_daily_archives(
      str(archive_dir),
      local_tz=ZoneInfo("UTC"),
      zstd_threads=0,
      compress_level=6,
      keep_uncompressed_tar=True,
      idle_seconds=60,
  )

  assert sealed == [ok_tar]


# --- get_tar_file_tasks ---


def test_get_tar_file_tasks_returns_file_members_only(tmp_path):
  """get_tar_file_tasks returns (tar_path, member_name) for file members, not directories."""
  tar_path = tmp_path / "test.tar"
  a = tmp_path / "a.txt"
  a.write_text("x")
  (tmp_path / "subdir").mkdir()
  sub_f = tmp_path / "subdir" / "b.txt"
  sub_f.write_text("y")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(a), arcname="a.txt")
    tf.add(str(tmp_path / "subdir"), arcname="subdir")
    tf.add(str(sub_f), arcname="subdir/b.txt")
  tasks = get_tar_file_tasks(str(tar_path))
  assert set(tasks) == {(str(tar_path), "a.txt"), (str(tar_path), "subdir/b.txt")}


def test_iter_tar_file_tasks_matches_get_tar_file_tasks(tmp_path):
  """Streaming and eager helpers produce the same task tuples."""
  tar_path = tmp_path / "test.tar"
  a = tmp_path / "a.txt"
  a.write_text("x")
  (tmp_path / "subdir").mkdir()
  sub_f = tmp_path / "subdir" / "b.txt"
  sub_f.write_text("y")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(a), arcname="a.txt")
    tf.add(str(tmp_path / "subdir"), arcname="subdir")
    tf.add(str(sub_f), arcname="subdir/b.txt")

  assert list(iter_tar_file_tasks(str(tar_path))) == get_tar_file_tasks(str(tar_path))


def test_get_tar_file_tasks_empty_tar(tmp_path):
  """Empty tar returns empty list."""
  tar_path = tmp_path / "empty.tar"
  with tarfile.open(tar_path, "w"):
    pass
  assert get_tar_file_tasks(str(tar_path)) == []


def test_get_tar_file_tasks_prefers_uncompressed_tar_when_both_exist(tmp_path):
  """Deferred compression: mutable .tar wins over sibling .tar.gz for reads."""
  day_tar = tmp_path / "2020-06-01.tar"
  day_gz = tmp_path / "2020-06-01.tar.gz"
  inner = tmp_path / "member.txt"
  inner.write_text("from-plain-tar")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="from-tar.txt")
  other = tmp_path / "other.txt"
  other.write_text("from-gz-only")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(other), arcname="from-gz.txt")
  tasks = get_tar_file_tasks(str(day_gz))
  paths = {t[0] for t in tasks}
  names = {t[1] for t in tasks}
  assert paths == {str(day_tar)}
  assert "from-tar.txt" in names
  assert "from-gz.txt" not in names


def test_iter_stream_tasks_chunked_respects_concurrency(monkeypatch):
  """Stream task chunks are bounded by max concurrent sealed days."""
  monkeypatch.setattr(
      sta.cfg,
      "get_sync_timedb_archive_max_concurrent_sealed_days",
      lambda: 2,
  )
  monkeypatch.setattr(
      sta.cfg,
      "get_daily_archive_dir_path",
      lambda: "/daily",
  )
  monkeypatch.setattr(
      sta,
      "iter_archive_ingest_tasks",
      lambda paths, daily_archive_dir="": [
          (STREAM_ARCHIVE_TASK, p) for p in paths
      ],
  )
  chunks = list(
      _iter_stream_tasks_chunked(
          ["/a.tar.zst", "/b.tar.zst", "/c.tar.zst"],
          chunk_size=2,
      ),
  )
  assert chunks == [
      [(STREAM_ARCHIVE_TASK, "/a.tar.zst"), (STREAM_ARCHIVE_TASK, "/b.tar.zst")],
      [(STREAM_ARCHIVE_TASK, "/c.tar.zst")],
  ]


def test_sliding_window_refills_idle_worker_slot(monkeypatch):
  """Regression: fifth sealed day starts when first of four in-flight completes."""
  import threading
  import time

  from hpcperfstats.tests.test_multiprocessing_pool_health import _ManualPool

  monkeypatch.setattr(
      st,
      "_prewarm_archive_members_redis_for_sealed_chunk",
      lambda _paths: "-",
  )
  monkeypatch.setattr(
      sta.cfg,
      "get_sync_timedb_archive_max_concurrent_sealed_days",
      lambda: 4,
  )
  monkeypatch.setattr(sta.cfg, "get_sync_pool_poll_timeout_s", lambda: 0.01)

  pool = _ManualPool()
  sealed_paths = [
      "/daily/slow0.tar.zst",
      "/daily/fast1.tar.zst",
      "/daily/fast2.tar.zst",
      "/daily/fast3.tar.zst",
      "/daily/slow4.tar.zst",
  ]
  tasks_locked = [("lock", path) for path in sealed_paths]
  results = []
  errors = []

  def _run():
    try:
      sta._process_sealed_tasks_sliding_window(
          pool,
          lambda task: task[1],
          tasks_locked,
          results.append,
          worker_registry={},
      )
    except Exception as exc:
      errors.append(exc)

  thread = threading.Thread(target=_run, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 4 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count == 4
  fast_first_batch = [
      ar
      for ar, task in pool.inflight.items()
      if "fast" in task[1]
  ]
  assert len(fast_first_batch) == 3
  for async_result in fast_first_batch:
    async_result.finish()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 5 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count == 5
  assert pool.peak == 4
  deadline = time.monotonic() + 5.0
  while len(results) < len(sealed_paths) and time.monotonic() < deadline:
    for async_result in list(pool.inflight):
      async_result.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)
  assert not errors
  assert sorted(results) == sorted(sealed_paths)


def test_archive_worker_process_count_uses_sync_archive_pool(monkeypatch):
  """Archive multiprocessing pool uses get_sync_archive_pool_processes (default 4)."""
  monkeypatch.setattr(sta.cfg, "get_sync_archive_pool_processes", lambda: 4)
  assert sta._archive_worker_process_count() == 4
  monkeypatch.setattr(sta.cfg, "get_sync_archive_pool_processes", lambda: 2)
  assert sta._archive_worker_process_count() == 2


def test_get_tar_file_tasks_restores_corrupt_tar_from_gz(monkeypatch, tmp_path):
  """Corrupt tar with sibling .gz is restored via zstd gzip and retried once."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = str(tmp_path / "broken.tar")
  gz_path = "%s.gz" % tar_path

  class _NoOpLock:
    def __enter__(self):
      return self

    def __exit__(self, _exc_type, exc, tb):
      return False

  class _Member:
    def __init__(self, name, is_file):
      self.name = name
      self._is_file = is_file

    def isfile(self):
      return self._is_file

  class _FakeTar:
    def __enter__(self):
      return self

    def __exit__(self, _exc_type, exc, tb):
      return False

    def getmembers(self):
      return [_Member("a.txt", True), _Member("dir", False)]

  open_calls = {"count": 0}
  remove_calls = []
  restore_calls = []

  def _open_mock(path, mode):
    assert path == tar_path
    assert mode == "r"
    open_calls["count"] += 1
    if open_calls["count"] == 1:
      raise tarfile.ReadError("corrupt tar")
    return _FakeTar()

  monkeypatch.setattr(helpers, "file_read_lock_wait", lambda _p: _NoOpLock())
  monkeypatch.setattr(helpers, "file_write_lock", lambda _p: _NoOpLock())
  monkeypatch.setattr(helpers.tarfile, "open", _open_mock)
  monkeypatch.setattr(helpers.os.path, "exists", lambda p: p in (tar_path, gz_path))
  monkeypatch.setattr(
      helpers.os.path,
      "isfile",
      lambda p: p in (tar_path, gz_path),
  )
  monkeypatch.setattr(
      helpers.os,
      "remove",
      lambda p: remove_calls.append(p),
  )
  monkeypatch.setattr(
      helpers,
      "decompress_compressed_to_tar",
      lambda path, out_tar, threads, **_: restore_calls.append((path, out_tar, threads)) or True,
  )

  assert get_tar_file_tasks(tar_path) == [(tar_path, "a.txt")]
  assert open_calls["count"] == 2
  assert remove_calls == [tar_path]
  assert restore_calls == [(gz_path, tar_path, helpers.get_archive_zstd_thread_count())]


def test_get_tar_file_tasks_raises_when_corrupt_and_no_gz(monkeypatch, tmp_path):
  """Corrupt tar without sibling .gz surfaces the read error."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = str(tmp_path / "broken.tar")

  class _NoOpLock:
    def __enter__(self):
      return self

    def __exit__(self, _exc_type, exc, tb):
      return False

  monkeypatch.setattr(helpers, "file_read_lock_wait", lambda _p: _NoOpLock())
  monkeypatch.setattr(
      helpers.tarfile,
      "open",
      lambda _path, _mode: (_ for _ in ()).throw(tarfile.ReadError("corrupt tar")),
  )
  monkeypatch.setattr(helpers.os.path, "exists", lambda _p: False)

  with pytest.raises(tarfile.ReadError):
    get_tar_file_tasks(tar_path)


def test_get_tar_file_tasks_raises_when_zstd_restore_fails(monkeypatch, tmp_path):
  """Corrupt tar with .gz still raises when zstd restore fails."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = str(tmp_path / "broken.tar")
  gz_path = "%s.gz" % tar_path

  class _NoOpLock:
    def __enter__(self):
      return self

    def __exit__(self, _exc_type, exc, tb):
      return False

  monkeypatch.setattr(helpers, "file_read_lock_wait", lambda _p: _NoOpLock())
  monkeypatch.setattr(
      helpers.tarfile,
      "open",
      lambda _path, _mode: (_ for _ in ()).throw(tarfile.ReadError("corrupt tar")),
  )
  monkeypatch.setattr(helpers.os.path, "exists", lambda p: p == gz_path or p == tar_path)
  monkeypatch.setattr(helpers.os, "remove", lambda _p: None)

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", lambda *_a, **_k: False)

  with pytest.raises(tarfile.ReadError):
    get_tar_file_tasks(tar_path)


# --- get_existing_archive_members ---


def test_get_existing_archive_members_missing_file(tmp_path):
  """Missing tar path returns empty dict."""
  assert get_existing_archive_members(str(tmp_path / "nonexistent.tar")) == {}


def test_get_existing_archive_members_empty_tar(tmp_path):
  """Empty tar returns empty dict."""
  tar_path = tmp_path / "empty.tar"
  with tarfile.open(tar_path, "w"):
    pass
  assert get_existing_archive_members(str(tar_path)) == {}


def test_get_existing_archive_members_with_files(tmp_path):
  """Tar with members returns name -> size."""
  tar_path = tmp_path / "test.tar"
  a = tmp_path / "a.txt"
  b = tmp_path / "b.txt"
  a.write_text("hello")
  b.write_text("world")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(a), arcname="a.txt")
    tf.add(str(b), arcname="sub/b.txt")
  members = get_existing_archive_members(str(tar_path))
  assert members["a.txt"] == 5
  assert members["sub/b.txt"] == 5
  assert not (tmp_path / "test.tar.fnctl.lock").exists()


def test_get_existing_archive_members_for_daily_prefers_tar(tmp_path):
  """When both .tar and .tar.gz exist, member map comes from .tar."""
  day_tar = tmp_path / "2022-05-01.tar"
  day_gz = tmp_path / "2022-05-01.tar.gz"
  inner = tmp_path / "x.txt"
  inner.write_text("hello")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="from.tar")
  other = tmp_path / "y.txt"
  other.write_text("gz")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(other), arcname="from.gz")
  m = get_existing_archive_members_for_daily_archive(str(day_gz))
  assert m["from.tar"] == 5
  assert "from.gz" not in m


def test_get_existing_archive_members_for_daily_reads_gz_when_no_tar(tmp_path):
  day_gz = tmp_path / "2022-05-02.tar.gz"
  inner = tmp_path / "z.txt"
  inner.write_text("abc")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="onlymember")
  m = get_existing_archive_members_for_daily_archive(str(day_gz))
  assert m["onlymember"] == 3


def test_remove_verified_archived_raw_files_removes_when_verified(tmp_path):
  """Removal requires matching .tar and .tar.gz validation (same members/sizes)."""
  arch_suffix = "cluster.integration.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2022, 5, 3, 15, 30, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  gz_key = str(tgz_dir / "2022-05-03.tar.gz")
  tar_path = daily_tar_path_from_compressed(gz_key)
  arcname = get_tar_member_name(str(seg))
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(seg), arcname=arcname)
  with tarfile.open(gz_key, "w:gz") as tf:
    tf.add(str(seg), arcname=arcname)
  assert validate_sealed_daily_archive_for_raw_removal(gz_key, log_fn=None)[0]
  remove_verified_archived_raw_files(
      str(tmp_path), arch_suffix, str(tgz_dir), log_fn=None)
  assert not seg.is_file()


def test_remove_verified_archived_raw_files_bootstraps_missing_daily_archive(
    tmp_path, monkeypatch,
):
  """When neither .tar nor .tar.gz exists, removal pass must call archive before validate."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  arch_suffix = "cluster.integration.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2026, 4, 15, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  zst_key = str(tgz_dir / "2026-04-15.tar.zst")
  tar_path = daily_tar_path_from_compressed(zst_key)
  assert not os.path.isfile(zst_key)
  assert not os.path.isfile(tar_path)

  archive_calls = []

  def fake_archive(info):
    archive_calls.append(info)
    return True

  member_name = get_tar_member_name(str(seg))

  def fake_validate(sealed_path, log_fn=None, validation_cache=None, allow_auto_seal=True):
    del validation_cache, allow_auto_seal
    assert sealed_path == zst_key
    return True, {member_name: os.path.getsize(seg)}

  monkeypatch.setattr(
      helpers,
      "validate_sealed_daily_archive_for_raw_removal",
      fake_validate,
  )

  remove_verified_archived_raw_files(
      str(tmp_path),
      arch_suffix,
      str(tgz_dir),
      log_fn=None,
      archive_stats_files_fn=fake_archive,
      ingest_ready_fn=lambda _p: True,
  )
  assert archive_calls == [(zst_key, [str(seg)])]
  assert not seg.is_file()


def test_remove_verified_archived_raw_files_skips_bootstrap_until_ingest_ready(
    tmp_path, monkeypatch,
):
  """Bootstrap must not run when ingest_ready_fn rejects paths."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  arch_suffix = "cluster.integration.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2026, 4, 16, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  archive_calls = []

  def fake_archive(info):
    archive_calls.append(info)
    return True

  monkeypatch.setattr(
      helpers,
      "validate_sealed_daily_archive_for_raw_removal",
      lambda *_a, **_k: (True, {}),
  )

  remove_verified_archived_raw_files(
      str(tmp_path),
      arch_suffix,
      str(tgz_dir),
      log_fn=None,
      archive_stats_files_fn=fake_archive,
      ingest_ready_fn=lambda _p: False,
  )
  assert archive_calls == []
  assert seg.is_file()


def test_remove_verified_archived_raw_files_skips_removal_until_ingest_ready(
    tmp_path,
):
  """Verified archive membership is not enough without ingest_ready_fn."""
  arch_suffix = "cluster.integration.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2022, 5, 4, 15, 30, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  gz_key = str(tgz_dir / "2022-05-04.tar.gz")
  tar_path = daily_tar_path_from_compressed(gz_key)
  arcname = get_tar_member_name(str(seg))
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(seg), arcname=arcname)
  with tarfile.open(gz_key, "w:gz") as tf:
    tf.add(str(seg), arcname=arcname)

  remove_verified_archived_raw_files(
      str(tmp_path),
      arch_suffix,
      str(tgz_dir),
      log_fn=None,
      ingest_ready_fn=lambda _p: False,
  )
  assert seg.is_file()


def test_archive_stats_files_skips_append_when_not_head_ingested(monkeypatch, tmp_path):
  """Paths failing the DB gate must not reach tar append."""
  import hpcperfstats.dbload.sync_timedb as st

  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-02.tar.zst")
  append_calls = {"n": 0}

  monkeypatch.setattr(
      st,
      "filter_paths_head_ingested",
      lambda paths, **_: ([], list(paths)),
  )
  monkeypatch.setattr(st, "_append_to_tar", lambda *_a, **_k: append_calls.__setitem__("n", append_calls["n"] + 1))

  assert archive_stats_files((archive_key, [str(raw_file)]))
  assert append_calls["n"] == 0


def test_remove_verified_uncompressed_daily_tars_removes_when_tar_matches_gz(tmp_path):
  day_tar = tmp_path / "2026-04-22.tar"
  day_gz = tmp_path / "2026-04-22.tar.gz"
  member = tmp_path / "member.txt"
  member.write_text("same-content")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(member), arcname="member.txt")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(member), arcname="member.txt")

  remove_verified_uncompressed_daily_tars(
      str(tmp_path), log_fn=None, remaining_raw_by_gz={})

  assert not day_tar.exists()
  assert day_gz.is_file()


def test_remove_verified_uncompressed_daily_tars_skips_when_raw_remains(tmp_path):
  day_tar = tmp_path / "2026-04-23.tar"
  day_gz = tmp_path / "2026-04-23.tar.gz"
  raw_on_disk = tmp_path / "still_here.raw"
  raw_on_disk.write_text("data")
  member = tmp_path / "member.txt"
  member.write_text("same-content")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(member), arcname="member.txt")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(member), arcname="member.txt")

  remove_verified_uncompressed_daily_tars(
      str(tmp_path),
      log_fn=None,
      remaining_raw_by_gz={
          normalize_daily_compressed_path(str(day_gz)): [str(raw_on_disk)],
      },
  )

  assert day_tar.is_file()
  assert day_gz.is_file()


def test_remove_verified_uncompressed_daily_tars_drops_when_raw_ghost_only(
    tmp_path,
):
  """Ghost mapping entries (not on disk) must not block tar drop (hpcperfstats03 H6)."""
  day_tar = tmp_path / "2026-04-24.tar"
  day_gz = tmp_path / "2026-04-24.tar.gz"
  member = tmp_path / "member.txt"
  member.write_text("same-content")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(member), arcname="member.txt")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(member), arcname="member.txt")

  remove_verified_uncompressed_daily_tars(
      str(tmp_path),
      log_fn=None,
      remaining_raw_by_gz={
          normalize_daily_compressed_path(str(day_gz)): ["/ghost/raw/not-on-disk"],
      },
  )

  assert not day_tar.exists()
  assert day_gz.is_file()


def test_remove_verified_uncompressed_daily_tars_skips_when_raw_on_disk(
    tmp_path,
):
  day_tar = tmp_path / "2026-04-24b.tar"
  day_gz = tmp_path / "2026-04-24b.tar.gz"
  raw_on_disk = tmp_path / "still_here.raw"
  raw_on_disk.write_text("data")
  member = tmp_path / "member-b.txt"
  member.write_text("same-content")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(member), arcname="member.txt")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(member), arcname="member.txt")

  remove_verified_uncompressed_daily_tars(
      str(tmp_path),
      log_fn=None,
      remaining_raw_by_gz={
          normalize_daily_compressed_path(str(day_gz)): [str(raw_on_disk)],
      },
  )

  assert day_tar.is_file()
  assert day_gz.is_file()


def test_daily_tar_needs_day_close_work_when_tar_exists_despite_tar_dropped_hint(
    tmp_path,
):
  """Hint drift: ``tar_dropped`` phase but ``.tar`` still on disk needs work."""
  day_tar = tmp_path / "2026-05-22.tar"
  day_tar.write_bytes(b"x" * 64)
  day_phases = {str(day_tar): "tar_dropped"}
  assert daily_tar_needs_day_close_work(
      str(day_tar),
      day_phases=day_phases,
      remaining_raw_by_gz={},
  )


def test_remove_verified_uncompressed_daily_tars_force_removes_with_raw(
    tmp_path,
):
  day_tar = tmp_path / "2026-04-25.tar"
  day_gz = tmp_path / "2026-04-25.tar.gz"
  member = tmp_path / "member.txt"
  member.write_text("same-content")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(member), arcname="member.txt")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(member), arcname="member.txt")

  remove_verified_uncompressed_daily_tars(
      str(tmp_path),
      log_fn=None,
      remaining_raw_by_gz={
          normalize_daily_compressed_path(str(day_gz)): ["/raw/still-here"],
      },
      force_remove_uncompressed_tar=True,
  )

  assert not day_tar.is_file()
  assert day_gz.is_file()


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_tar_to_zst_force_removes_tar_when_raw_remains(tmp_path):
  tar_path = tmp_path / "2021-03-06.tar"
  zst_path = tmp_path / "2021-03-06.tar.zst"
  member = tmp_path / "c.txt"
  member.write_text("x")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="c.txt")
  remaining = {str(zst_path): ["/archive/host/segment"]}
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=False,
      log_fn=None,
      remaining_raw_by_gz=remaining,
      force_remove_uncompressed_tar=True,
  )
  assert zst_path.is_file()
  assert not tar_path.is_file()


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_tar_to_zst_refuses_shrink_over_existing_zst(tmp_path):
  tar_path = tmp_path / "2021-03-07.tar"
  zst_path = tmp_path / "2021-03-07.tar.zst"
  a = tmp_path / "a.txt"
  b = tmp_path / "b.txt"
  a.write_text("a")
  b.write_text("bb")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(a), arcname="a.txt")
    tf.add(str(b), arcname="b.txt")
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  first_size = zst_path.stat().st_size
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(a), arcname="a.txt")
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert zst_path.stat().st_size == first_size


def test_archive_stats_files_fail_closed_when_decompress_fails(monkeypatch, tmp_path):
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-02.tar.zst")
  Path(archive_key).write_bytes(b"fake-zst")

  import hpcperfstats.dbload.sync_timedb as st

  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(
      st,
      "_decompress_compressed_archive",
      lambda *_a, **_k: False,
  )
  append_calls = {"n": 0}
  monkeypatch.setattr(
      st,
      "_append_to_tar",
      lambda *_a, **_k: append_calls.__setitem__("n", append_calls["n"] + 1),
  )

  assert archive_stats_files((archive_key, [str(raw_file)])) is False
  assert append_calls["n"] == 0
  assert raw_file.exists()


def test_not_ingested_raw_still_blocks_tar_removal_after_seal(tmp_path, monkeypatch):
  """Scheduled maintenance: filesystem gate blocks .tar removal while raw remains."""

  arch_suffix = "cluster.integration.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2026, 4, 24, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  gz_key = str(tgz_dir / "2026-04-24.tar.gz")
  tar_path = tgz_dir / "2026-04-24.tar"
  arcname = get_tar_member_name(str(seg))
  with tarfile.open(str(tar_path), "w") as tf:
    tf.add(str(seg), arcname=arcname)
  with tarfile.open(gz_key, "w:gz") as tf:
    tf.add(str(seg), arcname=arcname)

  remaining = build_remaining_raw_stats_by_daily_gz(
      str(tmp_path), arch_suffix, str(tgz_dir))
  remove_verified_uncompressed_daily_tars(
      str(tgz_dir), log_fn=None, remaining_raw_by_gz=remaining)
  assert tar_path.is_file()

  remove_verified_archived_raw_files(
      str(tmp_path),
      arch_suffix,
      str(tgz_dir),
      log_fn=None,
      ingest_ready_fn=lambda _p: True,
  )
  assert not seg.is_file()
  remaining_after = build_remaining_raw_stats_by_daily_gz(
      str(tmp_path), arch_suffix, str(tgz_dir))
  remove_verified_uncompressed_daily_tars(
      str(tgz_dir), log_fn=None, remaining_raw_by_gz=remaining_after)
  assert not tar_path.is_file()


def test_validate_sealed_daily_archive_fails_on_tar_gz_mismatch(tmp_path):
  gz = tmp_path / "2022-01-01.tar.gz"
  tar_p = tmp_path / "2022-01-01.tar"
  a = tmp_path / "a.txt"
  a.write_text("aa")
  b = tmp_path / "b.txt"
  b.write_text("bbbb")
  with tarfile.open(tar_p, "w") as tf:
    tf.add(str(a), arcname="same/name")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(b), arcname="same/name")
  ok, members = validate_sealed_daily_archive_for_raw_removal(str(gz), log_fn=None)
  assert not ok
  assert members is None


def test_validate_sealed_daily_archive_gzip_only_ok(tmp_path):
  gz = tmp_path / "2022-01-02.tar.gz"
  f = tmp_path / "one.txt"
  f.write_text("hi")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(f), arcname="one.txt")
  ok, members = validate_sealed_daily_archive_for_raw_removal(str(gz), log_fn=None)
  assert ok
  assert members["one.txt"] == 2


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_validate_sealed_daily_archive_seals_from_valid_tar_when_zst_missing(tmp_path, monkeypatch):
  zst = tmp_path / "2022-01-03.tar.zst"
  tar_p = tmp_path / "2022-01-03.tar"
  f = tmp_path / "raw.txt"
  f.write_text("seal-me")
  with tarfile.open(tar_p, "w") as tf:
    tf.add(str(f), arcname="raw.txt")

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  seal_calls = {"count": 0}

  def _fake_atomic_seal(
      _tar_path,
      _zst_path,
      num_threads,
      compress_level,
      keep_uncompressed_tar,
      log_fn=None,
      remaining_raw_by_gz=None,
      force_remove_uncompressed_tar=False,
  ):
    del keep_uncompressed_tar, log_fn, remaining_raw_by_gz, force_remove_uncompressed_tar
    seal_calls["count"] += 1
    from hpcperfstats.dbload.lib.zstd_cli import zstd_compress_tar_to_file, zstd_test

    tmp = "%s.tmp" % _zst_path
    zstd_compress_tar_to_file(_tar_path, tmp, num_threads, compress_level)
    zstd_test(tmp, num_threads)
    os.replace(tmp, _zst_path)

  monkeypatch.setattr(helpers, "atomic_seal_tar_to_zst", _fake_atomic_seal)
  ok, members = validate_sealed_daily_archive_for_raw_removal(str(zst), log_fn=None)
  assert ok
  assert seal_calls["count"] == 1
  assert members["raw.txt"] == len("seal-me")


def test_validate_sealed_daily_archive_validation_cache_hits(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz = tmp_path / "2022-02-01.tar.gz"
  f = tmp_path / "cache.txt"
  f.write_text("cache")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(f), arcname="cache.txt")

  calls = {"n": 0}
  real_scan = helpers._scan_compressed_archive_members_and_readable

  def wrapped_scan(path, **kwargs):
    del kwargs
    calls["n"] += 1
    return real_scan(path)

  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", wrapped_scan)
  cache = {"hits": 0, "misses": 0}
  first_ok, first_members = validate_sealed_daily_archive_for_raw_removal(
      str(gz), log_fn=None, validation_cache=cache
  )
  second_ok, second_members = validate_sealed_daily_archive_for_raw_removal(
      str(gz), log_fn=None, validation_cache=cache
  )

  assert first_ok and second_ok
  assert first_members == second_members
  assert calls["n"] == 1
  assert cache["misses"] == 1
  assert cache["hits"] == 1


def test_batch_raw_removal_parallel_validation_counts_misses_once_per_archive(
    monkeypatch, tmp_path,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  monkeypatch.setattr(helpers, "_get_archive_validation_worker_count", lambda n: 2)
  mapping = {}
  for day in ("2022-02-01", "2022-02-02"):
    gz = tmp_path / ("%s.tar.gz" % day)
    member = tmp_path / ("%s.txt" % day)
    member.write_text(day)
    tar_path = daily_tar_path_from_compressed(str(gz))
    with tarfile.open(tar_path, "w") as tf:
      tf.add(str(member), arcname="%s.txt" % day)
    with tarfile.open(gz, "w:gz") as tf:
      tf.add(str(member), arcname="%s.txt" % day)
    mapping[str(gz)] = []

  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[str(tmp_path / "closed-segment-placeholder")],
      mapping=mapping,
      ready_paths=set(),
  )
  cache = {"hits": 0, "misses": 0}
  remove_verified_archived_raw_files(
      str(tmp_path),
      "suffix",
      str(tmp_path),
      log_fn=None,
      maintenance_snapshot=snapshot,
      validation_cache=cache,
  )
  assert cache["misses"] == 2
  assert cache["hits"] == 0


def test_validate_sealed_daily_archive_validation_cache_invalidates_on_mtime_change(
    monkeypatch, tmp_path
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz = tmp_path / "2022-02-02.tar.gz"
  f = tmp_path / "stale.txt"
  f.write_text("v1")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(f), arcname="stale.txt")

  calls = {"n": 0}
  real_scan = helpers._scan_compressed_archive_members_and_readable

  def wrapped_scan(path, **kwargs):
    calls["n"] += 1
    return real_scan(path, **kwargs)

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: False,
  )
  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", wrapped_scan)
  cache = {"hits": 0, "misses": 0}
  ok1, _members1 = validate_sealed_daily_archive_for_raw_removal(
      str(gz), log_fn=None, validation_cache=cache
  )
  assert ok1

  # Rewrite gzip so key identity changes (mtime/size).
  f.write_text("v2-updated")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(f), arcname="stale.txt")

  ok2, members2 = validate_sealed_daily_archive_for_raw_removal(
      str(gz), log_fn=None, validation_cache=cache
  )
  assert ok2
  assert members2["stale.txt"] == len("v2-updated")
  assert calls["n"] == 2
  assert cache["misses"] == 2


def test_get_file_member_sizes_from_gzip_archive_reads_gz_only(tmp_path):
  gz = tmp_path / "x.tar.gz"
  tar_p = tmp_path / "x.tar"
  f = tmp_path / "inner.txt"
  f.write_text("zzz")
  with tarfile.open(tar_p, "w") as tf:
    tf.add(str(f), arcname="only")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(f), arcname="only")
  assert get_file_member_sizes_from_gzip_archive(str(gz)) == {"only": 3}


def test_get_archive_validation_worker_count_bounds(monkeypatch):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(helpers.cfg, "get_sync_archive_validation_max_workers", lambda: 6)
  monkeypatch.delenv("SYNC_ARCHIVE_VALIDATION_WORKERS", raising=False)
  assert helpers._get_archive_validation_worker_count(0) == 1
  assert helpers._get_archive_validation_worker_count(3) == 3
  assert helpers._get_archive_validation_worker_count(20) == 6

  monkeypatch.setenv("SYNC_ARCHIVE_VALIDATION_WORKERS", "2")
  assert helpers._get_archive_validation_worker_count(20) == 2


def test_remove_verified_archived_raw_files_streaming_apply_follows_completion_order(
    tmp_path, monkeypatch
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  seg_a = tmp_path / "seg_a"
  seg_b = tmp_path / "seg_b"
  seg_a.write_text("a")
  seg_b.write_text("bbb")
  gz_a = str(tmp_path / "2026-04-21.tar.gz")
  gz_b = str(tmp_path / "2026-04-22.tar.gz")

  monkeypatch.setattr(
      helpers,
      "collect_stats_files_in_range",
      lambda *_a, **_k: [str(seg_a), str(seg_b)],
  )
  monkeypatch.setattr(
      helpers,
      "build_archive_mapping",
      lambda *_a, **_k: {gz_a: [str(seg_a)], gz_b: [str(seg_b)]},
  )

  def fake_stream(_gz_paths, *, log_fn=None, validation_cache=None, allow_auto_seal=True):
    del log_fn, validation_cache, allow_auto_seal
    members_b = {helpers.get_tar_member_name(str(seg_b)): seg_b.stat().st_size}
    members_a = {helpers.get_tar_member_name(str(seg_a)): seg_a.stat().st_size}
    yield gz_b, True, members_b
    yield gz_a, True, members_a

  monkeypatch.setattr(helpers, "_iter_archive_validation_results_stream", fake_stream)
  logs = []
  helpers.remove_verified_archived_raw_files(
      str(tmp_path),
      "unused.suffix",
      str(tmp_path / "daily"),
      log_fn=lambda msg, flush=True: logs.append(msg),
      archive_stats_files_fn=lambda *_a, **_k: True,
  )
  assert not seg_a.exists()
  assert not seg_b.exists()
  removal_logs = [
      l for l in logs if "removing stats file (scheduled archive maintenance):" in l
  ]
  assert removal_logs
  assert str(seg_b) in removal_logs[0]


def test_remove_verified_uncompressed_daily_tars_streaming_apply_order(
    tmp_path, monkeypatch
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_a = tmp_path / "2026-04-21.tar"
  tar_b = tmp_path / "2026-04-22.tar"
  tar_a.write_text("a")
  tar_b.write_text("b")
  zst_a = tmp_path / "2026-04-21.tar.zst"
  zst_b = tmp_path / "2026-04-22.tar.zst"
  zst_a.write_bytes(b"sealed")
  zst_b.write_bytes(b"sealed")
  monkeypatch.setattr(helpers, "iter_daily_tar_paths", lambda _d: [str(tar_a), str(tar_b)])

  def fake_stream(_gz_paths, *, log_fn=None, validation_cache=None, allow_auto_seal=True):
    del log_fn, validation_cache, allow_auto_seal
    yield str(zst_b), True, {"x": 1}
    yield str(zst_a), True, {"x": 1}

  monkeypatch.setattr(helpers, "_iter_archive_validation_results_stream", fake_stream)
  logs = []
  helpers.remove_verified_uncompressed_daily_tars(
      str(tmp_path),
      log_fn=lambda msg, flush=True: logs.append(msg),
  )
  assert not tar_a.exists()
  assert not tar_b.exists()
  removed_logs = [
      l for l in logs
      if "Maintenance removed verified uncompressed tar:" in l
  ]
  assert removed_logs
  assert str(tar_b) in removed_logs[0]


def test_remove_verified_archived_raw_files_logs_cache_summary(tmp_path):
  arch_suffix = "cluster.integration.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2022, 5, 4, 15, 30, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  gz_key = str(tgz_dir / "2022-05-04.tar.gz")
  tar_path = daily_tar_path_from_compressed(gz_key)
  arcname = get_tar_member_name(str(seg))
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(seg), arcname=arcname)
  with tarfile.open(gz_key, "w:gz") as tf:
    tf.add(str(seg), arcname=arcname)

  logs = []
  remove_verified_archived_raw_files(
      str(tmp_path),
      arch_suffix,
      str(tgz_dir),
      log_fn=lambda msg, flush=True: logs.append(msg),
  )
  assert any("Archive validation cache summary hits=" in ln for ln in logs)


def _tar_add_bytes(tf, name, data):
  ti = tarfile.TarInfo(name=name)
  ti.size = len(data)
  ti.mtime = 0
  tf.addfile(ti, io.BytesIO(data))


def test_get_existing_archive_members_uses_max_size_for_duplicate_paths(tmp_path):
  """Same member path twice: reported size is the larger payload."""
  tar_path = tmp_path / "dup.tar"
  with tarfile.open(tar_path, "w") as tf:
    _tar_add_bytes(tf, "host/stats", b"aa")
    _tar_add_bytes(tf, "host/stats", b"longer")
  assert get_existing_archive_members(str(tar_path))["host/stats"] == 6


def test_tar_has_duplicate_file_members_true_when_same_name_twice(tmp_path):
  tar_path = tmp_path / "d.tar"
  with tarfile.open(tar_path, "w") as tf:
    _tar_add_bytes(tf, "x", b"1")
    _tar_add_bytes(tf, "x", b"22")
  assert tar_has_duplicate_file_members(str(tar_path))


def test_tar_has_duplicate_file_members_false_when_unique(tmp_path):
  tar_path = tmp_path / "u.tar"
  with tarfile.open(tar_path, "w") as tf:
    _tar_add_bytes(tf, "a", b"x")
    _tar_add_bytes(tf, "b", b"y")
  assert not tar_has_duplicate_file_members(str(tar_path))


def test_dedupe_tar_keep_largest_file_per_member_keeps_largest(tmp_path):
  tar_path = tmp_path / "dedupe.tar"
  with tarfile.open(tar_path, "w") as tf:
    _tar_add_bytes(tf, "p/q", b"small")
    _tar_add_bytes(tf, "p/q", b"muchlonger")
  assert dedupe_tar_keep_largest_file_per_member(str(tar_path), log_fn=None)
  assert not tar_has_duplicate_file_members(str(tar_path))
  assert get_existing_archive_members(str(tar_path))["p/q"] == 10
  with tarfile.open(tar_path, "r") as tf:
    names = [m.name for m in tf.getmembers() if m.isfile()]
  assert names == ["p/q"]


def test_dedupe_tar_invalidates_members_cache(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2026-06-03.tar"
  with tarfile.open(tar_path, "w") as tf:
    _tar_add_bytes(tf, "a", b"x")
    _tar_add_bytes(tf, "a", b"yy")
  invalidated = []
  monkeypatch.setattr(
      helpers,
      "invalidate_after_daily_tar_mutation",
      lambda path, **kw: invalidated.append(path),
  )
  assert helpers.dedupe_tar_keep_largest_file_per_member(str(tar_path), log_fn=None)
  assert invalidated == [str(tar_path)]


def test_invalidate_after_daily_tar_mutation_from_tar_path(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2026-06-04.tar"
  tar_path.write_bytes(b"")
  dropped = []
  monkeypatch.setattr(
      helpers,
      "invalidate_daily_archive_members_cache",
      lambda path: dropped.append(path),
  )
  helpers.invalidate_after_daily_tar_mutation(
      str(tar_path),
      reason="test",
      log_fn=None,
  )
  assert dropped == [str(tmp_path / "2026-06-04.tar.zst")]


def test_dedupe_sealed_daily_archive_last_resort(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  zst_path = tmp_path / "2026-06-01.tar.zst"
  zst_path.write_bytes(b"sealed")
  calls = []

  monkeypatch.setattr(
      helpers,
      "decompress_compressed_to_tar",
      lambda *_a, **_k: calls.append("decompress") or True,
  )
  monkeypatch.setattr(
      helpers,
      "dedupe_tar_keep_largest_file_per_member",
      lambda *_a, **_k: calls.append("dedupe") or True,
  )
  monkeypatch.setattr(
      helpers,
      "atomic_seal_tar_to_zst",
      lambda *_a, **_k: calls.append("seal"),
  )
  monkeypatch.setattr(
      helpers,
      "invalidate_after_daily_tar_mutation",
      lambda *_a, **_k: calls.append("invalidate"),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.dedupe_hint_is_set",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.clear_dedupe_hint",
      lambda *_a, **_k: calls.append("clear_hint"),
  )
  assert helpers.dedupe_sealed_daily_archive(str(zst_path), log_fn=None)
  assert calls == ["decompress", "dedupe", "seal", "invalidate", "clear_hint"]


def test_get_existing_archive_members_uses_redis_l2(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  zst_path = tmp_path / "2026-06-02.tar.zst"
  zst_path.write_bytes(b"z")
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.redis_lookup_full_members",
      lambda keys: {"cached/member": 11},
  )
  members = helpers.get_existing_archive_members_for_daily_archive(str(zst_path))
  assert members == {"cached/member": 11}


def test_dedupe_tar_tie_same_size_keeps_last(tmp_path):
  """Equal sizes: last archive entry is retained."""
  tar_path = tmp_path / "tie.tar"
  with tarfile.open(tar_path, "w") as tf:
    _tar_add_bytes(tf, "n", b"abcd")
    _tar_add_bytes(tf, "n", b"wxyz")
  assert dedupe_tar_keep_largest_file_per_member(str(tar_path), log_fn=None)
  with tarfile.open(tar_path, "r") as tf:
    bodies = [tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()]
  assert bodies == [b"wxyz"]


# --- filter_files_to_add_to_archive ---


def test_filter_files_to_add_to_archive_all_new(tmp_path):
  """When existing_members is empty, all files are to add."""
  f1 = tmp_path / "f1"
  f2 = tmp_path / "f2"
  f1.write_text("a")
  f2.write_text("bb")
  to_add = filter_files_to_add_to_archive(
      [str(f1), str(f2)], {})
  assert set(to_add) == {str(f1), str(f2)}


def test_filter_files_to_add_to_archive_already_present_same_size(tmp_path):
  """File already in archive with same size is not added."""
  f1 = tmp_path / "f1"
  f1.write_text("ab")
  # member name for path like /tmp/.../f1 is f1 (or full path without leading /)
  member_name = get_tar_member_name(str(f1))
  existing = {member_name: 2}
  to_add = filter_files_to_add_to_archive([str(f1)], existing)
  assert to_add == []


def test_filter_files_to_add_to_archive_present_different_size(tmp_path):
  """File in archive with different size is added."""
  f1 = tmp_path / "f1"
  f1.write_text("abc")
  member_name = get_tar_member_name(str(f1))
  existing = {member_name: 2}
  to_add = filter_files_to_add_to_archive([str(f1)], existing)
  assert to_add == [str(f1)]


def test_filter_files_to_add_to_archive_mixed(tmp_path):
  """Mix of new, same size, and different size."""
  f1 = tmp_path / "f1"
  f2 = tmp_path / "f2"
  f3 = tmp_path / "f3"
  f1.write_text("x")
  f2.write_text("yy")
  f3.write_text("zzz")
  # f2 already in archive with same size; f1 and f3 not in archive
  existing = {get_tar_member_name(str(f2)): 2}
  to_add = filter_files_to_add_to_archive(
      [str(f1), str(f2), str(f3)], existing)
  assert set(to_add) == {str(f1), str(f3)}


# --- sync_timedb._append_to_tar (tar -r --null -T) ---


@pytest.mark.skipif(not shutil.which("tar"), reason="tar binary required")
def test_append_to_tar_writes_members_via_files_from(tmp_path):
  from hpcperfstats.dbload.sync_timedb import _append_to_tar

  f1 = tmp_path / "segment1"
  f1.write_text("segment-body")
  tar_path = tmp_path / "2024-06-01.tar"
  _append_to_tar(str(tar_path), [str(f1)])
  assert tar_path.is_file()
  with tarfile.open(tar_path, "r") as tf:
    bodies = [
        tf.extractfile(m).read()
        for m in tf.getmembers()
        if m.isfile() and tf.extractfile(m) is not None
    ]
  assert b"segment-body" in bodies


def test_append_to_tar_argv_always_includes_posix(monkeypatch, tmp_path):
  """Create (-c) and append (-r) both pass --posix and -C / for large-member pax."""
  from hpcperfstats.dbload import sync_timedb as st

  f1 = tmp_path / "segment1"
  f1.write_bytes(b"a")
  f2 = tmp_path / "segment2"
  f2.write_bytes(b"b")
  tar_path = tmp_path / "2024-06-01.tar"
  captured = []

  class _Ok:
    returncode = 0
    stdout = ""
    stderr = ""
    args = ()

  def _run(args, **_kwargs):
    captured.append(list(args))
    # Pretend create succeeded so second call uses -r.
    if "-c" in args:
      tar_path.write_bytes(b"ustar\0")
    return _Ok()

  monkeypatch.setattr(st.subprocess, "run", _run)
  monkeypatch.setattr(
      st, "file_write_lock", lambda *_a, **_k: contextlib.nullcontext())
  st._append_to_tar(str(tar_path), [str(f1)])
  st._append_to_tar(str(tar_path), [str(f2)])
  assert len(captured) == 2
  assert "-c" in captured[0] and "--posix" in captured[0]
  assert "-C" in captured[0] and captured[0][captured[0].index("-C") + 1] == "/"
  assert "-r" in captured[1] and "--posix" in captured[1]
  assert "-C" in captured[1] and captured[1][captured[1].index("-C") + 1] == "/"


def test_format_tar_append_failure_log_includes_stderr():
  from hpcperfstats.dbload.sync_timedb import format_tar_append_failure_log

  exc = subprocess.CalledProcessError(
      2,
      ["tar", "-r", "-f", "day.tar"],
      output="",
      stderr=(
          "/usr/bin/tar: value 11109288094 out of off_t range "
          "0..8589934591\n"
      ),
  )
  msg = format_tar_append_failure_log("day.tar", exc, retry=False)
  assert msg.startswith("ERROR: tar append failed for day.tar")
  assert "tar append stderr:" in msg
  assert "out of off_t range" in msg
  assert "8589934591" in msg
  assert "marker=off_t_range" in msg
  retry_msg = format_tar_append_failure_log("day.tar", exc, retry=True)
  assert retry_msg.startswith("ERROR: retry tar append failed for day.tar")
  assert "tar append stderr:" in retry_msg
  assert "marker=off_t_range" in retry_msg


def test_archive_stats_files_error_log_includes_tar_stderr(monkeypatch, tmp_path):
  """ERROR: tar append failed must fold CalledProcessError.stderr for grep."""
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01.tar.zst")

  import hpcperfstats.dbload.sync_timedb as st

  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "_decompress_compressed_archive", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "get_existing_archive_members", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st, "filter_files_to_add_to_archive", lambda files, *_a, **_k: list(files))
  monkeypatch.setattr(st, "_restore_daily_tar_or_log_failure", lambda *a, **k: True)

  def _boom(*_a, **_k):
    raise subprocess.CalledProcessError(
        2,
        ["tar", "-r", "--posix"],
        output="",
        stderr="tar: value 9000000000 out of off_t range 0..8589934591\n",
    )

  monkeypatch.setattr(st, "_append_to_tar", _boom)
  logs = []
  monkeypatch.setattr(st, "log_print", lambda msg, **_k: logs.append(str(msg)))

  assert st.archive_stats_files((archive_key, [str(raw_file)])) is False
  error_lines = [line for line in logs if line.startswith("ERROR: tar append failed")]
  assert error_lines
  assert "tar append stderr:" in error_lines[0]
  assert "out of off_t range" in error_lines[0]
  assert raw_file.exists()


def test_partition_and_sort_archive_items_helpers(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      USTAR_MAX_MEMBER_BYTES,
      partition_paths_by_ustar_member_limit,
      sort_archive_items_oldest_day_first,
  )

  small = tmp_path / "small"
  small.write_bytes(b"abc")
  within, oversized = partition_paths_by_ustar_member_limit([str(small)])
  assert within == [str(small)]
  assert oversized == []

  items = [
      ("/d/2026-03-02.tar.zst", ["b"]),
      ("/d/2026-03-01.tar.zst", ["a"]),
  ]
  assert [i[0] for i in sort_archive_items_oldest_day_first(items)] == [
      "/d/2026-03-01.tar.zst",
      "/d/2026-03-02.tar.zst",
  ]
  assert USTAR_MAX_MEMBER_BYTES == 8589934591


@pytest.mark.skipif(not shutil.which("tar"), reason="tar binary required")
def test_convert_daily_tar_to_pax_via_extract_recreate(tmp_path, monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  member = tmp_path / "m1"
  member.write_bytes(b"payload")
  tar_path = tmp_path / "2026-06-01.tar"
  # Classic ustar create (no --format=pax).
  subprocess.run(
      ["tar", "-cf", str(tar_path), "-C", str(tmp_path), member.name],
      check=True,
      capture_output=True,
  )
  logs = []
  monkeypatch.setattr(
      helpers, "file_write_lock", lambda *_a, **_k: contextlib.nullcontext())
  assert helpers.convert_daily_tar_to_pax_via_extract_recreate(
      str(tar_path), log_fn=lambda m, **_k: logs.append(str(m)),
  )
  assert any("convert_done" in line for line in logs)
  assert tar_path.is_file()


def test_prepare_paths_convert_fail_skips_oversized(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  small = tmp_path / "small"
  small.write_bytes(b"ok")
  giant = tmp_path / "giant"
  giant.write_bytes(b"g")
  tar_path = tmp_path / "2026-06-01.tar"
  tar_path.write_bytes(b"ustar\0")

  monkeypatch.setattr(
      helpers, "partition_paths_by_ustar_member_limit",
      lambda paths, **_k: ([str(small)], [str(giant)]),
  )
  monkeypatch.setattr(helpers, "is_pax_capable_daily_tar", lambda *_a, **_k: False)
  monkeypatch.setattr(
      helpers, "classify_daily_tar_file_label", lambda *_a, **_k: "POSIX tar archive")
  monkeypatch.setattr(
      helpers, "convert_daily_tar_to_pax_via_extract_recreate",
      lambda *_a, **_k: False,
  )
  logs = []
  to_append, skipped = helpers.prepare_paths_for_giant_member_append(
      str(tar_path),
      [str(small), str(giant)],
      log_fn=lambda m, **_k: logs.append(str(m)),
  )
  assert to_append == [str(small)]
  assert skipped == [str(giant)]
  assert any("must_convert" in line for line in logs)
  assert any("convert_fail_skip" in line for line in logs)


def test_archive_stats_files_convert_fail_skip_outcome(monkeypatch, tmp_path):
  """Convert fail skips giants; job still outcome=ok when remaining appends."""
  raw_small = tmp_path / "1000"
  raw_small.write_text("1709123456 job1 cn001\n")
  raw_giant = tmp_path / "1001"
  raw_giant.write_text("1709123457 job2 cn002\n")
  archive_key = str(tmp_path / "2024-03-01.tar.zst")
  tar_path = daily_tar_path_from_compressed(archive_key)
  os.makedirs(os.path.dirname(tar_path) or ".", exist_ok=True)
  Path(tar_path).write_bytes(b"ustar\0")

  import hpcperfstats.dbload.sync_timedb as st

  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "_decompress_compressed_archive", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "get_existing_archive_members", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st, "filter_files_to_add_to_archive", lambda files, *_a, **_k: list(files))
  monkeypatch.setattr(st, "_restore_daily_tar_or_log_failure", lambda *a, **k: True)
  monkeypatch.setattr(
      st,
      "prepare_paths_for_giant_member_append",
      lambda tar, files, **_k: ([str(raw_small)], [str(raw_giant)]),
  )
  appended = []

  def _append(tar, paths):
    appended.extend(list(paths))

  monkeypatch.setattr(st, "_append_to_tar", _append)
  logs = []
  monkeypatch.setattr(st, "log_print", lambda msg, **_k: logs.append(str(msg)))

  result = st.archive_stats_files(
      (archive_key, [str(raw_small), str(raw_giant)]),
  )
  assert result
  assert appended == [str(raw_small)]
  assert getattr(result, "skipped_paths", ()) == (str(raw_giant),)
  assert any("outcome=ok" in line for line in logs if "archive_job_done" in line)


def test_archive_stats_files_error_log_outcome_fail(monkeypatch, tmp_path):
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01.tar.zst")

  import hpcperfstats.dbload.sync_timedb as st

  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "_decompress_compressed_archive", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "get_existing_archive_members", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st, "filter_files_to_add_to_archive", lambda files, *_a, **_k: list(files))
  monkeypatch.setattr(st, "_restore_daily_tar_or_log_failure", lambda *a, **k: True)
  monkeypatch.setattr(
      st,
      "prepare_paths_for_giant_member_append",
      lambda tar, files, **_k: (list(files), []),
  )

  def _boom(*_a, **_k):
    raise subprocess.CalledProcessError(
        2,
        ["tar", "-r", "--posix"],
        output="",
        stderr="tar: value 9000000000 out of off_t range 0..8589934591\n",
    )

  monkeypatch.setattr(st, "_append_to_tar", _boom)
  logs = []
  monkeypatch.setattr(st, "log_print", lambda msg, **_k: logs.append(str(msg)))

  assert st.archive_stats_files((archive_key, [str(raw_file)])) is False
  assert any(
      "archive_job_done" in line and "outcome=fail" in line for line in logs
  )


# --- get_verified_files_to_remove ---


def test_get_verified_files_to_remove_none_in_archive(tmp_path):
  """When no members match, nothing to remove."""
  f1 = tmp_path / "f1"
  f1.write_text("a")
  to_remove = get_verified_files_to_remove([str(f1)], {})
  assert to_remove == []


def test_get_verified_files_to_remove_same_size(tmp_path):
  """File in archive with same size is verified for removal."""
  f1 = tmp_path / "f1"
  f1.write_text("ab")
  member_name = get_tar_member_name(str(f1))
  existing = {member_name: 2}
  to_remove = get_verified_files_to_remove([str(f1)], existing)
  assert to_remove == [str(f1)]


def test_get_verified_files_to_remove_different_size(tmp_path):
  """File in archive with different size is not verified for removal."""
  f1 = tmp_path / "f1"
  f1.write_text("abc")
  member_name = get_tar_member_name(str(f1))
  existing = {member_name: 2}
  to_remove = get_verified_files_to_remove([str(f1)], existing)
  assert to_remove == []


# --- get_stats_chunk ---


def test_get_stats_chunk_full_chunk():
  """Full chunk returns correct slice."""
  files = ["a", "b", "c", "d", "e"]
  assert get_stats_chunk(files, 0, 2) == ["a", "b"]
  assert get_stats_chunk(files, 1, 2) == ["c", "d"]
  assert get_stats_chunk(files, 2, 2) == ["e"]


def test_get_stats_chunk_empty_list():
  """Empty list returns empty slice."""
  assert get_stats_chunk([], 0, 10) == []


def test_get_stats_chunk_out_of_range():
  """Chunk index beyond data returns empty slice."""
  assert get_stats_chunk(["a", "b"], 2, 2) == []


# --- collect_stats_files_in_range ---
# Subdirs must end with this suffix (mirrors DEFAULT.host_name_ext).
_ARCH_HOST_SUFFIX = "cluster.integration.test"


def test_stats_file_is_active_segment_hardlinked_to_current(tmp_path):
  """Epoch file same inode as current is the live segment listend writes."""
  host = tmp_path / ("h." + _ARCH_HOST_SUFFIX)
  host.mkdir()
  cur = host / "current"
  epoch = host / "1000"
  cur.write_text("x")
  try:
    os.link(str(cur), str(epoch))
  except OSError:
    pytest.skip("hard links not supported on this filesystem")
  assert stats_file_is_active_segment(str(epoch)) is True


def test_stats_file_is_active_segment_false_when_not_linked(tmp_path):
  """Closed epoch file has its own inode vs current."""
  host = tmp_path / ("h." + _ARCH_HOST_SUFFIX)
  host.mkdir()
  (host / "current").write_text("live")
  closed = host / "2000"
  closed.write_text("segment")
  assert stats_file_is_active_segment(str(closed)) is False


def test_stats_file_is_active_segment_false_no_current(tmp_path):
  """No current file means treat as not active (e.g. copied archive)."""
  host = tmp_path / ("h." + _ARCH_HOST_SUFFIX)
  host.mkdir()
  (host / "3000").write_text("orphan")
  assert stats_file_is_active_segment(str(host / "3000")) is False


def test_collect_stats_files_in_range_skips_active_hardlinked_epoch(tmp_path):
  """Do not queue epoch files still linked to current (race with listend)."""
  host = tmp_path / ("n." + _ARCH_HOST_SUFFIX)
  host.mkdir()
  cur = host / "current"
  active_epoch = host / "11111"
  cur.write_text("growing")
  try:
    os.link(str(cur), str(active_epoch))
  except OSError:
    pytest.skip("hard links not supported on this filesystem")
  closed = host / "22222"
  closed.write_text("done")
  t = datetime(2020, 6, 15).timestamp()
  os.utime(active_epoch, (t, t))
  os.utime(closed, (t, t))
  result = collect_stats_files_in_range(
      str(tmp_path), datetime(2020, 6, 1), datetime(2020, 7, 1),
      _ARCH_HOST_SUFFIX)
  assert not any(p.endswith("11111") for p in result)
  assert any(p.endswith("22222") for p in result)


def test_collect_stats_files_in_range_no_subdirs(tmp_path):
  """No subdirs ending with host_name_ext returns empty list."""
  (tmp_path / "other").mkdir()
  (tmp_path / "other" / "file").write_text("x")
  assert collect_stats_files_in_range(
      str(tmp_path),
      datetime(2020, 1, 1),
      datetime(2020, 1, 10),
      _ARCH_HOST_SUFFIX,
  ) == []


def test_collect_stats_files_in_range_empty_suffix_returns_nothing(tmp_path):
  """Blank host_name_ext yields no files."""
  host = tmp_path / ("n1." + _ARCH_HOST_SUFFIX)
  host.mkdir()
  (host / "1").write_text("x")
  t = datetime(2020, 6, 15).timestamp()
  os.utime(host / "1", (t, t))
  assert collect_stats_files_in_range(
      str(tmp_path), datetime(2020, 6, 1), datetime(2020, 7, 1), "") == []
  assert collect_stats_files_in_range(
      str(tmp_path), datetime(2020, 6, 1), datetime(2020, 7, 1), "   ") == []


def test_collect_stats_files_in_range_skips_current(tmp_path):
  """Files named 'current*' are skipped."""
  cn = tmp_path / ("cn001." + _ARCH_HOST_SUFFIX)
  cn.mkdir()
  (cn / "current").write_text("x")
  (cn / "12345").write_text("y")
  # Set mtime so 12345 is in range
  t = datetime(2020, 6, 15, 12, 0, 0).timestamp()
  os.utime(cn / "12345", (t, t))
  os.utime(cn / "current", (t, t))
  start = datetime(2020, 6, 1)
  end = datetime(2020, 7, 1)
  result = collect_stats_files_in_range(
      str(tmp_path), start, end, _ARCH_HOST_SUFFIX)
  assert len(result) == 1
  assert result[0].endswith("12345")


def test_collect_stats_files_in_range_skips_lock_files(tmp_path):
  """Sidecar lock files must not be treated as stats segments."""
  cn = tmp_path / ("cn001." + _ARCH_HOST_SUFFIX)
  cn.mkdir()

  epoch_ts = int(datetime(2020, 6, 15, 12, 0, 0).timestamp())
  stats_path = cn / str(epoch_ts)
  lock_path = cn / f"{epoch_ts}{LOCK_SUFFIX}"

  stats_path.write_text("segment-data")
  lock_path.write_text("lock-data")

  # Ensure both would be in-range if discovery did not filter lock files.
  start = datetime(2020, 6, 1)
  end = datetime(2020, 7, 1)
  t = datetime(2020, 6, 16, 12, 0, 0).timestamp()
  os.utime(stats_path, (t, t))
  os.utime(lock_path, (t, t))

  result = collect_stats_files_in_range(
      str(tmp_path), start, end, _ARCH_HOST_SUFFIX)
  basenames = [os.path.basename(p) for p in result]
  assert str(epoch_ts) in basenames
  assert f"{epoch_ts}{LOCK_SUFFIX}" not in basenames


def test_collect_stats_files_in_range_non_matching_suffix_skipped(tmp_path):
  """Subdirs that do not end with host_name_ext are ignored."""
  wrong = tmp_path / "cn001.other.domain"
  wrong.mkdir()
  (wrong / "99").write_text("x")
  t = datetime(2020, 6, 15).timestamp()
  os.utime(wrong / "99", (t, t))
  result = collect_stats_files_in_range(
      str(tmp_path), datetime(2020, 6, 1), datetime(2020, 7, 1),
      _ARCH_HOST_SUFFIX)
  assert result == []


def test_collect_stats_files_in_range_date_filter(tmp_path):
  """Files outside date range are excluded."""
  cn = tmp_path / ("cn001." + _ARCH_HOST_SUFFIX)
  cn.mkdir()
  old_f = cn / "1"
  new_f = cn / "2"
  old_f.write_text("a")
  new_f.write_text("b")
  # old: before range, new: inside range
  old_ts = (datetime(2020, 6, 1) - timedelta(days=2)).timestamp()
  new_ts = datetime(2020, 6, 15).timestamp()
  os.utime(old_f, (old_ts, old_ts))
  os.utime(new_f, (new_ts, new_ts))
  start = datetime(2020, 6, 1)
  end = datetime(2020, 7, 1)
  result = collect_stats_files_in_range(
      str(tmp_path), start, end, _ARCH_HOST_SUFFIX)
  assert len(result) == 1
  assert result[0].endswith("2")


def test_collect_stats_files_in_range_includes_same_day_when_end_has_time(tmp_path):
  """End datetime with time should include same-day files after midnight."""
  cn = tmp_path / ("cn001." + _ARCH_HOST_SUFFIX)
  cn.mkdir()
  same_day = cn / "same-day"
  same_day.write_text("x")
  same_day_ts = datetime(2020, 6, 15, 12, 0, 0).timestamp()
  os.utime(same_day, (same_day_ts, same_day_ts))
  result = collect_stats_files_in_range(
      str(tmp_path),
      datetime(2020, 6, 8, 0, 0, 0),
      datetime(2020, 6, 15, 13, 0, 0),
      _ARCH_HOST_SUFFIX,
  )
  assert str(same_day) in result


def test_collect_lock_sidecar_stats_counts_stale_and_age(tmp_path):
  fresh = tmp_path / "fresh.fnctl.lock"
  stale = tmp_path / "stale.fnctl.lock"
  fresh.write_text("")
  stale.write_text("")
  now = time.time()
  os.utime(fresh, (now - 5, now - 5))
  os.utime(stale, (now - 100, now - 100))
  stats = collect_lock_sidecar_stats(str(tmp_path), stale_after_seconds=60)
  assert stats["lock_files"] == 2
  assert stats["stale_lock_files"] == 1
  assert stats["oldest_lock_age_seconds"] >= 100


def test_collect_stats_files_in_range_sorted_oldest_first(tmp_path):
  """Results are sorted by effective timestamp, oldest first (supervisor ingest order)."""
  cn = tmp_path / ("cn001." + _ARCH_HOST_SUFFIX)
  cn.mkdir()
  # Three files: base names are epoch seconds one minute apart.
  base_ts = datetime(2020, 6, 15, 12, 0, 0)
  epochs = [
      int((base_ts + timedelta(minutes=offset)).timestamp())
      for offset in [0, 1, 2]
  ]
  for ts in epochs:
    p = cn / str(ts)
    p.write_text("x")
    os.utime(p, (ts, ts))
  start = datetime(2020, 6, 1)
  end = datetime(2020, 7, 1)
  result = collect_stats_files_in_range(
      str(tmp_path), start, end, _ARCH_HOST_SUFFIX)
  basenames = [os.path.basename(p) for p in result]
  # Expect oldest (smallest epoch) first.
  assert basenames == [str(epochs[0]), str(epochs[1]), str(epochs[2])]


def test_collect_stats_files_in_range_parallel_multi_host(monkeypatch, tmp_path):
  """Multi-host collect uses ingest pool cap and preserves oldest-first order."""
  monkeypatch.setattr(cfg, "get_sync_ingest_pool_processes", lambda: 4)
  base_ts = datetime(2020, 6, 15, 12, 0, 0)
  epochs = [
      int((base_ts + timedelta(minutes=offset)).timestamp())
      for offset in [2, 0, 1]
  ]
  host_dirs = []
  for host_idx in range(3):
    cn = tmp_path / ("cn%03d." % host_idx + _ARCH_HOST_SUFFIX)
    cn.mkdir()
    host_dirs.append(cn)
    ts = epochs[host_idx]
    p = cn / str(ts)
    p.write_text("x")
    os.utime(p, (ts, ts))

  logs = []
  result = collect_stats_files_in_range(
      str(tmp_path),
      "all",
      None,
      _ARCH_HOST_SUFFIX,
      log_fn=lambda msg, **kw: logs.append(msg),
  )
  assert len(result) == 3
  assert result == sorted(result, key=lambda path: int(os.path.basename(path)))
  joined = "\n".join(logs)
  assert "collect_stats_files_in_range: hosts=3 workers=3" in joined


def test_rescan_pending_stats_files_excludes_processed_and_keeps_oldest_first(tmp_path):
  """Rescan excludes already processed files and keeps oldest-first ordering."""
  cn = tmp_path / ("cn001." + _ARCH_HOST_SUFFIX)
  cn.mkdir()
  base_ts = datetime(2020, 6, 15, 12, 0, 0)
  old_epoch = int(base_ts.timestamp())
  mid_epoch = int((base_ts + timedelta(minutes=1)).timestamp())
  new_epoch = int((base_ts + timedelta(minutes=2)).timestamp())
  for ts in [old_epoch, mid_epoch, new_epoch]:
    p = cn / str(ts)
    p.write_text("x")
    os.utime(p, (ts, ts))

  start = datetime(2020, 6, 1)
  end = datetime(2020, 7, 1)
  processed = {str(cn / str(new_epoch))}
  pending = rescan_pending_stats_files(
      str(tmp_path), start, end, _ARCH_HOST_SUFFIX, processed)

  assert [os.path.basename(p) for p in pending] == [str(old_epoch), str(mid_epoch)]


def test_collect_stats_files_in_range_uses_filename_epoch_when_mtime_outside(tmp_path):
  """Filename epoch within range causes inclusion even if mtime is outside."""
  cn = tmp_path / ("cn001." + _ARCH_HOST_SUFFIX)
  cn.mkdir()

  # Filename encodes an epoch in June 2020, but mtime is in January 2020.
  fname_ts = datetime(2020, 6, 15, 12, 0, 0).timestamp()
  stats_file = cn / str(int(fname_ts))
  stats_file.write_text("x")

  mtime_ts = datetime(2020, 1, 1, 0, 0, 0).timestamp()
  os.utime(stats_file, (mtime_ts, mtime_ts))

  start = datetime(2020, 6, 1)
  end = datetime(2020, 7, 1)
  result = collect_stats_files_in_range(
      str(tmp_path), start, end, _ARCH_HOST_SUFFIX)
  assert len(result) == 1
  assert result[0].endswith(str(int(fname_ts)))


def test_collect_stats_files_in_range_all_no_date_filter(tmp_path):
  """startdate 'all' includes every stats file regardless of mtime/filename age."""
  cn = tmp_path / ("c572-001." + _ARCH_HOST_SUFFIX)
  cn.mkdir()
  old_epoch = int(datetime(2018, 1, 1, 12, 0, 0).timestamp())
  new_epoch = int(datetime(2030, 6, 15, 12, 0, 0).timestamp())
  (cn / str(old_epoch)).write_text("a")
  (cn / str(new_epoch)).write_text("b")
  t_old = datetime(2010, 1, 1).timestamp()
  t_new = datetime(2035, 1, 1).timestamp()
  os.utime(cn / str(old_epoch), (t_old, t_old))
  os.utime(cn / str(new_epoch), (t_new, t_new))

  result = collect_stats_files_in_range(
      str(tmp_path), "all", None, _ARCH_HOST_SUFFIX)
  basenames = sorted(os.path.basename(p) for p in result)
  assert basenames == [str(old_epoch), str(new_epoch)]


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_tar_to_zst_logs_retention_mode(tmp_path):
  tar_path = tmp_path / "2021-03-03.tar"
  zst_path = tmp_path / "2021-03-03.tar.zst"
  member = tmp_path / "c.txt"
  member.write_text("x")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="c.txt")
  logs = []
  atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
  )
  assert any("retaining uncompressed tar" in line for line in logs)


def test_collect_stats_files_in_range_includes_non_compute_prefix(tmp_path):
  """Any host dirname ending with host_name_ext is scanned (not only c*/v*)."""
  gpu = tmp_path / ("gpu7." + _ARCH_HOST_SUFFIX)
  gpu.mkdir()
  ts = int(datetime(2020, 6, 15, 12, 0, 0).timestamp())
  (gpu / str(ts)).write_text("x")
  os.utime(gpu / str(ts), (ts, ts))
  result = collect_stats_files_in_range(
      str(tmp_path), datetime(2020, 6, 1), datetime(2020, 7, 1),
      _ARCH_HOST_SUFFIX)
  assert len(result) == 1


# --- build_archive_mapping ---


def test_build_archive_mapping_groups_by_date(tmp_path):
  """Files are grouped by archive path from first timestamp in file."""
  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  f1 = tmp_path / "f1"
  f2 = tmp_path / "f2"
  # First line with digit is timestamp (epoch)
  f1.write_text("1709123456 job1 cn001\n")
  f2.write_text("1709123460 job2 cn001\n")
  # Same day -> same archive
  mapping = build_archive_mapping([str(f1), str(f2)], str(tgz_dir))
  assert len(mapping) == 1
  key = list(mapping.keys())[0]
  assert key.endswith(DAILY_ARCHIVE_ZST_SUFFIX)
  assert os.path.basename(key).startswith("2024-02-")  # 1709123456 -> Feb 2024 (tz-dependent)
  assert len(mapping[key]) == 2
  assert not (tmp_path / "f1.fnctl.lock").exists()
  assert not (tmp_path / "f2.fnctl.lock").exists()


def test_build_archive_mapping_skips_no_timestamp(tmp_path):
  """Files with no parseable timestamp are skipped and not in mapping."""
  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  f1 = tmp_path / "f1"
  f1.write_text("no digit line\n")
  mapping = build_archive_mapping([str(f1)], str(tgz_dir))
  assert mapping == {}


def test_build_archive_mapping_summarizes_missing_timestamp_logs(
    tmp_path, monkeypatch,
):
  """Missing-timestamp skips are summarized once (not one log line per path)."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  paths = []
  for i in range(8):
    f = tmp_path / ("bad_%d" % i)
    f.write_text("no digit line\n")
    paths.append(str(f))
  logs = []
  monkeypatch.setattr(
      helpers, "log_print", lambda msg, flush=False: logs.append(msg),
  )
  mapping = helpers.build_archive_mapping(paths, str(tgz_dir))
  assert mapping == {}
  summary = [
      line for line in logs
      if "Unable to find first timestamp in" in line
  ]
  assert len(summary) == 1
  assert "8 path(s)" in summary[0]
  assert "sample=" in summary[0]


def test_build_archive_mapping_mock_parser(tmp_path):
  """Custom parse_first_ts_fn can be injected."""
  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  f1 = tmp_path / "f1"
  f1.write_text("anything")
  def parse_mock(lines):
    return ("1709123456", "job1", "cn001")  # fixed timestamp
  mapping = build_archive_mapping(
      [str(f1)], str(tgz_dir), parse_first_ts_fn=parse_mock)
  assert len(mapping) == 1
  assert str(f1) in list(mapping.values())[0]


def test_build_archive_mapping_includes_today(tmp_path):
  """Files with timestamp today are included (closed segments only reach here)."""
  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  f1 = tmp_path / "f1"
  today_ts = datetime.today().replace(hour=12, minute=0, second=0).timestamp()
  f1.write_text("%d job1 cn001\n" % today_ts)
  mapping = build_archive_mapping([str(f1)], str(tgz_dir))
  assert len(mapping) == 1
  key = list(mapping.keys())[0]
  assert key.endswith(datetime.today().strftime("%Y-%m-%d") + DAILY_ARCHIVE_ZST_SUFFIX)
  assert str(f1) in mapping[key]


def test_build_archive_mapping_uses_real_sample_timestamp(monkeypatch, tmp_path):
  """build_archive_mapping should derive archive date from sample content."""
  import datetime as _real_datetime
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  # Make sure the sample's timestamp date is treated as "not today" so we
  # actually get an archive mapping regardless of the current system date.
  class _FakeDateTime(_real_datetime.datetime):
    @classmethod
    def today(cls):
      return _real_datetime.datetime(1999, 1, 1, 12, 0, 0)

  monkeypatch.setattr(helpers, "datetime", _FakeDateTime)

  sample_path = os.path.abspath(
      os.path.join(
          os.path.dirname(os.path.realpath(__file__)),
          "..",
          "dbload",
          "tests",
          "HPCPerfStatsdDataSample",
      )
  )
  with open(sample_path, "r") as fd:
    sample_contents = fd.read()

  sample_lines = sample_contents.splitlines(True)
  sample_t, _sample_jid, _sample_host = parse_first_timestamp_line(sample_lines)
  assert sample_t is not None

  sample_file_date = _real_datetime.datetime.fromtimestamp(float(sample_t))
  expected_key = os.path.join(
      str(tmp_path / "tgz"),
      sample_file_date.strftime("%Y-%m-%d") + DAILY_ARCHIVE_ZST_SUFFIX,
  )

  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()

  # Filename is irrelevant for build_archive_mapping; only file contents' first
  # timestamp line is used.
  stats_file = tmp_path / "stats_file_sample"
  stats_file.write_text(sample_contents)

  mapping = build_archive_mapping([str(stats_file)], str(tgz_dir))
  assert len(mapping) == 1
  assert expected_key in mapping
  assert mapping[expected_key] == [str(stats_file)]


def test_build_archive_mapping_accepts_placeholder_jid(tmp_path):
  """Files whose first timestamp line has jid '-' still map into daily archives."""
  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  stats_file = tmp_path / "stats_missing_jid"
  stats_file.write_text("1709123456 - cn001\n!cpu user\ncpu 0 10\n")

  mapping = build_archive_mapping([str(stats_file)], str(tgz_dir))
  assert len(mapping) == 1
  only_key = next(iter(mapping.keys()))
  assert only_key.endswith(DAILY_ARCHIVE_ZST_SUFFIX)
  assert mapping[only_key] == [str(stats_file)]


def test_build_archive_mapping_uses_precomputed_first_timestamp(tmp_path):
  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  stats_file = tmp_path / "stats1"
  stats_file.write_text("1709123456 job1 cn001\n")
  mapping = build_archive_mapping(
      [str(stats_file)],
      str(tgz_dir),
      first_timestamp_by_path={str(stats_file): "1709123456"},
  )
  assert len(mapping) == 1
  assert str(stats_file) in list(mapping.values())[0]


def test_collect_first_timestamps_by_path(tmp_path):
  f1 = tmp_path / "f1"
  f2 = tmp_path / "f2"
  f1.write_text("1709123456 job1 cn001\n")
  f2.write_text("no timestamp\n")
  timestamps = collect_first_timestamps_by_path([str(f1), str(f2)])
  assert timestamps == {str(f1): "1709123456"}


def test_rescan_pending_stats_files_uses_hints_with_periodic_full_sweep(tmp_path):
  host = tmp_path / ("n." + _ARCH_HOST_SUFFIX)
  host.mkdir()
  ts = int(datetime(2020, 6, 15, 12, 0, 0).timestamp())
  f1 = host / str(ts)
  f1.write_text("x")
  os.utime(f1, (ts, ts))
  hints = {}
  first = rescan_pending_stats_files(
      str(tmp_path),
      datetime(2020, 6, 1),
      datetime(2020, 7, 1),
      _ARCH_HOST_SUFFIX,
      set(),
      host_scan_hints=hints,
      full_rescan_every=2,
  )
  second = rescan_pending_stats_files(
      str(tmp_path),
      datetime(2020, 6, 1),
      datetime(2020, 7, 1),
      _ARCH_HOST_SUFFIX,
      set(),
      host_scan_hints=hints,
      full_rescan_every=2,
  )
  third = rescan_pending_stats_files(
      str(tmp_path),
      datetime(2020, 6, 1),
      datetime(2020, 7, 1),
      _ARCH_HOST_SUFFIX,
      set(),
      host_scan_hints=hints,
      full_rescan_every=2,
  )
  assert len(first) == 1
  assert len(second) in (0, 1)
  assert len(third) == 1


def _patch_archive_gate_pass(monkeypatch):
  import hpcperfstats.dbload.sync_timedb as st

  monkeypatch.setattr(
      st,
      "filter_paths_head_ingested",
      lambda paths, **_: (list(paths), []),
  )


def test_archive_stats_files_does_not_raise_on_append_failure(monkeypatch, tmp_path):
  """Append failure keeps raw files in place and returns cleanly for retry."""
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01.tar.zst")

  import hpcperfstats.dbload.sync_timedb as st

  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "_decompress_compressed_archive", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "get_existing_archive_members", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st, "filter_files_to_add_to_archive", lambda files, *_a, **_k: list(files))

  def _boom(*_a, **_k):
    raise subprocess.CalledProcessError(2, ["tar", "-r"])

  monkeypatch.setattr(st, "_append_to_tar", _boom)

  archive_stats_files((archive_key, [str(raw_file)]))
  assert raw_file.exists()


def test_archive_stats_files_does_not_raise_on_fail_closed_append_guard(
    monkeypatch, tmp_path,
):
  """Fail-closed RuntimeError from _append_to_tar returns cleanly for retry."""
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01b.tar.zst")

  import hpcperfstats.dbload.sync_timedb as st

  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "_decompress_compressed_archive", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "get_existing_archive_members", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st, "filter_files_to_add_to_archive", lambda files, *_a, **_k: list(files))
  monkeypatch.setattr(st, "_restore_daily_tar_or_log_failure", lambda *a, **k: True)

  def _fail_closed(*_a, **_k):
    raise RuntimeError(
        "refusing to create daily tar while sealed archive exists without "
        "restored sibling: /daily/2024-03-01b.tar")

  monkeypatch.setattr(st, "_append_to_tar", _fail_closed)
  assert st.archive_stats_files((archive_key, [str(raw_file)])) is False
  assert raw_file.exists()


@pytest.mark.skipif(not shutil.which("tar"), reason="tar binary required")
def test_archive_stats_files_creates_new_tar_when_missing(monkeypatch, tmp_path):
  """Missing daily archive should be bootstrapped as a new .tar."""
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01.tar.zst")
  archive_tar = daily_tar_path_from_compressed(archive_key)
  assert not os.path.exists(archive_key)
  assert not os.path.exists(archive_tar)

  _patch_archive_gate_pass(monkeypatch)
  assert archive_stats_files((archive_key, [str(raw_file)]))
  assert os.path.exists(archive_tar)
  members = get_existing_archive_members(archive_tar)
  assert get_tar_member_name(str(raw_file)) in members


def test_archive_stats_files_skips_dedupe_in_append_path(monkeypatch, tmp_path):
  """Duplicate-member dedupe should run in maintenance, not per append."""
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01.tar.zst")

  import hpcperfstats.dbload.sync_timedb as st

  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "_decompress_compressed_archive", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "get_existing_archive_members", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st, "filter_files_to_add_to_archive", lambda files, *_a, **_k: list(files))
  monkeypatch.setattr(st, "_append_to_tar", lambda *_a, **_k: None)

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  dedupe_calls = {"count": 0}
  monkeypatch.setattr(
      helpers,
      "dedupe_tar_keep_largest_file_per_member",
      lambda *_a, **_k: dedupe_calls.__setitem__("count", dedupe_calls["count"] + 1) or True,
  )

  archive_stats_files((archive_key, [str(raw_file)]))
  assert dedupe_calls["count"] == 0


def test_process_tar_chunk_stops_when_shutdown_requested(monkeypatch):
  """Sliding-window processing should stop early when shutdown flips true."""
  def fake_sliding_window(_pool, _worker, tasks, **kwargs):
    del kwargs
    for item in tasks:
      yield item[1]

  monkeypatch.setattr(sta, "imap_sliding_window_watch_pool", fake_sliding_window)
  monkeypatch.setattr(
      st,
      "_prewarm_archive_members_redis_for_sealed_chunk",
      lambda _paths: "-",
  )

  shutdown_requested[0] = False
  try:
    results = []

    def _capture(item):
      results.append(item)
      shutdown_requested[0] = True

    sta._process_task_chunk_interruptibly(
        object(),
        lambda x: x,
        [("lock", "/a.tar.zst")],
        _capture,
        worker_registry={},
    )
    assert results == ["/a.tar.zst"]
  finally:
    shutdown_requested[0] = False


def test_process_stream_archive_task_spawn_picklable():
  """Spawn Pool workers must unpickle the stream worker callable."""
  from multiprocessing.reduction import ForkingPickler
  import multiprocessing

  ForkingPickler.dumps(sta._process_stream_archive_task)
  mgr = multiprocessing.Manager()
  try:
    lock = mgr.Lock()
    ForkingPickler.dumps((lock, "/tmp/x.tar.zst"))
  finally:
    mgr.shutdown()


def test_configure_blas_thread_env_idempotent():
  """Caps BLAS/OpenMP threads before numpy (avoids pthread EAGAIN under spawn)."""
  sta._configure_blas_thread_env()
  sta._configure_blas_thread_env()


# --- sync_timedb_archive sealed-only backfill ---


def _write_sealed_daily_archive(
    tmp_path,
    day="2024-03-01",
    member_name="stats/host.txt",
    body="1000 sample line\n",
    *,
    keep_tar=False,
):
  if not shutil.which("zstd"):
    pytest.skip("zstd not on PATH")
  tar_p = tmp_path / ("%s.tar" % day)
  zst_p = tmp_path / ("%s.tar.zst" % day)
  inner = tmp_path / "inner.txt"
  inner.write_text(body)
  with tarfile.open(tar_p, "w") as tf:
    tf.add(str(inner), arcname=member_name)
  atomic_seal_tar_to_zst(
      str(tar_p),
      str(zst_p),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=keep_tar,
      log_fn=None,
  )
  return str(zst_p), str(tar_p)


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_resolve_sealed_archive_path_prefers_zst(tmp_path):
  zst_p, tar_p = _write_sealed_daily_archive(tmp_path, keep_tar=True)
  gz_p = str(tmp_path / "2024-03-01.tar.gz")
  with tarfile.open(gz_p, "w:gz") as tf:
    tf.add(str(tmp_path / "inner.txt"), arcname="other.txt")
  assert resolve_sealed_archive_path_for_ingest(zst_p) == zst_p
  assert resolve_sealed_archive_path_for_ingest(tar_p, str(tmp_path)) == zst_p


def test_resolve_sealed_archive_path_none_when_tar_only(tmp_path):
  tar_p = tmp_path / "2024-03-02.tar"
  tar_p.write_text("unsealed")
  assert resolve_sealed_archive_path_for_ingest(str(tar_p), str(tmp_path)) is None


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_iter_sealed_daily_archive_member_lines_zst_only(tmp_path):
  zst_p, tar_p = _write_sealed_daily_archive(
      tmp_path,
      day="2024-03-03",
      member_name="host/stats.txt",
      body="payload-line\n",
      keep_tar=False,
  )
  assert not os.path.isfile(tar_p)
  members = list(iter_sealed_daily_archive_member_lines(zst_p))
  assert len(members) == 1
  assert members[0][0] == "host/stats.txt"
  assert members[0][1] == ["payload-line\n"]


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_iter_sealed_daily_archive_member_paths_spools_to_disk(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      iter_sealed_daily_archive_member_paths,
  )

  zst_p, _tar_p = _write_sealed_daily_archive(
      tmp_path,
      day="2024-03-12",
      member_name="host.example/stats.txt",
      body="payload-line\n",
  )
  spool = str(tmp_path / "spool")
  members = list(iter_sealed_daily_archive_member_paths(zst_p, spool_dir=spool))
  assert len(members) == 1
  assert members[0][0] == "host.example/stats.txt"
  member_path = members[0][1]
  assert os.path.isfile(member_path)
  assert member_path.startswith(spool)
  with open(member_path, "r", encoding="utf-8") as fh:
    assert fh.read() == "payload-line\n"


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_iter_sealed_daily_archive_member_paths_skipped_invokes_callback(
    tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib import conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      iter_sealed_daily_archive_member_paths,
  )

  zst_p, _tar_p = _write_sealed_daily_archive(
      tmp_path,
      day="2024-03-13",
      member_name="host.example/stats.txt",
      body="payload-line\n",
  )
  monkeypatch.setattr(cfg, "get_sync_ingest_max_file_read_bytes", lambda: 1)
  skipped = []
  members = list(
      iter_sealed_daily_archive_member_paths(
          zst_p,
          spool_dir=str(tmp_path / "spool"),
          on_member_skipped=lambda: skipped.append(1),
      ),
  )
  assert members == []
  assert len(skipped) == 1


def test_iter_archive_ingest_tasks_one_stream_per_sealed_day(tmp_path, monkeypatch):
  if not shutil.which("zstd"):
    pytest.skip("zstd not on PATH")
  zst_p, _tar_p = _write_sealed_daily_archive(tmp_path, day="2024-03-04")
  tar_only = tmp_path / "2024-03-05.tar"
  tar_only.write_text("x")
  tasks = list(
      iter_archive_ingest_tasks(
          [zst_p, str(tar_only)],
          str(tmp_path),
      ),
  )
  assert tasks == [(STREAM_ARCHIVE_TASK, zst_p)]


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_collect_sealed_paths_skips_tar_only_day(tmp_path):
  _write_sealed_daily_archive(tmp_path, day="2024-03-06")
  (tmp_path / "2024-03-07.tar").write_text("unsealed")
  start = datetime(2024, 3, 6)
  end = datetime(2024, 3, 7)
  paths, skipped = collect_sealed_daily_archive_paths_in_range(
      str(tmp_path),
      start,
      end,
  )
  assert len(paths) == 1
  assert paths[0].endswith("2024-03-06.tar.zst")
  assert skipped == 1


def test_parse_sync_timedb_archive_argv_single_day():
  mode, start, end, paths = parse_sync_timedb_archive_argv(
      ["sync_timedb_archive.py", "2024-01-15"],
  )
  assert mode == "date"
  assert start == datetime(2024, 1, 15)
  assert end == start
  assert paths == []


def test_parse_sync_timedb_archive_argv_all():
  mode, start, end, paths = parse_sync_timedb_archive_argv(
      ["sync_timedb_archive.py", "all"],
  )
  assert mode == "date"
  assert start == "all"
  assert end is None
  assert paths == []


def test_parse_argv_rejects_plain_tar_path():
  with pytest.raises(SystemExit):
    parse_sync_timedb_archive_argv(
        ["sync_timedb_archive.py", "/data/2024-01-01.tar"],
    )


def test_parse_argv_empty_usage():
  with pytest.raises(SystemExit):
    parse_sync_timedb_archive_argv(["sync_timedb_archive.py"])


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_process_stream_archive_task_ingests_all_members(monkeypatch, tmp_path):
  zst_p, _tar_p = _write_sealed_daily_archive(
      tmp_path,
      day="2024-03-08",
      member_name="m1.txt",
      body="a\n",
  )
  calls = []

  class _Lock:
    pass

  def fake_add(lock, member_path, stats_file_contents=None):
    calls.append((member_path, stats_file_contents))

  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb.add_stats_file_to_db",
      fake_add,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_worker_memory.release_spawn_pool_worker_memory",
      lambda: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.django_bootstrap.ensure_django",
      lambda: None,
  )
  monkeypatch.setattr(
      "django.db.close_old_connections",
      lambda: None,
  )
  sta._process_stream_archive_task((_Lock(), zst_p))
  assert len(calls) == 1
  member_path, contents = calls[0]
  assert contents is None
  assert "m1.txt" in member_path
  assert not os.path.isfile(member_path)


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_ingest_does_not_write_decompress_artifacts(monkeypatch, tmp_path):
  zst_p, _tar_p = _write_sealed_daily_archive(tmp_path, day="2024-03-09")
  before = set(os.listdir(tmp_path))
  decompress_calls = []

  def _spy_decompress(*args, **kwargs):
    decompress_calls.append(args)
    return False

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.decompress_compressed_to_tar",
      _spy_decompress,
  )
  list(iter_sealed_daily_archive_member_lines(zst_p))
  after = set(os.listdir(tmp_path))
  assert decompress_calls == []
  new_files = after - before
  assert not any(
      name.endswith(".tar") and not name.endswith((".tar.zst", ".tar.gz"))
      for name in new_files
  )
  assert all(name.endswith(".fnctl.lock") for name in new_files)


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_sealed_stream_uses_zstd_priority_wrap(monkeypatch, tmp_path):
  zst_p, _tar_p = _write_sealed_daily_archive(tmp_path, day="2024-03-10")
  wrap_calls = []
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  real_open = helpers._open_tarfile_for_read

  @contextlib.contextmanager
  def _spy_open(path, num_threads, *, apply_priority_wrap=True):
    wrap_calls.append(apply_priority_wrap)
    with real_open(
        path, num_threads, apply_priority_wrap=apply_priority_wrap,
    ) as tf:
      yield tf

  monkeypatch.setattr(helpers, "_open_tarfile_for_read", _spy_open)
  list(iter_sealed_daily_archive_member_lines(zst_p))
  assert wrap_calls == [True]


def test_zstd_thread_count_for_wrap_splits_ingest_and_archive(monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(helpers, "get_archive_zstd_thread_count", lambda: 0)
  monkeypatch.setattr(helpers, "get_ingest_zstd_thread_count", lambda: 4)
  assert helpers.zstd_thread_count_for_wrap(True) == 0
  assert helpers.zstd_thread_count_for_wrap(False) == 4


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_unniced_stream_uses_ingest_zstd_thread_count(monkeypatch, tmp_path):
  zst_p, _tar_p = _write_sealed_daily_archive(tmp_path, day="2024-03-12")
  thread_calls = []
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  real_open = helpers._open_tarfile_for_read

  @contextlib.contextmanager
  def _spy_open(path, num_threads, *, apply_priority_wrap=True):
    thread_calls.append((num_threads, apply_priority_wrap))
    with real_open(
        path, num_threads, apply_priority_wrap=apply_priority_wrap,
    ) as tf:
      yield tf

  monkeypatch.setattr(helpers, "get_archive_zstd_thread_count", lambda: 0)
  monkeypatch.setattr(helpers, "get_ingest_zstd_thread_count", lambda: 4)
  monkeypatch.setattr(helpers, "_open_tarfile_for_read", _spy_open)
  helpers._stream_compressed_archive_members(
      zst_p, apply_priority_wrap=False,
  )
  assert thread_calls == [(4, False)]
  thread_calls.clear()
  helpers._stream_compressed_archive_members(
      zst_p, apply_priority_wrap=True,
  )
  assert thread_calls == [(0, True)]


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_stream_archive_skips_oversize_member(monkeypatch, tmp_path):
  zst_p, _tar_p = _write_sealed_daily_archive(tmp_path, day="2024-03-11")
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_max_file_read_bytes",
      lambda: 4,
  )
  members = list(iter_sealed_daily_archive_member_lines(zst_p))
  assert members == []


# --- legacy daily .tar.gz -> .tar.zst migration ---


def test_iter_daily_gz_paths_skips_locks_and_scratch(tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  (tmp_path / "2024-01-01.tar.gz").write_bytes(b"g")
  (tmp_path / "2024-01-02.tar.gz.tmp").write_bytes(b"t")
  (tmp_path / "2024-01-03.tar.decomp.tmp").write_bytes(b"d")
  (tmp_path / "2024-01-04.tar.gz.fnctl.lock").write_bytes(b"l")
  (tmp_path / "not-a-date.tar.gz").write_bytes(b"x")
  found = list(helpers.iter_daily_gz_paths(str(tmp_path)))
  assert found == [str(tmp_path / "2024-01-01.tar.gz")]


def test_migrate_one_dropped_only_when_zst_is_superset(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-10.tar.gz"
  zst_path = tmp_path / "2024-03-10.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")

  def _scan(path, **_kwargs):
    if str(path).endswith(".tar.gz"):
      return True, {"a.txt": 10}
    return True, {"a.txt": 10, "b.txt": 5}

  monkeypatch.setattr(helpers, "_scan_compressed_archive_members_and_readable", _scan)
  monkeypatch.setattr(
      helpers,
      "_sealed_archive_members_via_redis_or_scan",
      lambda path, **kwargs: _scan(path),
  )
  drop_calls = []

  def _drop(gz, zst, log_fn=None, **kwargs):
    drop_calls.append((gz, zst, kwargs))
    gz_path.unlink()

  monkeypatch.setattr(helpers, "drop_legacy_gz_if_equivalent_to_zst", _drop)
  status = helpers.migrate_one_daily_legacy_gz(
      str(gz_path),
      zstd_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert status == helpers.MIGRATE_GZ_STATUS_DROPPED_ONLY
  assert not gz_path.exists()
  assert drop_calls


def test_migrate_one_kept_mismatch_when_zst_missing_gz_member(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-11.tar.gz"
  zst_path = tmp_path / "2024-03-11.tar.zst"
  gz_path.write_bytes(b"gz")
  zst_path.write_bytes(b"zst")

  def _scan(path, **_kwargs):
    if str(path).endswith(".tar.gz"):
      return True, {"a.txt": 10, "b.txt": 20}
    return True, {"a.txt": 10}

  monkeypatch.setattr(helpers, "_scan_compressed_archive_members_and_readable", _scan)
  status = helpers.migrate_one_daily_legacy_gz(
      str(gz_path),
      zstd_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert status == helpers.MIGRATE_GZ_STATUS_KEPT_MISMATCH
  assert gz_path.exists()


def test_migrate_one_skipped_locked_on_write_lock_timeout(monkeypatch, tmp_path):
  import contextlib

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-12.tar.gz"
  gz_path.write_bytes(b"gz")

  @contextlib.contextmanager
  def _timeout_lock(_path, timeout_seconds=0, expiry_seconds=None):
    del timeout_seconds, expiry_seconds
    raise TimeoutError("contended")

  monkeypatch.setattr(helpers, "file_write_lock", _timeout_lock)
  status = helpers.migrate_one_daily_legacy_gz(
      str(gz_path),
      zstd_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert status == helpers.MIGRATE_GZ_STATUS_SKIPPED_LOCKED
  assert gz_path.exists()


def test_migrate_one_gz_only_converts_via_decompress_and_seal(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-13.tar.gz"
  tar_path = tmp_path / "2024-03-13.tar"
  zst_path = tmp_path / "2024-03-13.tar.zst"
  gz_path.write_bytes(b"gz")
  remove_compressed_values = []

  def _decompress(gz, tar, threads, remove_compressed=True):
    del threads
    assert gz == str(gz_path)
    remove_compressed_values.append(remove_compressed)
    tar_path.write_bytes(b"tar-bytes")
    return True

  seal_calls = []

  def _seal(tar, zst, *args, **kwargs):
    del args, kwargs
    seal_calls.append((tar, zst))
    zst_path.write_bytes(b"zst")

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _decompress)
  monkeypatch.setattr(helpers, "atomic_seal_tar_to_zst", _seal)
  monkeypatch.setattr(helpers, "drop_legacy_gz_if_equivalent_to_zst", lambda *a, **k: None)
  status = helpers.migrate_one_daily_legacy_gz(
      str(gz_path),
      zstd_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert status == helpers.MIGRATE_GZ_STATUS_CONVERTED
  assert not gz_path.exists()
  assert zst_path.exists()
  assert seal_calls
  assert remove_compressed_values == [False]


def test_migrate_legacy_dry_run_does_not_mutate(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-14.tar.gz"
  gz_path.write_bytes(b"gz")
  monkeypatch.setattr(helpers, "check_archive_migration_prerequisites", lambda: None)
  summary = helpers.migrate_legacy_daily_gz_archives(
      str(tmp_path),
      dry_run=True,
      log_fn=None,
  )
  assert gz_path.exists()
  assert summary.get("gz_remaining") == 1
  assert summary.get(helpers.MIGRATE_GZ_STATUS_CONVERTED, 0) >= 0


def test_migrate_one_gz_only_avoids_nested_lock_failure(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-15.tar.gz"
  tar_path = tmp_path / "2024-03-15.tar"
  zst_path = tmp_path / "2024-03-15.tar.zst"
  gz_path.write_bytes(b"gz")

  def _decompress(gz, tar, threads, remove_compressed=True):
    del threads
    assert gz == str(gz_path)
    assert tar == str(tar_path)
    # This assertion documents the production failure mode:
    # if remove_compressed=True here, helper-level gzip removal re-locks gz_path
    # while migrate_one_daily_legacy_gz already holds its write lock.
    assert remove_compressed is False
    tar_path.write_bytes(b"tar-bytes")
    return True

  def _seal(tar, zst, *args, **kwargs):
    del args, kwargs
    assert tar == str(tar_path)
    assert zst == str(zst_path)
    zst_path.write_bytes(b"zst")

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _decompress)
  monkeypatch.setattr(helpers, "atomic_seal_tar_to_zst", _seal)
  monkeypatch.setattr(helpers, "drop_legacy_gz_if_equivalent_to_zst", lambda *a, **k: None)

  status = helpers.migrate_one_daily_legacy_gz(
      str(gz_path),
      zstd_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      log_fn=None,
  )

  assert status == helpers.MIGRATE_GZ_STATUS_CONVERTED
  assert not gz_path.exists()
  assert zst_path.exists()


def test_migrate_one_gz_only_uses_tmp_decompress_dir(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz_path = tmp_path / "2024-03-16.tar.gz"
  archive_tar_path = tmp_path / "2024-03-16.tar"
  zst_path = tmp_path / "2024-03-16.tar.zst"
  temp_dir = tmp_path / "tmp-work"
  gz_path.write_bytes(b"gz")
  seen_tar_targets = []

  def _decompress(gz, tar, threads, remove_compressed=True):
    del threads, remove_compressed
    assert gz == str(gz_path)
    seen_tar_targets.append(tar)
    Path(tar).write_bytes(b"tar-bytes")
    return True

  def _seal(tar, zst, *args, **kwargs):
    del args, kwargs
    assert tar.startswith(str(temp_dir))
    assert zst == str(zst_path)
    zst_path.write_bytes(b"zst")

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _decompress)
  monkeypatch.setattr(helpers, "atomic_seal_tar_to_zst", _seal)
  monkeypatch.setattr(helpers, "drop_legacy_gz_if_equivalent_to_zst", lambda *a, **k: None)

  status = helpers.migrate_one_daily_legacy_gz(
      str(gz_path),
      zstd_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      decompress_tmp_dir=str(temp_dir),
      log_fn=None,
  )

  assert status == helpers.MIGRATE_GZ_STATUS_CONVERTED
  assert not gz_path.exists()
  assert zst_path.exists()
  assert not archive_tar_path.exists()
  assert seen_tar_targets
  for path in seen_tar_targets:
    assert path.startswith(str(temp_dir))
    assert not Path(path).exists()


# ---------------------------------------------------------------------------
# Post-chunk hygiene helpers (seal flag, auto-seal gate, day disqualification).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_seal_dirty_only_when_no_remaining_raw_defers_day_with_raw(
    monkeypatch, tmp_path
):
  """only_when_no_remaining_raw skips a dirty day while raw stats remain on disk."""
  from zoneinfo import ZoneInfo

  sealed = []

  def fake_atomic_seal(tar_path, zst_path, *_a, **_k):
    sealed.append(os.path.normpath(tar_path))
    member = tmp_path / "m.txt"
    member.write_text("x")
    with tarfile.open(tar_path, "w") as tf:
      tf.add(str(member), arcname="m.txt")
    atomic_seal_tar_to_zst(
        tar_path, zst_path, num_threads=1, compress_level=6,
        keep_uncompressed_tar=True, log_fn=None,
    )

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.atomic_seal_tar_to_zst",
      fake_atomic_seal,
  )
  tar_with_raw = tmp_path / "2024-01-10.tar"
  tar_clean = tmp_path / "2024-01-11.tar"
  for tar_p in (tar_with_raw, tar_clean):
    tar_p.write_text("dirty")
    zst_p = tmp_path / (tar_p.name + ".zst")
    if zst_p.exists():
      zst_p.unlink()
  remaining = {
      normalize_daily_compressed_path(str(tar_with_raw)): ["/raw/seg1"],
  }
  seal_dirty_daily_archives(
      str(tmp_path),
      local_tz=ZoneInfo("UTC"),
      zstd_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      idle_seconds=0,
      seal_immediately_if_dirty=True,
      log_fn=None,
      remaining_raw_by_gz=remaining,
      only_when_no_remaining_raw=True,
  )
  assert sealed == [os.path.normpath(str(tar_clean))]


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_validate_for_raw_removal_skips_auto_seal_when_disabled(tmp_path):
  """allow_auto_seal=False must not create a missing sealed archive."""
  tar_path = tmp_path / "2024-02-01.tar"
  member = tmp_path / "a.txt"
  member.write_text("hello")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="a.txt")
  zst_path = tmp_path / "2024-02-01.tar.zst"

  ok, members = validate_sealed_daily_archive_for_raw_removal(
      str(zst_path), log_fn=None, allow_auto_seal=False)
  assert ok is False
  assert members is None
  assert not zst_path.exists()

  ok, members = validate_sealed_daily_archive_for_raw_removal(
      str(zst_path), log_fn=None, allow_auto_seal=True)
  assert ok is True
  assert members and "a.txt" in members
  assert zst_path.exists()


def test_daily_tar_paths_from_pending_archive_tasks_handles_heap_and_items():
  import types

  task = types.SimpleNamespace(archive_info=("/arch/2024-03-01.tar.zst", ["/raw/a"]))
  heap_entry = (0.0, 1, 123.0, {"task": task, "paths": ["/raw/a"]})
  plain_item = {"task": types.SimpleNamespace(
      archive_info=("/arch/2024-03-02.tar.gz", ["/raw/b"]))}
  result = daily_tar_paths_from_pending_archive_tasks([heap_entry, plain_item])
  assert result == frozenset({
      os.path.normpath("/arch/2024-03-01.tar"),
      os.path.normpath("/arch/2024-03-02.tar"),
  })
  assert daily_tar_paths_from_pending_archive_tasks([]) == frozenset()


def test_daily_tar_paths_for_stats_paths_uses_first_ts_then_filename_epoch():
  archive_dir = "/arch"
  epoch_day = datetime(2024, 4, 5, 1, 0, 0)
  other_day = datetime(2024, 4, 6, 1, 0, 0)
  epoch_path = "/raw/host/%d" % int(epoch_day.timestamp())
  named_path = "/raw/host/not-an-epoch"
  first_ts = {named_path: int(other_day.timestamp())}
  result = daily_tar_paths_for_stats_paths(
      [epoch_path, named_path], archive_dir, first_ts)
  assert os.path.normpath("/arch/2024-04-05.tar") in result
  assert os.path.normpath("/arch/2024-04-06.tar") in result


def test_merge_maintenance_skip_daily_tar_paths_unions_unmapped_days():
  archive_dir = "/arch"
  unmapped_day = datetime(2024, 5, 2, 2, 0, 0)
  unmapped_path = "/raw/host/%d" % int(unmapped_day.timestamp())
  mapping = {"/arch/2024-05-01.tar.zst": ["/raw/host/1"]}
  merged = merge_maintenance_skip_daily_tar_paths(
      ["/arch/2024-05-09.tar"],
      closed_paths=[unmapped_path],
      mapping=mapping,
      tgz_archive_dir=archive_dir,
  )
  assert os.path.normpath("/arch/2024-05-09.tar") in merged
  assert os.path.normpath("/arch/2024-05-02.tar") in merged


def test_remove_verified_uncompressed_daily_tars_skips_unmapped_day_via_skip_paths(
    tmp_path,
):
  """Scheduled maintenance must not drop ``.tar`` when unmapped closed raw exists."""
  day_tar = tmp_path / "2026-04-23.tar"
  day_gz = tmp_path / "2026-04-23.tar.gz"
  member = tmp_path / "member.txt"
  member.write_text("same-content")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(member), arcname="member.txt")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(member), arcname="member.txt")

  remove_verified_uncompressed_daily_tars(
      str(tmp_path),
      log_fn=None,
      remaining_raw_by_gz={},
      skip_daily_tar_paths=frozenset({os.path.normpath(str(day_tar))}),
  )

  assert day_tar.is_file()
  assert day_gz.is_file()


def test_archive_validation_worker_count_respects_max_workers(monkeypatch):
  import hpcperfstats.dbload.lib.conf_parser as cfg
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(cfg, "get_sync_archive_validation_max_workers", lambda: 2)
  assert helpers._get_archive_validation_worker_count(8) == 2


def test_collect_unmapped_closed_raw_daily_tars_ignores_unparsable_on_disk(tmp_path):
  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  raw_path = host_dir / "bad_raw"
  raw_path.write_text("no-timestamp-here\n")
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      collect_unmapped_closed_raw_daily_tars,
  )

  result = collect_unmapped_closed_raw_daily_tars(
      str(archive_dir), ".hpc", str(daily_dir),
  )
  assert not result


def test_quarantine_ingest_failed_raw_path_valid_head_corrupt_body(tmp_path):
  import json

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  day_epoch = 1704067200
  raw_path = host_dir / str(day_epoch)
  raw_path.write_text(
      "%d job1 cn001\n"
      "bad line with only two tokens\n" % day_epoch,
      encoding="utf-8",
  )

  moved = helpers.quarantine_ingest_failed_raw_path(
      str(raw_path),
      str(archive_dir),
      helpers.INGEST_PARSE_FAILED_QUARANTINE_REASON,
      error_detail="not enough values to unpack (expected 3, got 2)",
      log_fn=lambda *_a, **_k: None,
  )
  assert moved is True
  assert not raw_path.exists()
  quarantine_path = (
      archive_dir / helpers.SYNC_TIMEDB_UNPARSABLE_RAW_DIRNAME
      / "host.hpc" / str(day_epoch)
  )
  assert quarantine_path.is_file()
  manifest = json.loads(
      (archive_dir / helpers.SYNC_TIMEDB_UNPARSABLE_RAW_MANIFEST_BASENAME).read_text()
  )
  entries = manifest["entries"] if isinstance(manifest, dict) else manifest
  assert len(entries) == 1
  assert entries[0]["reason"] == helpers.INGEST_PARSE_FAILED_QUARANTINE_REASON
  assert "unpack" in entries[0]["error_detail"]


def test_quarantine_unparsable_closed_raw_moves_file_and_writes_manifest(tmp_path):
  import json

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  raw_path = host_dir / "bad_raw"
  raw_path.write_text("no-timestamp-here\n")

  moved = helpers.quarantine_unparsable_closed_raw_paths(
      [str(raw_path)],
      str(archive_dir),
      log_fn=lambda *_a, **_k: None,
  )
  assert moved == 1
  assert not raw_path.exists()
  quarantine_path = (
      archive_dir / helpers.SYNC_TIMEDB_UNPARSABLE_RAW_DIRNAME / "host.hpc" / "bad_raw"
  )
  assert quarantine_path.is_file()
  manifest = json.loads(
      (archive_dir / helpers.SYNC_TIMEDB_UNPARSABLE_RAW_MANIFEST_BASENAME).read_text()
  )
  entries = manifest["entries"] if isinstance(manifest, dict) else manifest
  assert len(entries) == 1
  assert entries[0]["original_path"] == str(raw_path)
  assert entries[0]["reason"] == helpers.UNPARSABLE_RAW_QUARANTINE_REASON
  discovered = helpers.collect_stats_files_in_range(
      str(archive_dir), "all", None, ".hpc")
  assert str(raw_path) not in discovered


def test_quarantine_manifest_written_before_move(monkeypatch, tmp_path):
  import json

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  raw_path = host_dir / "bad_raw"
  raw_path.write_text("no-timestamp-here\n")
  manifest_path = archive_dir / helpers.SYNC_TIMEDB_UNPARSABLE_RAW_MANIFEST_BASENAME
  order = []

  real_move = helpers.shutil.move

  def track_move(src, dst):
    if manifest_path.is_file():
      payload = json.loads(manifest_path.read_text())
      entries = payload.get("entries", payload)
      order.append("manifest_has_entry" if entries else "manifest_empty")
    order.append("move")
    return real_move(src, dst)

  monkeypatch.setattr(helpers.shutil, "move", track_move)

  moved = helpers.quarantine_unparsable_closed_raw_paths(
      [str(raw_path)],
      str(archive_dir),
      log_fn=lambda *_a, **_k: None,
  )
  assert moved == 1
  assert "move" in order
  assert "manifest_has_entry" in order
  assert order.index("manifest_has_entry") < order.index("move")


def test_remove_verified_skips_delete_when_fingerprint_changes(
    monkeypatch, tmp_path,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  archive_dir = tmp_path / "archive"
  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  raw_path = host_dir / "1000000000"
  raw_path.write_bytes(b"x" * 5)
  zst_path = tgz_dir / "2020-01-01.tar.zst"
  tar_path = tgz_dir / "2020-01-01.tar"
  member_name = helpers.get_tar_member_name(str(raw_path))

  import tarfile

  with tarfile.open(tar_path, "w") as tf:
    info = tarfile.TarInfo(name=member_name)
    info.size = 5
    tf.addfile(info, fileobj=__import__("io").BytesIO(b"x" * 5))

  monkeypatch.setattr(
      helpers,
      "collect_stats_files_in_range",
      lambda *_a, **_k: [str(raw_path)],
  )
  monkeypatch.setattr(
      helpers,
      "build_archive_mapping",
      lambda paths, _tgz: {str(zst_path): list(paths)},
  )
  monkeypatch.setattr(
      helpers,
      "validate_sealed_daily_archive_for_raw_removal",
      lambda *_a, **_k: (True, {member_name: 5}),
  )
  monkeypatch.setattr(
      helpers,
      "_iter_archive_validation_results_stream",
      lambda paths, **_kw: [(paths[0], True, {member_name: 5})],
  )

  original_fp = helpers.raw_stats_path_fingerprint
  calls = {"n": 0}

  def fp_then_change(path):
    calls["n"] += 1
    if calls["n"] == 1:
      return original_fp(path)
    raw_path.write_bytes(b"yy")
    return original_fp(path)

  monkeypatch.setattr(helpers, "raw_stats_path_fingerprint", fp_then_change)

  helpers.remove_verified_archived_raw_files(
      str(archive_dir),
      ".hpc",
      str(tgz_dir),
      log_fn=lambda *_a, **_k: None,
      require_fingerprint_at_delete=True,
  )
  assert raw_path.is_file()


def test_unparsable_unmapped_does_not_disqualify_day(tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  day_epoch = int(datetime(2026, 1, 1, 12, 0, 0).timestamp())
  raw_path = host_dir / str(day_epoch)
  raw_path.write_text("not-a-stats-line\n")
  closed_paths = helpers.collect_stats_files_in_range(
      str(archive_dir), "all", None, ".hpc")
  mapping = helpers.build_archive_mapping(closed_paths, str(daily_dir))
  result = helpers.collect_days_with_unmapped_closed_raw(
      closed_paths, mapping, str(daily_dir))
  assert not result


def test_effective_keep_uncompressed_tar_prior_day_false_today_grace(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from datetime import timezone

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  prior_tar = str(daily_dir / "2026-01-01.tar")
  today_tar = str(daily_dir / "2026-06-07.tar")
  tz = timezone.utc
  monkeypatch.setattr(helpers.cfg, "get_archive_keep_uncompressed_tar", lambda: False)
  monkeypatch.setattr(
      helpers.cfg, "get_archive_today_uncompressed_tar_grace_hours", lambda: 8.0)
  assert not helpers.effective_keep_uncompressed_tar(
      prior_tar, local_tz=tz, now=datetime(2026, 6, 7, 12, 0, tzinfo=tz))
  assert helpers.effective_keep_uncompressed_tar(
      today_tar,
      local_tz=tz,
      now=datetime(2026, 6, 7, 6, 0, tzinfo=tz),
  )
  assert not helpers.effective_keep_uncompressed_tar(
      today_tar,
      local_tz=tz,
      now=datetime(2026, 6, 7, 9, 0, tzinfo=tz),
  )


def test_daily_tar_seal_calendar_eligible_prior_day_and_today_grace(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from datetime import timezone

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  prior_tar = str(daily_dir / "2026-01-01.tar")
  today_tar = str(daily_dir / "2026-06-07.tar")
  tz = timezone.utc
  monkeypatch.setattr(
      helpers.cfg, "get_archive_today_uncompressed_tar_grace_hours", lambda: 8.0)
  assert daily_tar_seal_calendar_eligible(
      prior_tar, tz, now=datetime(2026, 6, 7, 1, 0, tzinfo=tz))
  assert not daily_tar_seal_calendar_eligible(
      today_tar, tz, now=datetime(2026, 6, 7, 7, 59, tzinfo=tz))
  assert daily_tar_seal_calendar_eligible(
      today_tar, tz, now=datetime(2026, 6, 7, 8, 0, tzinfo=tz))


def _local_day_epoch(day_str):
  """Unix second for noon on ``YYYY-MM-DD`` in local timezone (stable tar bucket)."""
  day = datetime.strptime(day_str, "%Y-%m-%d")
  return str(int(day.replace(hour=12).timestamp()))


def test_raw_stats_path_needs_tar_append_no_tar_yet(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  host_dir = tmp_path / "archive" / "node1.hpc"
  host_dir.mkdir(parents=True)
  ts = _local_day_epoch("2024-01-01")
  raw_path = host_dir / ts
  raw_path.write_text(f"{ts} job1 node1\n")
  assert raw_stats_path_needs_tar_append(
      str(raw_path),
      str(daily_dir),
      first_ts=ts,
  )


def test_raw_stats_path_needs_tar_append_skips_matching_member(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  host_dir = tmp_path / "archive" / "node1.hpc"
  host_dir.mkdir(parents=True)
  ts = _local_day_epoch("2024-01-01")
  raw_path = host_dir / ts
  payload = f"{ts} job1 node1\n".encode()
  raw_path.write_bytes(payload)
  tar_path = daily_dir / "2024-01-01.tar"
  member_name = get_tar_member_name(str(raw_path))
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(raw_path), arcname=member_name)
  assert not raw_stats_path_needs_tar_append(
      str(raw_path),
      str(daily_dir),
      first_ts=ts,
  )


def test_raw_stats_path_tar_append_decision_member_exists(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  host_dir = tmp_path / "archive" / "node1.hpc"
  host_dir.mkdir(parents=True)
  ts = _local_day_epoch("2024-01-02")
  raw_path = host_dir / ts
  payload = f"{ts} job1 node1\n".encode()
  raw_path.write_bytes(payload)
  tar_path = daily_dir / "2024-01-02.tar"
  member_name = get_tar_member_name(str(raw_path))
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(raw_path), arcname=member_name)
  needs, skip = raw_stats_path_tar_append_decision(
      str(raw_path),
      str(daily_dir),
      first_ts=ts,
  )
  assert needs is False
  assert skip == ARCHIVE_SKIP_MEMBER_EXISTS


def test_raw_stats_path_tar_append_decision_missing_path(tmp_path):
  needs, skip = raw_stats_path_tar_append_decision(
      str(tmp_path / "missing" / "segment"),
      str(tmp_path / "daily"),
  )
  assert needs is False
  assert skip == ARCHIVE_SKIP_MISSING_PATH


def test_raw_stats_path_tar_append_decision_active_segment(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  raw_path = tmp_path / "archive" / "host.hpc" / "1700000000"
  raw_path.parent.mkdir(parents=True)
  raw_path.write_text("1700000000 job1 host\n")
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.stats_file_is_active_segment",
      lambda _p: True,
  )
  needs, skip = raw_stats_path_tar_append_decision(
      str(raw_path),
      str(daily_dir),
  )
  assert needs is False
  assert skip == ARCHIVE_SKIP_ACTIVE_SEGMENT


def test_raw_stats_path_tar_append_decision_day_ingest_skip(
    tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveDayIngestSkipError,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  host_dir = tmp_path / "archive" / "node1.hpc"
  host_dir.mkdir(parents=True)
  ts = _local_day_epoch("2024-01-03")
  raw_path = host_dir / ts
  raw_path.write_text(f"{ts} job1 node1\n")

  def raise_skip(*_a, **_k):
    raise ArchiveDayIngestSkipError(
        "2024-01-03",
        str(daily_dir / "2024-01-03.tar.zst"),
        "zst_frame_invalid",
        "bad frame",
    )

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.daily_archive_has_member_with_size",
      raise_skip,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers._log_archive_day_ingest_skip_once",
      lambda _exc: None,
  )
  needs, skip = raw_stats_path_tar_append_decision(
      str(raw_path),
      str(daily_dir),
      first_ts=ts,
  )
  assert needs is False
  assert skip == "day_ingest_skip:zst_frame_invalid"


def test_raw_stats_path_needs_tar_append_when_member_size_differs(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  host_dir = tmp_path / "archive" / "node1.hpc"
  host_dir.mkdir(parents=True)
  ts = _local_day_epoch("2024-01-01")
  raw_path = host_dir / ts
  raw_path.write_text(f"{ts} job1 node1\nextra")
  tar_path = daily_dir / "2024-01-01.tar"
  member_name = get_tar_member_name(str(raw_path))
  with tarfile.open(tar_path, "w") as tf:
    info = tarfile.TarInfo(name=member_name)
    info.size = 4
    tf.addfile(info, io.BytesIO(b"tiny"))
  assert raw_stats_path_needs_tar_append(
      str(raw_path),
      str(daily_dir),
      first_ts=ts,
  )


@pytest.fixture(autouse=False)
def _clear_daily_archive_members_cache():
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  helpers.clear_daily_archive_members_cache()
  yield
  helpers.clear_daily_archive_members_cache()


def test_sealed_archive_member_has_exact_size_early_exit(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  day_gz = tmp_path / "2024-06-11.tar.gz"
  inner_target = tmp_path / "target.txt"
  inner_target.write_text("x")
  inner_noise = tmp_path / "noise.txt"
  inner_noise.write_text("padding")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner_target), arcname="host/target")
    for i in range(50):
      tf.add(str(inner_noise), arcname="noise/%d" % i)

  iter_count = {"n": 0}
  real_iter = helpers._iter_tar_members

  def counting_iter(tf):
    for member in real_iter(tf):
      iter_count["n"] += 1
      yield member

  monkeypatch.setattr(helpers, "_iter_tar_members", counting_iter)
  sealed = str(day_gz)
  assert helpers._sealed_archive_member_has_exact_size(
      sealed, "host/target", 1,
  ) is True
  assert iter_count["n"] == 1
  iter_count["n"] = 0
  assert helpers._sealed_archive_member_has_exact_size(
      sealed, "missing/member", 1,
  ) is False


def test_daily_archive_has_member_falls_back_when_populate_raises(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_gz = tmp_path / "2024-06-12.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: FakeRedis(),
  )
  assert helpers.daily_archive_has_member_with_size(
      sealed, "host/raw", 4,
  ) is True
  assert helpers.daily_archive_has_member_with_size(
      sealed, "host/other", 4,
  ) is False


def test_ingest_sealed_path_uses_populate_not_parallel_point(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)
  populate_calls = {"n": 0}
  point_calls = {"n": 0}

  def _track_populate(_sealed_path, _cache_key):
    populate_calls["n"] += 1
    return {"host/raw": 4}

  def _forbidden_point_lookup(*_a, **_k):
    point_calls["n"] += 1
    raise AssertionError("ingest must not run local point lookup before Redis populate")

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: FakeRedis(),
  )
  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_sealed_scan", _track_populate,
  )
  monkeypatch.setattr(
      helpers, "_sealed_archive_member_has_exact_size", _forbidden_point_lookup,
  )
  assert helpers.daily_archive_has_member_with_size(
      sealed, "host/raw", 4,
  ) is True
  assert populate_calls["n"] == 1
  assert point_calls["n"] == 0


def test_ingest_sealed_single_flight_one_zstd_scan(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import threading

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)
  stream_calls = {"n": 0}
  stream_lock = threading.Lock()
  original_stream = helpers._stream_compressed_archive_members
  fake = FakeRedis()

  def _counting_stream(compressed_path, on_member=None, **kwargs):
    with stream_lock:
      stream_calls["n"] += 1
    return original_stream(compressed_path, on_member, **kwargs)

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 30,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      helpers, "_stream_compressed_archive_members", _counting_stream,
  )

  results = []
  errors = []

  def _lookup(member):
    try:
      results.append(
          helpers.daily_archive_has_member_with_size(
              sealed, member, 4 if member == "host/raw" else 99,
          ),
      )
    except Exception as exc:
      errors.append(exc)

  threads = [
      threading.Thread(target=_lookup, args=("host/raw",))
      for _ in range(8)
  ] + [
      threading.Thread(target=_lookup, args=("host/missing",))
      for _ in range(8)
  ]
  for t in threads:
    t.start()
  for t in threads:
    t.join(timeout=10)
  assert not errors
  assert stream_calls["n"] == 1
  assert results.count(True) == 8
  assert results.count(False) == 8


def _start_fake_populate_pool_worker(monkeypatch, fake, *, stop_event):
  """BRPOP loop for unit tests when PopulatePoolController reports running."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
      set_populate_pool_controller,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_members_populate_queue_brpop,
  )

  class _FakePopulateController:
    def is_running(self):
      return True

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 30,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  set_populate_pool_controller(_FakePopulateController())

  def _worker():
    token = set_worker_pool_kind("populate-pool")
    try:
      while not stop_event.is_set():
        job = archive_members_populate_queue_brpop(timeout_s=0.2)
        if job is None:
          continue
        canonical = str(job.get("canonical") or "")
        if canonical:
          helpers.execute_archive_members_populate_for_canonical(canonical)
    finally:
      reset_worker_pool_kind(token)

  thread = threading.Thread(target=_worker, daemon=True)
  thread.start()
  return thread


def test_ingest_worker_never_streams_sealed_on_cold_redis(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import threading

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
      reset_populate_pool_controller_for_tests,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)
  fake = FakeRedis()
  stop = threading.Event()
  _start_fake_populate_pool_worker(monkeypatch, fake, stop_event=stop)

  ingest_stream_calls = {"n": 0}
  populate_stream_calls = {"n": 0}
  stream_lock = threading.Lock()
  original_stream = helpers._stream_compressed_archive_members

  def _counting_stream(compressed_path, on_member=None, **kwargs):
    from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
        get_worker_pool_kind,
    )

    with stream_lock:
      kind = get_worker_pool_kind()
      if kind == "ingest-pool":
        ingest_stream_calls["n"] += 1
      elif kind == "populate-pool":
        populate_stream_calls["n"] += 1
    return original_stream(compressed_path, on_member, **kwargs)

  monkeypatch.setattr(
      helpers, "_stream_compressed_archive_members", _counting_stream,
  )

  errors = []

  def _lookup():
    token = set_worker_pool_kind("ingest-pool")
    try:
      helpers.daily_archive_has_member_with_size(sealed, "host/raw", 4)
    except Exception as exc:
      errors.append(exc)
    finally:
      reset_worker_pool_kind(token)

  threads = [threading.Thread(target=_lookup) for _ in range(8)]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=15)
  stop.set()
  reset_populate_pool_controller_for_tests()
  assert not errors
  assert ingest_stream_calls["n"] == 0
  assert populate_stream_calls["n"] == 1


def test_sealed_stream_timeout_no_longer_ingest_per_file(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """Expired ingest deadline during populate wait must not abort sealed scan."""
  import threading
  import time

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      IngestArchiveLookupBudgetExceededError,
      reset_ingest_task_deadline_monotonic,
      set_ingest_task_deadline_monotonic,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
      reset_populate_pool_controller_for_tests,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)
  fake = FakeRedis()
  stop = threading.Event()
  _start_fake_populate_pool_worker(monkeypatch, fake, stop_event=stop)

  stream_release = threading.Event()
  original_stream = helpers._stream_compressed_archive_members

  def _slow_stream(compressed_path, on_member=None, **kwargs):
    stream_release.wait(timeout=5.0)
    return original_stream(compressed_path, on_member, **kwargs)

  monkeypatch.setattr(helpers, "_stream_compressed_archive_members", _slow_stream)

  deadline_token = set_ingest_task_deadline_monotonic(time.monotonic() - 1.0)
  errors = []
  results = []

  def _lookup():
    pool_token = set_worker_pool_kind("ingest-pool")
    try:
      results.append(
          helpers.daily_archive_has_member_with_size(sealed, "host/raw", 4),
      )
    except Exception as exc:
      errors.append(exc)
    finally:
      reset_worker_pool_kind(pool_token)

  thread = threading.Thread(target=_lookup)
  thread.start()
  time.sleep(0.1)
  stream_release.set()
  thread.join(timeout=10)
  reset_ingest_task_deadline_monotonic(deadline_token)
  stop.set()
  reset_populate_pool_controller_for_tests()
  assert not errors
  assert not any(
      isinstance(exc, IngestArchiveLookupBudgetExceededError) for exc in errors
  )
  assert results == [True]


def test_validate_sealed_uses_redis_single_flight(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import threading

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)
  stream_calls = {"n": 0}
  stream_lock = threading.Lock()
  original_stream = helpers._stream_compressed_archive_members
  fake = FakeRedis()

  def _counting_stream(compressed_path, on_member=None, **kwargs):
    with stream_lock:
      stream_calls["n"] += 1
    return original_stream(compressed_path, on_member, **kwargs)

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 30,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      helpers, "_stream_compressed_archive_members", _counting_stream,
  )

  errors = []
  validate_results = []

  def _validate():
    try:
      ok, members = helpers.validate_sealed_daily_archive_for_raw_removal(
          sealed,
          log_fn=None,
      )
      validate_results.append((ok, members))
    except Exception as exc:
      errors.append(exc)

  def _lookup():
    try:
      helpers.get_existing_archive_members_for_daily_archive(sealed)
    except Exception as exc:
      errors.append(exc)

  threads = [
      threading.Thread(target=_validate),
      threading.Thread(target=_lookup),
  ]
  for t in threads:
    t.start()
  for t in threads:
    t.join(timeout=10)
  assert not errors
  assert stream_calls["n"] == 1
  assert validate_results
  assert validate_results[0][0] is True
  assert validate_results[0][1].get("host/raw") == 4


def test_ingest_waiters_no_local_zstd_while_lock_held(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import threading
  import time

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      build_archive_members_redis_keys,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)
  point_calls = {"n": 0}
  fake = FakeRedis()

  def _forbidden_point_lookup(*_a, **_k):
    point_calls["n"] += 1
    raise AssertionError("waiters must not run local point lookup while lock held")

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      helpers, "_sealed_archive_member_has_exact_size", _forbidden_point_lookup,
  )
  def _forbidden_populate(*_a, **_k):
    raise AssertionError("must wait, not populate while lock held")

  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_sealed_scan", _forbidden_populate,
  )

  canonical = helpers.normalize_daily_compressed_path(sealed)
  cache_key = helpers._daily_archive_members_cache_key(canonical)
  keys = build_archive_members_redis_keys(cache_key)
  fake.set(keys.lock_key, "tok", ex=30)
  fake.set(keys.complete_key, "0")
  fake.set(keys.progress_key, str(time.time()))

  done = {"ok": None, "exc": None}

  def _waiter():
    try:
      done["ok"] = helpers.daily_archive_has_member_with_size(
          sealed, "host/raw", 4,
      )
    except Exception as exc:
      done["exc"] = exc

  t_wait = threading.Thread(target=_waiter)
  t_wait.start()
  time.sleep(0.05)
  assert point_calls["n"] == 0
  fake.hset(keys.hash_key, "host/raw", "4")
  fake.delete(keys.lock_key)
  fake.set(keys.complete_key, "1")
  t_wait.join(timeout=2)
  assert done["exc"] is None
  assert done["ok"] is True
  assert point_calls["n"] == 0


def _compress_bytes_to_zst(payload: bytes, zst_path: Path):
  import subprocess

  from hpcperfstats.dbload.lib.zstd_cli import zstd_executable

  raw_path = zst_path.with_suffix(".raw")
  raw_path.write_bytes(payload)
  subprocess.run(
      [zstd_executable(), "-q", "-T0", "-f", str(raw_path), "-o", str(zst_path)],
      check=True,
  )
  raw_path.unlink(missing_ok=True)


def _make_valid_zst_truncated_tar(tmp_path) -> str:
  tar_path = tmp_path / "inner.tar"
  inner = tmp_path / "payload.txt"
  inner.write_text("hello")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(inner), arcname="host/payload")
  tar_bytes = tar_path.read_bytes()
  truncated = tar_bytes[: max(512, len(tar_bytes) // 2)]
  zst_path = tmp_path / "2026-05-09.tar.zst"
  _compress_bytes_to_zst(truncated, zst_path)
  return str(zst_path)


@pytest.mark.skipif(
    __import__("shutil").which("zstd") is None,
    reason="zstd binary required",
)
def test_classify_stream_failure_zst_valid_tar_eof(tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  sealed = _make_valid_zst_truncated_tar(tmp_path)
  kind, detail = helpers.classify_sealed_archive_stream_failure(
      sealed, EOFError("unexpected end of data"),
  )
  assert kind == helpers.SKIP_KIND_TAR_TRUNCATED
  assert "unexpected end" in detail.lower() or detail


def test_classify_stream_failure_zst_invalid(tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  sealed = tmp_path / "2026-05-09.tar.zst"
  sealed.write_bytes(b"not-valid-zstd")
  kind, _detail = helpers.classify_sealed_archive_stream_failure(
      str(sealed), EOFError("unexpected end of data"),
  )
  assert kind == helpers.SKIP_KIND_ZST_FRAME_INVALID


def test_concurrent_waiters_no_zstd_after_day_skip(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import threading

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveDayIngestSkipError,
      build_archive_members_redis_keys,
      set_archive_day_ingest_skip,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)
  stream_calls = {"n": 0}
  fake = FakeRedis()

  def _forbidden_stream(*_a, **_k):
    stream_calls["n"] += 1
    raise AssertionError("must not stream sealed archive after day skip")

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      helpers, "_stream_compressed_archive_members", _forbidden_stream,
  )

  canonical = helpers.normalize_daily_compressed_path(sealed)
  cache_key = helpers._daily_archive_members_cache_key(canonical)
  keys = build_archive_members_redis_keys(cache_key)
  set_archive_day_ingest_skip(
      fake, keys, "tar_truncated_or_unreadable", "unexpected end of data",
  )
  fake.set(keys.degraded_key, "1")

  errors = []
  results = []

  def _lookup():
    try:
      results.append(
          helpers.daily_archive_has_member_with_size(
              sealed, "host/raw", 4,
          ),
      )
    except ArchiveDayIngestSkipError as exc:
      errors.append(exc)

  threads = [threading.Thread(target=_lookup) for _ in range(8)]
  for t in threads:
    t.start()
  for t in threads:
    t.join(timeout=5)
  assert stream_calls["n"] == 0
  assert len(errors) == 8
  assert not results


def test_raw_stats_needs_append_false_when_day_skipped(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveDayIngestSkipError,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  host_dir = tmp_path / "archive" / "node1.hpc"
  host_dir.mkdir(parents=True)
  ts = _local_day_epoch("2026-05-09")
  raw_path = host_dir / ts
  raw_path.write_bytes(("%s job1 node1\n" % ts).encode())

  def _raise_skip(*_a, **_k):
    raise ArchiveDayIngestSkipError(
        "2026-05-09",
        str(daily_dir / "2026-05-09.tar.zst"),
        helpers.SKIP_KIND_TAR_TRUNCATED,
        "unexpected end of data",
    )

  monkeypatch.setattr(
      helpers, "daily_archive_has_member_with_size", _raise_skip,
  )
  assert helpers.raw_stats_path_needs_tar_append(
      str(raw_path),
      str(daily_dir),
      first_ts=ts,
  ) is False


def test_get_existing_archive_members_no_local_scan_when_degraded(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """Degraded Redis populate without day skip must not fall through to local zstd."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
      build_archive_members_redis_keys,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

  day_gz = tmp_path / "2024-06-14.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  sealed = str(day_gz)
  stream_calls = {"n": 0}
  fake = FakeRedis()

  def _forbidden_stream(*_a, **_k):
    stream_calls["n"] += 1
    raise AssertionError("must not stream sealed archive when Redis degraded")

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      helpers, "_stream_compressed_archive_members", _forbidden_stream,
  )
  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", _forbidden_stream,
  )

  canonical = helpers.normalize_daily_compressed_path(sealed)
  cache_key = helpers._daily_archive_members_cache_key(canonical)
  keys = build_archive_members_redis_keys(cache_key)
  fake.set(keys.degraded_key, "1")

  request_calls = {"n": 0}

  def _fake_request(path, *, role="ingest"):
    del role
    request_calls["n"] += 1
    raise ArchiveMembersRedisUnavailableError(
        "archive members Redis enabled but lookup did not return members for %s"
        % path,
    )

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".request_archive_members_populate_and_wait",
      _fake_request,
  )

  with pytest.raises(ArchiveMembersRedisUnavailableError):
    get_existing_archive_members_for_daily_archive(sealed)
  assert request_calls["n"] == 1
  assert stream_calls["n"] == 0


def test_daily_archive_has_member_no_wait_no_tar(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """No tar/sealed on disk must not enter Redis populate wait."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      build_archive_members_redis_keys,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

  day = "2026-06-09"
  canonical = str(tmp_path / ("%s.tar.zst" % day))
  fake = FakeRedis()
  request_calls = {"n": 0}

  def _forbidden_request(*_a, **_k):
    request_calls["n"] += 1
    raise AssertionError("must not wait on populate when no daily archive")

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".request_archive_members_populate_and_wait",
      _forbidden_request,
  )
  keys = build_archive_members_redis_keys(
      helpers._daily_archive_members_cache_key(canonical),
  )
  fake.set(keys.complete_key, "0")

  assert helpers.daily_archive_has_member_with_size(
      canonical, "host/raw", 4,
  ) is False
  assert request_calls["n"] == 0


def test_raw_stats_path_needs_tar_append_reraises_redis_unavailable(
    monkeypatch, tmp_path,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  host_dir = tmp_path / "archive" / "node1.hpc"
  host_dir.mkdir(parents=True)
  ts = _local_day_epoch("2024-06-13")
  raw_path = host_dir / ts
  raw_path.write_bytes(("%s job1 node1\n" % ts).encode())

  def _raise_lookup(*_a, **_k):
    raise ArchiveMembersRedisUnavailableError("redis down")

  monkeypatch.setattr(
      helpers, "daily_archive_has_member_with_size", _raise_lookup,
  )
  with pytest.raises(ArchiveMembersRedisUnavailableError, match="redis down"):
    raw_stats_path_needs_tar_append(
        str(raw_path),
        str(daily_dir),
        first_ts=ts,
    )


def test_ingest_skipped_calendar_days_lru_not_full_clear(monkeypatch):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  helpers._INGEST_SKIPPED_CALENDAR_DAYS.clear()
  monkeypatch.setattr(
      helpers, "_INGEST_SKIPPED_CALENDAR_DAYS_MAX", 3,
  )
  helpers._cache_ingest_skipped_calendar_day("day-a", "k", "d", "/a")
  helpers._cache_ingest_skipped_calendar_day("day-b", "k", "d", "/b")
  helpers._cache_ingest_skipped_calendar_day("day-c", "k", "d", "/c")
  helpers._cache_ingest_skipped_calendar_day("day-d", "k", "d", "/d")
  assert "day-a" not in helpers._INGEST_SKIPPED_CALENDAR_DAYS
  assert "day-b" in helpers._INGEST_SKIPPED_CALENDAR_DAYS
  helpers._cache_ingest_skipped_calendar_day("day-b", "k2", "d2", "/b2")
  helpers._cache_ingest_skipped_calendar_day("day-e", "k", "d", "/e")
  assert "day-c" not in helpers._INGEST_SKIPPED_CALENDAR_DAYS
  assert "day-b" in helpers._INGEST_SKIPPED_CALENDAR_DAYS
  assert "day-d" in helpers._INGEST_SKIPPED_CALENDAR_DAYS
  helpers._INGEST_SKIPPED_CALENDAR_DAYS.clear()


def test_daily_archive_members_cache_hit_skips_second_scan(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  day_gz = tmp_path / "2024-06-01.tar.gz"
  inner = tmp_path / "z.txt"
  inner.write_text("abc")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="onlymember")
  scan_calls = {"n": 0}
  original = helpers._scan_compressed_archive_members_and_readable

  def counting_scan(path, **kwargs):
    scan_calls["n"] += 1
    return original(path, **kwargs)

  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", counting_scan,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  path = str(day_gz)
  first = get_existing_archive_members_for_daily_archive(path)
  second = get_existing_archive_members_for_daily_archive(path)
  assert first == second == {"onlymember": 3}
  assert scan_calls["n"] == 1


def test_daily_archive_members_cache_invalidates_on_tar_mtime(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  day_tar = tmp_path / "2024-06-02.tar"
  day_gz = tmp_path / "2024-06-02.tar.gz"
  inner = tmp_path / "a.txt"
  inner.write_text("one")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="member")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="stale")
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  path = str(day_gz)
  get_existing_archive_members_for_daily_archive(path)
  inner.write_text("two")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="member")
  updated = get_existing_archive_members_for_daily_archive(path)
  assert updated["member"] == 3


def test_raw_stats_needs_append_uses_cache(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  host_dir = tmp_path / "archive" / "node1.hpc"
  host_dir.mkdir(parents=True)
  ts = _local_day_epoch("2024-06-03")
  raw_path = host_dir / ts
  payload = f"{ts} job1 node1\n".encode()
  raw_path.write_bytes(payload)
  tar_path = daily_dir / "2024-06-03.tar"
  member_name = get_tar_member_name(str(raw_path))
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(raw_path), arcname=member_name)
  scan_calls = {"n": 0}
  original = helpers.get_existing_archive_members

  def counting_get_members(tar_p):
    scan_calls["n"] += 1
    return original(tar_p)

  monkeypatch.setattr(
      helpers, "get_existing_archive_members", counting_get_members,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  assert not raw_stats_path_needs_tar_append(
      str(raw_path), str(daily_dir), first_ts=ts,
  )
  assert not raw_stats_path_needs_tar_append(
      str(raw_path), str(daily_dir), first_ts=ts,
  )
  assert scan_calls["n"] == 1


def test_single_member_early_exit_finds_match(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  day_gz = tmp_path / "2024-06-04.tar.gz"
  inner = tmp_path / "target.txt"
  inner.write_text("match")
  other = tmp_path / "other.txt"
  other.write_text("noise")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(other), arcname="noise/member")
    tf.add(str(inner), arcname="host/target")
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  assert helpers.daily_archive_has_member_with_size(
      str(day_gz), "host/target", 5,
  )
  scan_calls = {"n": 0}
  original = helpers._scan_compressed_archive_members_and_readable

  def counting_scan(path, **kwargs):
    scan_calls["n"] += 1
    return original(path, **kwargs)

  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", counting_scan,
  )
  assert helpers.daily_archive_has_member_with_size(
      str(day_gz), "host/target", 5,
  )
  assert scan_calls["n"] == 0


def test_daily_archive_has_member_prefers_redis_when_tar_and_sealed_exist(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      build_archive_members_redis_keys,
      store_complete_members_in_redis,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  member_name = "host.vista.tacc.utexas.edu/raw"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  tar_path = daily_dir / "2026-05-20.tar"
  with tarfile.open(tar_path, "w:") as tf:
    tf.add(str(inner), arcname=member_name)
  sealed = daily_dir / "2026-05-20.tar.gz"
  with tarfile.open(sealed, "w:gz") as tf:
    tf.add(str(inner), arcname=member_name)
  sealed_str = str(sealed)
  cache_key = helpers._daily_archive_members_cache_key(
      helpers.normalize_daily_compressed_path(sealed_str),
  )
  keys = build_archive_members_redis_keys(cache_key)
  fake_redis = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake_redis,
  )
  store_complete_members_in_redis(
      keys,
      {member_name: 4},
      saw_duplicates=False,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  tar_scan_calls = {"n": 0}
  original_get_members = helpers.get_existing_archive_members

  def counting_tar_scan(path):
    tar_scan_calls["n"] += 1
    return original_get_members(path)

  monkeypatch.setattr(
      helpers, "get_existing_archive_members", counting_tar_scan,
  )
  assert helpers.daily_archive_has_member_with_size(
      sealed_str, member_name, 4,
  ) is True
  assert tar_scan_calls["n"] == 0


def test_warm_duplicate_check_uses_hget_not_hgetall(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      build_archive_members_redis_keys,
      store_complete_members_in_redis,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  member_name = "host.vista.tacc.utexas.edu/raw"
  sealed = daily_dir / "2026-05-20.tar.gz"
  sealed.write_bytes(b"gz")
  sealed_str = str(sealed)
  cache_key = helpers._daily_archive_members_cache_key(
      helpers.normalize_daily_compressed_path(sealed_str),
  )
  keys = build_archive_members_redis_keys(cache_key)
  fake_redis = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake_redis,
  )
  store_complete_members_in_redis(
      keys,
      {member_name: 4, "other/member": 99},
      saw_duplicates=False,
  )
  hgetall_calls = {"n": 0}
  hget_calls = {"n": 0}
  original_hgetall = fake_redis.hgetall
  original_hget = fake_redis.hget

  def counting_hgetall(key):
    hgetall_calls["n"] += 1
    return original_hgetall(key)

  def counting_hget(key, field):
    hget_calls["n"] += 1
    return original_hget(key, field)

  fake_redis.hgetall = counting_hgetall
  fake_redis.hget = counting_hget
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  assert helpers.daily_archive_has_member_with_size(
      sealed_str, member_name, 4,
  ) is True
  assert hget_calls["n"] >= 1
  assert hgetall_calls["n"] == 0


def test_concurrent_duplicate_check_avoids_parallel_tar_scans_with_redis_warm(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import threading

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      build_archive_members_redis_keys,
      store_complete_members_in_redis,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  member_name = "host.vista.tacc.utexas.edu/raw"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  tar_path = daily_dir / "2026-05-20.tar"
  with tarfile.open(tar_path, "w:") as tf:
    tf.add(str(inner), arcname=member_name)
  sealed = daily_dir / "2026-05-20.tar.gz"
  with tarfile.open(sealed, "w:gz") as tf:
    tf.add(str(inner), arcname=member_name)
  sealed_str = str(sealed)
  cache_key = helpers._daily_archive_members_cache_key(
      helpers.normalize_daily_compressed_path(sealed_str),
  )
  keys = build_archive_members_redis_keys(cache_key)
  fake_redis = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake_redis,
  )
  store_complete_members_in_redis(
      keys,
      {member_name: 4},
      saw_duplicates=False,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  tar_scan_calls = {"n": 0}
  lock = threading.Lock()
  original_get_members = helpers.get_existing_archive_members

  def counting_tar_scan(path):
    with lock:
      tar_scan_calls["n"] += 1
    return original_get_members(path)

  monkeypatch.setattr(
      helpers, "get_existing_archive_members", counting_tar_scan,
  )
  errors = []

  def worker():
    try:
      helpers.daily_archive_has_member_with_size(
          sealed_str, member_name, 4,
      )
    except Exception as exc:
      errors.append(exc)

  threads = [threading.Thread(target=worker) for _ in range(8)]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join()
  assert not errors
  assert tar_scan_calls["n"] == 0


def test_invalidate_daily_archive_members_cache_forces_rescan(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  day_gz = tmp_path / "2024-06-05.tar.gz"
  inner = tmp_path / "z.txt"
  inner.write_text("abc")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="onlymember")
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  path = str(day_gz)
  get_existing_archive_members_for_daily_archive(path)
  helpers.invalidate_daily_archive_members_cache(path)
  scan_calls = {"n": 0}
  original = helpers._scan_compressed_archive_members_and_readable

  def counting_scan(p, **kwargs):
    scan_calls["n"] += 1
    return original(p, **kwargs)

  monkeypatch.setattr(
      helpers, "_scan_compressed_archive_members_and_readable", counting_scan,
  )
  get_existing_archive_members_for_daily_archive(path)
  assert scan_calls["n"] == 1


def test_prior_day_tar_removed_at_seal_when_keep_false(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2026-01-01.tar"
  zst_path = tmp_path / "2026-01-01.tar.zst"
  tar_path.write_bytes(b"tar-payload")
  monkeypatch.setattr(helpers.shutil, "which", lambda _cmd: "/usr/bin/zstd")

  def fake_compress(_tar, out_path, *_a, **_k):
    open(out_path, "wb").write(b"z")

  monkeypatch.setattr(helpers, "zstd_compress_tar_to_file", fake_compress)
  monkeypatch.setattr(helpers, "zstd_test", lambda *a, **k: None)
  monkeypatch.setattr(helpers, "_seal_skip_existing_zst_equivalent", lambda *a, **k: (False, None))
  monkeypatch.setattr(helpers, "get_existing_archive_members", lambda _p: {})
  helpers.atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      1,
      3,
      False,
      log_fn=lambda *_a, **_k: None,
      remaining_raw_by_gz={},
  )
  assert not tar_path.exists()
  assert zst_path.is_file()


def test_iter_tar_file_tasks_falls_back_to_gz_when_zst_corrupt(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-04.tar"
  zst_path = tmp_path / "2020-01-04.tar.zst"
  gz_path = tmp_path / "2020-01-04.tar.gz"
  tar_path.write_text("bad")
  zst_path.write_text("bad-zst")
  gz_path.write_text("good-gz-placeholder")
  calls = []

  def _fake_decomp(compressed_path, out_tar_path, thread_count, *, remove_compressed=True):
    del thread_count, remove_compressed
    calls.append(str(compressed_path))
    if str(compressed_path).endswith(".tar.zst"):
      return False
    inner = tmp_path / "inn.txt"
    inner.write_text("ok")
    with tarfile.open(str(out_tar_path), "w") as tf:
      tf.add(str(inner), arcname="only.txt")
    return True

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _fake_decomp)
  members = list(helpers.iter_tar_file_tasks(str(tar_path)))
  assert members
  assert str(zst_path) in calls
  assert str(gz_path) in calls


def test_iter_tar_file_tasks_restore_uses_sealed_keep_policy(monkeypatch, tmp_path):
  """Corrupt-tar recovery must route through replace_corrupt (sealed-keep gate)."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-06.tar"
  zst_path = tmp_path / "2020-01-06.tar.zst"
  tar_path.write_text("bad")
  zst_path.write_bytes(b"sealed")
  inner = tmp_path / "only.txt"
  inner.write_text("ok")
  with tarfile.open(str(tar_path), "w") as tf:
    tf.add(str(inner), arcname="only.txt")

  open_calls = {"n": 0}

  class _FakeTar:
    def __enter__(self):
      return self

    def __exit__(self, *_a):
      return False

    def __iter__(self):
      yield type("M", (), {"isfile": lambda self: True, "name": "only.txt"})()

  def _open_mock(path, mode="r", *_a, **_k):
    del mode
    open_calls["n"] += 1
    if open_calls["n"] == 1:
      raise tarfile.ReadError("corrupt")
    return _FakeTar()

  restore_kwargs = {}

  def _spy_replace(tar_out, zst, gz, threads):
    restore_kwargs.update(
        {"tar": tar_out, "zst": zst, "gz": gz, "threads": threads},
    )
    return True

  monkeypatch.setattr(helpers.tarfile, "open", _open_mock)
  monkeypatch.setattr(
      helpers,
      "replace_corrupt_tar_from_compressed_backup",
      _spy_replace,
  )
  members = list(helpers.iter_tar_file_tasks(str(tar_path)))
  assert members == [(str(tar_path), "only.txt")]
  assert restore_kwargs["zst"] == str(zst_path)
  assert open_calls["n"] == 2


def test_validate_sealed_restores_corrupt_tar_before_fail_closed(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-05.tar"
  zst_path = tmp_path / "2020-01-05.tar.zst"
  tar_path.write_text("bad")
  zst_path.write_bytes(b"sealed-placeholder")
  members = {"member.txt": 7}

  def _fake_decomp(compressed_path, out_tar_path, thread_count, *, remove_compressed=True):
    del compressed_path, thread_count, remove_compressed
    inner = tmp_path / "member.txt"
    inner.write_text("payload")
    with tarfile.open(str(out_tar_path), "w") as tf:
      tf.add(str(inner), arcname="member.txt")
    return True

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _fake_decomp)
  monkeypatch.setattr(
      helpers,
      "_scan_compressed_archive_members_and_readable",
      lambda _path, **kwargs: (True, dict(members)),
  )
  ok, members_out = helpers.validate_sealed_daily_archive_for_raw_removal(
      str(zst_path),
      log_fn=None,
      allow_auto_seal=False,
  )
  assert ok is True
  assert members_out == members


def test_replace_corrupt_tar_does_not_clobber_concurrent_append(monkeypatch, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-06.tar"
  zst_path = tmp_path / "2020-01-06.tar.zst"
  gz_path = tmp_path / "2020-01-06.tar.gz"
  tar_path.write_text("corrupt")
  zst_path.write_text("zst-placeholder")
  appended = tmp_path / "appended.txt"
  appended.write_text("new-append")

  def _fake_decomp(compressed_path, out_tar_path, thread_count, *, remove_compressed=True):
    del compressed_path, thread_count, remove_compressed
    with tarfile.open(str(out_tar_path), "w") as tf:
      tf.add(str(appended), arcname="appended.txt")
    return True

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _fake_decomp)
  assert helpers.replace_corrupt_tar_from_compressed_backup(
      str(tar_path), str(zst_path), str(gz_path), 1)
  with tarfile.open(str(tar_path), "r") as tf:
    names = [m.name for m in tf.getmembers() if m.isfile()]
  assert "appended.txt" in names


def test_unmapped_disqualify_uses_coordinator_after_accrual_trim(monkeypatch):
  """Trimmed accrual ``closed_paths=[]`` must not hide unmapped; coordinator wins."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      resolve_unmapped_closed_raw_daily_tars,
  )

  archive_dir = "/arch"
  unmapped_day = datetime(2024, 5, 2, 2, 0, 0)
  unmapped_path = "/raw/host/%d" % int(unmapped_day.timestamp())
  coord_snap = ArchiveMaintenanceSnapshot(
      closed_paths=[unmapped_path],
      mapping={},
  )
  accrual_snap = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      mapping={},
  )
  collect_calls = {"n": 0}

  def boom_collect(*_a, **_k):
    collect_calls["n"] += 1
    return frozenset()

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers."
      "get_unmapped_closed_raw_daily_tars_cached",
      boom_collect,
  )
  result = resolve_unmapped_closed_raw_daily_tars(
      coordinator_snapshot=coord_snap,
      accrual_snapshot=accrual_snap,
      archive_data_dir="/raw",
      host_name_ext="cluster.test",
      tgz_archive_dir=archive_dir,
  )
  assert os.path.normpath("/arch/2024-05-02.tar") in result
  assert collect_calls["n"] == 0


def test_collect_days_with_unmapped_closed_raw_buckets_unmapped_only():
  archive_dir = "/arch"
  mapped_day = datetime(2024, 5, 1, 2, 0, 0)
  unmapped_day = datetime(2024, 5, 2, 2, 0, 0)
  mapped_path = "/raw/host/%d" % int(mapped_day.timestamp())
  unmapped_path = "/raw/host/%d" % int(unmapped_day.timestamp())
  closed_paths = [mapped_path, unmapped_path]
  mapping = {"/arch/2024-05-01.tar.zst": [mapped_path]}
  result = collect_days_with_unmapped_closed_raw(closed_paths, mapping, archive_dir)
  assert os.path.normpath("/arch/2024-05-02.tar") in result
  assert os.path.normpath("/arch/2024-05-01.tar") not in result


def test_build_day_close_disqualified_daily_tars_ignores_pending_ingest():
  archive_dir = "/arch"
  d = lambda day: os.path.normpath("/arch/2024-06-%02d.tar" % day)
  pending_day = datetime(2024, 6, 1, 3, 0, 0)
  pending_path = "/raw/host/%d" % int(pending_day.timestamp())
  disqualified = build_day_close_disqualified_daily_tars(
      tgz_archive_dir=archive_dir,
      pending_stats_paths=[pending_path],
      inflight_paths=[],
  )
  assert d(1) not in disqualified


def test_build_day_close_disqualified_daily_tars_unions_all_sources():
  archive_dir = "/arch"
  d = lambda day: os.path.normpath("/arch/2024-06-%02d.tar" % day)
  pending_day = datetime(2024, 6, 1, 3, 0, 0)
  inflight_day = datetime(2024, 6, 2, 3, 0, 0)
  pending_path = "/raw/host/%d" % int(pending_day.timestamp())
  inflight_path = "/raw/host/%d" % int(inflight_day.timestamp())
  disqualified = build_day_close_disqualified_daily_tars(
      tgz_archive_dir=archive_dir,
      remaining_raw_by_gz={"/arch/2024-06-03.tar.zst": ["/raw/x"]},
      pending_stats_paths=[pending_path],
      inflight_paths=[inflight_path],
      pending_append_by_daily_tar={d(4): {"/raw/y"}, d(9): set()},
      in_flight_archive_tars=[d(5)],
      pending_archive_task_tars=[d(6)],
      unmapped_closed_raw_tars=[d(7)],
  )
  assert d(1) not in disqualified  # pending ingest no longer disqualifies
  assert d(2) in disqualified  # inflight append
  assert d(3) in disqualified  # remaining raw on disk
  assert d(4) in disqualified  # non-empty append cache
  assert d(5) in disqualified  # in-flight archive job
  assert d(6) in disqualified  # queued archive task
  assert d(7) in disqualified  # unmapped closed raw
  assert d(9) not in disqualified  # empty append-cache bucket ignored


def test_stats_path_ingest_sort_epoch_matches_collect_order(tmp_path):
  host_dir = tmp_path / "host.hpc"
  host_dir.mkdir()
  older = host_dir / "1000"
  newer = host_dir / "2000"
  older.write_text("x")
  newer.write_text("y")
  assert stats_path_ingest_sort_epoch(str(older)) == 1000
  assert stats_path_ingest_sort_epoch(str(newer)) == 2000
  collected = collect_stats_files_in_range(
      str(tmp_path), "all", None, ".hpc", force_full_scan=True)
  assert collected == [str(older), str(newer)]


def test_ingest_stream_past_calendar_day_empty_pending():
  day = date(2020, 1, 1)
  assert ingest_stream_past_calendar_day(
      day,
      pending_stats_paths=[],
      max_sort_epoch_for_day=None,
  )


def test_ingest_stream_past_calendar_day_blocks_same_day_pending():
  day = date(2020, 1, 1)
  epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
  pending = ["/raw/host.hpc/%d" % epoch]
  assert not ingest_stream_past_calendar_day(
      day,
      pending_stats_paths=pending,
      max_sort_epoch_for_day=epoch,
  )


def test_ingest_stream_past_calendar_day_true_when_min_pending_after_day():
  day = date(2020, 1, 1)
  day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
  day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
  pending = ["/raw/host.hpc/%d" % day2_epoch]
  assert ingest_stream_past_calendar_day(
      day,
      pending_stats_paths=pending,
      max_sort_epoch_for_day=day1_epoch,
  )


def test_ingest_stream_past_calendar_day_restart_without_epoch_history():
  day = date(2020, 1, 1)
  day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
  pending = ["/raw/host.hpc/%d" % day2_epoch]
  assert ingest_stream_past_calendar_day(
      day,
      pending_stats_paths=pending,
      max_sort_epoch_for_day=None,
  )


def test_find_immediate_day_close_candidates_orders_oldest_first(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_old = str(daily_dir / "2020-01-01.tar")
  tar_new = str(daily_dir / "2020-01-02.tar")
  open(tar_old, "wb").close()
  open(tar_new, "wb").close()
  day2_ts = datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp()
  pending_path = tmp_path / "raw_day2"
  pending_path.write_text("stats")
  os.utime(pending_path, (day2_ts, day2_ts))
  pending = [str(pending_path)]
  result = find_immediate_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      candidate_tar_paths=[tar_new, tar_old],
      disqualified_daily_tars=set(),
      unprocessed_by_tar={
          os.path.normpath(tar_new): pending,
      },
      local_tz=timezone.utc,
  )
  assert result == [os.path.normpath(tar_old)]


def test_find_immediate_day_close_candidates_skips_disqualified(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  result = find_immediate_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      candidate_tar_paths=[tar_path],
      disqualified_daily_tars={os.path.normpath(tar_path)},
      unprocessed_by_tar={},
      local_tz=timezone.utc,
  )
  assert result == []


def test_find_immediate_day_close_candidates_retries_when_tar_dropped_hint_but_tar_exists(
    tmp_path, monkeypatch,
):
  """Hint drift: ``tar_dropped`` phase with ``.tar`` still on disk must re-enqueue."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers_mod

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  monkeypatch.setattr(helpers_mod, "tar_day_dirty_by_mtime", lambda _p: False)
  result = find_immediate_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      candidate_tar_paths=[tar_path],
      disqualified_daily_tars=set(),
      unprocessed_by_tar={},
      local_tz=timezone.utc,
      day_phases={os.path.normpath(tar_path): {"phase": "tar_dropped"}},
  )
  assert result == [os.path.normpath(tar_path)]


def test_find_immediate_day_close_candidates_requires_unprocessed_map(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  assert find_immediate_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      candidate_tar_paths=[tar_path],
      disqualified_daily_tars=set(),
      unprocessed_by_tar=None,
      local_tz=timezone.utc,
  ) == []


def test_augment_unprocessed_by_tar_with_pending_paths(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      augment_unprocessed_by_tar_with_pending_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  day_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
  pending_path = "/raw/host.hpc/%d" % day_epoch
  augmented = augment_unprocessed_by_tar_with_pending_paths(
      {},
      pending_stats_paths=[pending_path],
      tgz_archive_dir=str(daily_dir),
      checkpoint_paths=set(),
  )
  assert augmented[tar_path] == [pending_path]


def test_daily_tar_eligible_for_day_close_submit_requires_checkpoint_complete(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      daily_tar_eligible_for_day_close_submit,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2021-03-15.tar"))
  open(tar_path, "wb").close()
  day_ts = datetime(2021, 3, 15, 12, tzinfo=timezone.utc).timestamp()
  raw_path = tmp_path / "raw_pending"
  raw_path.write_text("x")
  os.utime(raw_path, (day_ts, day_ts))
  eligible, reason = daily_tar_eligible_for_day_close_submit(
      tar_path,
      unprocessed_by_tar={tar_path: [str(raw_path)]},
      disqualified_daily_tars=set(),
      local_tz=timezone.utc,
      tgz_archive_dir=str(daily_dir),
  )
  assert not eligible
  assert reason == "checkpoint_incomplete"
  eligible, reason = daily_tar_eligible_for_day_close_submit(
      tar_path,
      unprocessed_by_tar={tar_path: ["/raw/ghost_only"]},
      disqualified_daily_tars=set(),
      local_tz=timezone.utc,
      tgz_archive_dir=str(daily_dir),
  )
  assert eligible
  assert reason == ""
  eligible, reason = daily_tar_eligible_for_day_close_submit(
      tar_path,
      unprocessed_by_tar={tar_path: []},
      disqualified_daily_tars=set(),
      local_tz=timezone.utc,
      tgz_archive_dir=str(daily_dir),
  )
  assert eligible
  assert reason == ""


def test_daily_tar_eligible_for_day_close_submit_allows_closed_raw_on_disk(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      daily_tar_eligible_for_day_close_submit,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-22.tar"))
  open(tar_path, "wb").close()

  class _ClosedRawCoord:
    enabled = True

    def has_closed_raw_on_disk(self, tar_norm):
      return tar_norm == tar_path

  eligible, reason = daily_tar_eligible_for_day_close_submit(
      tar_path,
      unprocessed_by_tar={tar_path: []},
      disqualified_daily_tars=set(),
      local_tz=timezone.utc,
      day_raw_removal=_ClosedRawCoord(),
  )
  assert eligible
  assert reason == ""


def test_build_unprocessed_raw_by_daily_tar_subtracts_checkpoint(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      build_unprocessed_raw_by_daily_tar,
      load_checkpoint_path_set,
  )

  host = tmp_path / "n.integration.test"
  host.mkdir()
  day = date(2021, 3, 15)
  ts = int(datetime(day.year, day.month, day.day, 10, tzinfo=timezone.utc).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2021-03-15.tar")
  open(tar_path, "wb").close()
  checkpoint_path = tmp_path / ".sync_timedb_state.json"
  checkpoint_path.write_text(
      json.dumps([{"path": str(seg), "size": seg.stat().st_size,
                   "mtime": int(seg.stat().st_mtime)}])
  )
  assert str(seg) in load_checkpoint_path_set(str(checkpoint_path))
  unprocessed = build_unprocessed_raw_by_daily_tar(
      str(tmp_path),
      ".integration.test",
      str(daily_dir),
      checkpoint_path=str(checkpoint_path),
  )
  assert unprocessed.get(os.path.normpath(tar_path), []) == []


def test_find_immediate_day_close_uses_checkpoint_not_pending(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  day_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
  host_dir = tmp_path / "host.hpc"
  host_dir.mkdir()
  raw_path = str(host_dir / str(day_epoch))
  open(raw_path, "wb").close()
  pending_same_day = [raw_path]
  result = find_immediate_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      candidate_tar_paths=[tar_path],
      disqualified_daily_tars=set(),
      unprocessed_by_tar={},
      local_tz=timezone.utc,
  )
  assert result == [os.path.normpath(tar_path)]
  result_blocked = find_immediate_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      candidate_tar_paths=[tar_path],
      disqualified_daily_tars=set(),
      unprocessed_by_tar={os.path.normpath(tar_path): pending_same_day},
      local_tz=timezone.utc,
  )
  assert result_blocked == []


def test_classify_day_close_candidates_reports_reasons(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  day_ts = datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp()
  raw_path = tmp_path / "raw_x"
  raw_path.write_text("x")
  os.utime(raw_path, (day_ts, day_ts))
  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      unprocessed_by_tar={tar_path: [str(raw_path)]},
      disqualification_reasons={
          tar_path: {"checkpoint_incomplete", "inflight_append_path"},
      },
      local_tz=timezone.utc,
  )
  by_tar = {e["tar_path"]: e for e in entries}
  assert by_tar[tar_path]["status"] == "waiting_on_ingest"
  assert "checkpoint_incomplete" in by_tar[tar_path]["reasons"]


def test_classify_no_eligible_deferred_status(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  waiting_tar = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  ready_tar = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(waiting_tar, "wb").close()
  open(ready_tar, "wb").close()
  day_ts = datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp()
  raw_waiting = tmp_path / "raw_waiting"
  raw_waiting.write_text("x")
  os.utime(raw_waiting, (day_ts, day_ts))
  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      unprocessed_by_tar={waiting_tar: [str(raw_waiting)]},
      disqualification_reasons={},
      local_tz=timezone.utc,
  )
  by_tar = {e["tar_path"]: e for e in entries}
  assert by_tar[waiting_tar]["status"] == "waiting_on_ingest"
  assert by_tar[ready_tar]["status"] == "ready_for_enqueue"
  assert "awaiting_janitor_discover" in by_tar[ready_tar]["reasons"]
  assert "mutable_tar_present" in by_tar[ready_tar]["reasons"]
  assert by_tar[ready_tar]["mutable_tar"] is True
  assert "eligible_deferred" not in {e.get("status") for e in entries}


def test_closed_raw_no_longer_blocks_day_close_candidate_report(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()

  class _FakeDayRaw:
    enabled = True

    def has_closed_raw_on_disk(self, _tar):
      return True

  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      unprocessed_by_tar={},
      disqualification_reasons={},
      local_tz=timezone.utc,
      day_raw_removal=_FakeDayRaw(),
  )
  by_tar = {e["tar_path"]: e for e in entries}
  assert by_tar[tar_path]["status"] == "ready_for_enqueue"
  assert "awaiting_janitor_discover" in by_tar[tar_path]["reasons"]
  assert "closed_raw_on_disk" not in by_tar[tar_path]["reasons"]


def test_build_remaining_raw_accepts_snapshot_no_nested_collect(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      build_remaining_raw_stats_by_daily_gz,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  collect_calls = {"n": 0}

  def boom_collect(*_a, **_k):
    collect_calls["n"] += 1
    return []

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.collect_stats_files_in_range",
      boom_collect,
  )
  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={str(tmp_path / "2020-01-01.tar.zst"): ["/raw/a"]},
  )
  result = build_remaining_raw_stats_by_daily_gz(
      str(tmp_path),
      "cluster.test",
      str(tmp_path / "daily"),
      maintenance_snapshot=snapshot,
  )
  assert collect_calls["n"] == 0
  assert result == snapshot.remaining_raw_by_gz


def test_live_unprocessed_reconcile_uses_snapshot_no_collect(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      build_live_unprocessed_by_tar_for_reconcile,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  collect_calls = {"n": 0}

  def boom_collect(*_a, **_k):
    collect_calls["n"] += 1
    return []

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.collect_stats_files_in_range",
      boom_collect,
  )
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_norm = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  raw_path = str(tmp_path / "host.example.com" / "123")
  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[raw_path],
      mapping={str(daily_dir / "2020-01-01.tar.zst"): [raw_path]},
      first_timestamp_by_path={raw_path: 123.0},
  )
  result = build_live_unprocessed_by_tar_for_reconcile(
      str(tmp_path),
      "example.com",
      str(daily_dir),
      checkpoint_paths=set(),
      maintenance_snapshot=snapshot,
  )
  assert collect_calls["n"] == 0
  assert tar_norm in result
  assert raw_path in result[tar_norm]


def test_live_unprocessed_reconcile_live_path_collects_when_no_snapshot(
    monkeypatch, tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      build_live_unprocessed_by_tar_for_reconcile,
  )

  collect_calls = {"n": 0}

  def fake_collect(archive_data_dir, start, end, host_name_ext, **kwargs):
    collect_calls["n"] += 1
    host_dir = tmp_path / ("n." + host_name_ext)
    host_dir.mkdir(parents=True, exist_ok=True)
    seg = host_dir / "100"
    seg.write_text("100 job1 host\n")
    return [str(seg)]

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.collect_stats_files_in_range",
      fake_collect,
  )
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  build_live_unprocessed_by_tar_for_reconcile(
      str(tmp_path),
      "example.com",
      str(daily_dir),
      checkpoint_paths=set(),
      maintenance_snapshot=None,
  )
  assert collect_calls["n"] == 1


def test_log_day_close_candidate_report_omits_skipped_no_work(capsys, monkeypatch):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      log_day_close_candidate_report,
  )

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod

  monkeypatch.setattr(cfg_mod, "get_sync_day_close_candidate_report", lambda: True)
  log_day_close_candidate_report(
      [{
          "tar_path": "/arch/2020-01-01.tar",
          "status": "skipped_no_work",
          "reasons": [],
          "unprocessed": 0,
          "phase": "tar_dropped",
      }],
      reason="test",
  )
  assert "day_close candidate" not in capsys.readouterr().out


def test_log_day_close_candidate_report_logs_queued_and_disqualified(capsys, monkeypatch):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      log_day_close_candidate_report,
  )

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod

  monkeypatch.setattr(cfg_mod, "get_sync_day_close_candidate_report", lambda: True)
  log_day_close_candidate_report(
      [
          {
              "tar_path": "/arch/2020-01-01.tar",
              "status": "queued",
              "reasons": ["scheduled_enqueue"],
              "unprocessed": 0,
              "phase": "",
          },
          {
              "tar_path": "/arch/2020-01-02.tar",
              "status": "waiting_on_ingest",
              "reasons": ["checkpoint_incomplete"],
              "unprocessed": 2,
              "phase": "",
          },
      ],
      reason="test",
  )
  out = capsys.readouterr().out
  assert (
      "day_close candidate report reason=test queued=1 "
      "waiting_on_ingest=1 ready_for_enqueue=0 disqualified=0 "
      "mutable_tar_n=0"
  ) in out
  assert "status=queued" in out
  assert "status=waiting_on_ingest" in out
  queued_line = [
      line for line in out.splitlines()
      if "status=queued" in line and "day_close candidate tar=" in line
  ][0]
  waiting_line = [
      line for line in out.splitlines()
      if "status=waiting_on_ingest" in line and "day_close candidate tar=" in line
  ][0]
  assert "queue_order=1" in queued_line
  assert "queue_order=" in waiting_line
  assert "queue_order=1" not in waiting_line
  assert "skipped_no_work" not in out


def test_log_day_close_candidate_report_includes_async_progress(capsys, monkeypatch):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      log_day_close_candidate_report,
  )

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod

  monkeypatch.setattr(cfg_mod, "get_sync_day_close_candidate_report", lambda: True)

  def progress(_tar):
    return {
        "last_progress": "raw_removal",
        "last_progress_age_s": 123.0,
    }

  log_day_close_candidate_report(
      [
          {
              "tar_path": "/arch/2026-04-15.tar",
              "status": "queued",
              "reasons": ["day_close_in_progress"],
              "unprocessed": 0,
              "phase": "",
          },
      ],
      reason="test",
      async_progress_fn=progress,
  )
  out = capsys.readouterr().out
  assert "async_last_progress=raw_removal" in out
  assert "async_age_s=123" in out


def test_log_day_close_candidate_report_silent_when_empty(capsys, monkeypatch):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      log_day_close_candidate_report,
  )

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod

  monkeypatch.setattr(cfg_mod, "get_sync_day_close_candidate_report", lambda: True)
  log_day_close_candidate_report([], reason="test")
  assert "day_close candidate report" not in capsys.readouterr().out


def test_oldest_day_unprocessed_frozen_logs_after_unchanged_reports(
    capsys, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      log_day_close_candidate_report,
      reset_oldest_day_unprocessed_frozen_state_for_tests,
  )

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod

  monkeypatch.setattr(cfg_mod, "get_sync_day_close_candidate_report", lambda: True)
  reset_oldest_day_unprocessed_frozen_state_for_tests()
  waiting = [
      {
          "tar_path": "/daily/2026-06-02.tar",
          "status": "waiting_on_ingest",
          "reasons": ["checkpoint_incomplete"],
          "unprocessed": 5495,
          "phase": "",
      },
      {
          "tar_path": "/daily/2026-06-05.tar",
          "status": "waiting_on_ingest",
          "reasons": ["checkpoint_incomplete"],
          "unprocessed": 100,
          "phase": "",
      },
  ]
  log_day_close_candidate_report(waiting, reason="tick1")
  out1 = capsys.readouterr().out
  assert "oldest_day_unprocessed_frozen" not in out1
  log_day_close_candidate_report(waiting, reason="tick2")
  out2 = capsys.readouterr().out
  assert "oldest_day_unprocessed_frozen" in out2
  assert "2026-06-02.tar" in out2
  assert "unprocessed=5495" in out2
  reset_oldest_day_unprocessed_frozen_state_for_tests()


def test_prepend_checkpoint_blocked_paths_not_excluded_when_processed():
  """Checkpoint-blocked paths must not be dropped when still in processed_files."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      prepend_checkpoint_incomplete_paths_to_pending,
  )

  blocked = ["/a", "/b"]
  processed = {"/a", "/x"}
  blocked_set = set(blocked)
  reconcile_exclude = processed - blocked_set
  merged = prepend_checkpoint_incomplete_paths_to_pending(
      ["/c"],
      blocked,
      exclude=reconcile_exclude,
  )
  assert merged == ["/a", "/b", "/c"]
  legacy = prepend_checkpoint_incomplete_paths_to_pending(
      ["/c"],
      blocked,
      exclude=processed,
  )
  assert legacy == ["/b", "/c"]


def test_select_ingest_chunk_paths_fallback_checkpoint_incomplete_on_disk(tmp_path):
  """When pending has only newer-day paths, fall back to checkpoint_incomplete_on_disk."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  blocked1 = tmp_path / "blocked1"
  blocked2 = tmp_path / "blocked2"
  newer = tmp_path / "newer"
  for path in (blocked1, blocked2, newer):
    path.write_text("x")
  os.utime(blocked1, (d1.timestamp(), d1.timestamp()))
  os.utime(blocked2, (d1.timestamp(), d1.timestamp()))
  os.utime(newer, (d2.timestamp(), d2.timestamp()))
  pending = [str(newer)]
  unprocessed = {tar_a: [str(blocked1), str(blocked2)]}
  chunk = select_ingest_chunk_paths(
      pending,
      oldest_tar=tar_a,
      unprocessed_by_tar=unprocessed,
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=10,
  )
  assert chunk == [str(blocked1), str(blocked2)]
  assert str(newer) not in chunk


def test_select_ingest_chunk_paths_fallback_logs_calendar_days(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  blocked1 = tmp_path / "blocked1"
  newer = tmp_path / "newer"
  for path in (blocked1, newer):
    path.write_text("x")
  os.utime(blocked1, (d1.timestamp(), d1.timestamp()))
  os.utime(newer, (d2.timestamp(), d2.timestamp()))
  logs = []
  chunk = select_ingest_chunk_paths(
      [str(newer)],
      oldest_tar=tar_a,
      unprocessed_by_tar={tar_a: [str(blocked1)]},
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=10,
      log_fn=lambda msg: logs.append(str(msg)),
  )
  assert chunk == [str(blocked1)]
  assert any("oldest_day_chunk_gate_fallback" in line for line in logs)
  assert any("calendar_days=" in line for line in logs)


def test_prepend_checkpoint_incomplete_paths_to_pending_dedupes_and_orders():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      prepend_checkpoint_incomplete_paths_to_pending,
  )

  pending = ["/b", "/c"]
  blocked = ["/a", "/b"]
  merged = prepend_checkpoint_incomplete_paths_to_pending(
      pending,
      blocked,
      exclude={"/x"},
  )
  assert merged == ["/a", "/b", "/c"]


def test_pending_minus_chunk_non_prefix_oldest_tar():
  """Non-prefix chunk must not use pending[len(chunk):] (drops head, requeues chunk)."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      pending_minus_chunk,
  )

  pending = ["/p/A", "/p/B", "/p/C", "/p/D", "/p/E"]
  chunk = ["/p/C", "/p/E"]
  assert pending_minus_chunk(pending, chunk) == ["/p/A", "/p/B", "/p/D"]
  # Prefix-slice bug would yield pending[2:] == [C, D, E] (wrong).
  assert pending[len(chunk):] == ["/p/C", "/p/D", "/p/E"]


def test_pending_minus_chunk_prefix_matches_slice():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      pending_minus_chunk,
  )

  pending = ["/p/A", "/p/B", "/p/C", "/p/D"]
  chunk = ["/p/A", "/p/B"]
  assert pending_minus_chunk(pending, chunk) == pending[len(chunk):]


def test_pending_minus_chunk_normpath_and_order():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      pending_minus_chunk,
  )

  pending = ["/p/A", "/p/B", "/p/C"]
  chunk = ["/p/./B"]
  assert pending_minus_chunk(pending, chunk) == ["/p/A", "/p/C"]


def test_resolved_checkpoint_path_set_includes_memory_entries(tmp_path):
  """In-memory checkpoint entries count before disk flush."""
  from collections import deque

  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      resolved_checkpoint_path_set,
  )

  raw_path = tmp_path / "host" / "seg"
  raw_path.parent.mkdir(parents=True)
  raw_path.write_text("1000 job cn001\n")
  checkpoint_path = str(tmp_path / ".sync_timedb_state.json")
  stat = raw_path.stat()
  memory_entries = deque([{
      "path": str(raw_path),
      "size": int(stat.st_size),
      "mtime": int(stat.st_mtime),
  }])
  merged = resolved_checkpoint_path_set(checkpoint_path, memory_entries)
  assert str(raw_path) in merged


def test_build_live_unprocessed_blocked_drops_after_memory_checkpoint(
    tmp_path,
):
  """Stale accrual blocked paths drop when merged checkpoint has them."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      build_live_unprocessed_by_tar_for_reconcile,
      resolved_checkpoint_path_set,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import (
      ArchiveMaintenanceSnapshot,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_norm = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_norm, "wb").close()
  raw_path = str(tmp_path / "host.example.com" / "123")
  os.makedirs(os.path.dirname(raw_path), exist_ok=True)
  open(raw_path, "wb").close()
  stat = os.stat(raw_path)
  memory_entries = [{
      "path": raw_path,
      "size": int(stat.st_size),
      "mtime": int(stat.st_mtime),
  }]
  checkpoint_path = str(tmp_path / ".sync_timedb_state.json")
  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[raw_path],
      mapping={str(daily_dir / "2020-01-01.tar.zst"): [raw_path]},
      first_timestamp_by_path={raw_path: 123.0},
  )
  stale = build_live_unprocessed_by_tar_for_reconcile(
      str(tmp_path),
      "example.com",
      str(daily_dir),
      checkpoint_path=checkpoint_path,
      checkpoint_paths=set(),
      maintenance_snapshot=snapshot,
  )
  assert raw_path in stale.get(tar_norm, [])
  merged = resolved_checkpoint_path_set(checkpoint_path, memory_entries)
  cleared = build_live_unprocessed_by_tar_for_reconcile(
      str(tmp_path),
      "example.com",
      str(daily_dir),
      checkpoint_path=checkpoint_path,
      checkpoint_paths=merged,
      maintenance_snapshot=snapshot,
  )
  assert cleared.get(tar_norm, []) == []


def test_remove_processed_path_clears_checkpoint_entry(tmp_path):
  from collections import deque

  from hpcperfstats.dbload import sync_timedb as st_mod

  checkpoint_path = str(tmp_path / ".sync_timedb_state.json")
  raw_path = str(tmp_path / "host" / "12345")
  os.makedirs(os.path.dirname(raw_path))
  with open(raw_path, "w", encoding="utf-8") as fh:
    fh.write("12345 job1 cn001\nline\n")
  processed_files = set()
  processed_files_order = deque()
  checkpoint_entries = deque()
  file_states = {}
  st_mod._add_processed_path(
      raw_path,
      processed_files,
      processed_files_order,
      checkpoint_entries,
      checkpoint_path,
      file_states=file_states,
  )
  assert raw_path in processed_files
  st_mod._remove_processed_path(
      raw_path,
      processed_files,
      processed_files_order,
      checkpoint_entries,
      checkpoint_path,
      file_states=file_states,
      persist=True,
  )
  assert raw_path not in processed_files
  assert len(checkpoint_entries) == 0
  assert file_states[raw_path] == st_mod.SyncFileState.DISCOVERED
  loaded = st_mod._load_sync_checkpoint(checkpoint_path)
  assert loaded == []


def test_handoff_priority_cap_keeps_head_paths(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      prepend_checkpoint_incomplete_paths_to_pending,
  )

  handoff = ["/h1", "/h2"]
  pending = ["/t1", "/t2", "/t3", "/t4", "/t5"]
  merged = prepend_checkpoint_incomplete_paths_to_pending(pending, handoff)
  ingest_queue_max = 4
  priority_n = len(handoff)
  tail_budget = max(0, ingest_queue_max - priority_n)
  head = merged[:priority_n]
  tail = merged[priority_n:][:tail_budget]
  capped = head + tail
  assert capped[:2] == handoff
  assert len(capped) == ingest_queue_max
  assert "/t1" in capped
  assert "/t5" not in capped


def test_oldest_checkpoint_incomplete_tar_returns_oldest_on_disk_day(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      oldest_checkpoint_incomplete_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_old = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_new = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_old, "wb").close()
  open(tar_new, "wb").close()
  d_old = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  d_new = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  raw_old = tmp_path / "old"
  raw_new = tmp_path / "new"
  raw_old.write_text("a")
  raw_new.write_text("b")
  os.utime(raw_old, (d_old.timestamp(), d_old.timestamp()))
  os.utime(raw_new, (d_new.timestamp(), d_new.timestamp()))
  unprocessed = {
      tar_new: [str(raw_new)],
      tar_old: [str(raw_old)],
  }
  assert oldest_checkpoint_incomplete_tar(
      unprocessed, tgz_archive_dir=str(daily_dir)) == tar_old


def test_classify_ghost_unprocessed_becomes_awaiting_janitor_discover(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      unprocessed_by_tar={tar_path: ["/ghost/only"]},
      disqualification_reasons={},
      local_tz=timezone.utc,
  )
  by_tar = {e["tar_path"]: e for e in entries}
  assert by_tar[tar_path]["status"] == "ready_for_enqueue"
  assert "awaiting_janitor_discover" in by_tar[tar_path]["reasons"]
  assert "mutable_tar_present" in by_tar[tar_path]["reasons"]
  assert "checkpoint_incomplete" not in by_tar[tar_path]["reasons"]
  assert by_tar[tar_path]["unprocessed"] == 0
  assert by_tar[tar_path]["unprocessed_list"] == 1


def test_populate_uses_tar_not_sealed_when_dirty(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """Dirty mutable tar must populate Redis from tar scan, not sealed zst stream."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_zst = tmp_path / "2024-06-02.tar.zst"
  day_tar = tmp_path / "2024-06-02.tar"
  inner = tmp_path / "member.txt"
  inner.write_text("payload")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="host/member")
  day_zst.write_bytes(b"not-real-zst")
  os.utime(day_zst, (1000, 1000))
  os.utime(day_tar, (2000, 2000))

  sealed_calls = {"n": 0}
  tar_calls = {"n": 0}

  def _counting_sealed(*_a, **_k):
    sealed_calls["n"] += 1
    return {"stale": 1}

  def _counting_tar(*_a, **_k):
    tar_calls["n"] += 1
    return {"host/member": inner.stat().st_size}

  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_sealed_scan", _counting_sealed,
  )
  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_tar_scan", _counting_tar,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: FakeRedis(),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".redis_lookup_full_members",
      lambda keys: None,
  )

  token = set_worker_pool_kind("populate-pool")
  try:
    members = helpers.get_existing_archive_members_for_daily_archive(str(day_zst))
  finally:
    reset_worker_pool_kind(token)
  assert tar_calls["n"] == 1
  assert sealed_calls["n"] == 0
  assert members.get("host/member") == inner.stat().st_size


def test_populate_uses_tar_when_tar_exists_even_if_sealed_clean(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """When mutable tar exists, populate uses tar even if sealed mtime is current."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_zst = tmp_path / "2024-06-03.tar.zst"
  day_tar = tmp_path / "2024-06-03.tar"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="host/raw")
  day_zst.write_bytes(b"sealed-placeholder")
  os.utime(day_tar, (1000, 1000))
  os.utime(day_zst, (2000, 2000))

  sealed_calls = {"n": 0}
  tar_calls = {"n": 0}

  def _forbidden_sealed(*_a, **_k):
    sealed_calls["n"] += 1
    raise AssertionError("tar exists; must not scan sealed")

  def _counting_tar(*_a, **_k):
    tar_calls["n"] += 1
    return {"host/raw": inner.stat().st_size}

  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_sealed_scan", _forbidden_sealed,
  )
  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_tar_scan", _counting_tar,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: FakeRedis(),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".redis_lookup_full_members",
      lambda keys: None,
  )

  token = set_worker_pool_kind("populate-pool")
  try:
    members = helpers.get_existing_archive_members_for_daily_archive(str(day_zst))
  finally:
    reset_worker_pool_kind(token)
  assert tar_calls["n"] == 1
  assert sealed_calls["n"] == 0
  assert members.get("host/raw") == inner.stat().st_size


def test_sealed_populate_when_tar_absent(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """Sealed populate runs only when sibling mutable tar is absent."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_zst = tmp_path / "2024-06-03b.tar.zst"
  inner = tmp_path / "raw_only.txt"
  inner.write_text("data")
  day_zst.write_bytes(b"sealed-placeholder")

  sealed_calls = {"n": 0}
  tar_calls = {"n": 0}

  def _counting_sealed(sealed_path, cache_key, tar_path=None):
    del sealed_path, cache_key, tar_path
    sealed_calls["n"] += 1
    return {"host/raw": inner.stat().st_size}

  def _forbidden_tar(*_a, **_k):
    tar_calls["n"] += 1
    raise AssertionError("no tar; must not use tar populate")

  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_sealed_scan", _counting_sealed,
  )
  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_tar_scan", _forbidden_tar,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: FakeRedis(),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".redis_lookup_full_members",
      lambda keys: None,
  )

  token = set_worker_pool_kind("populate-pool")
  try:
    helpers.get_existing_archive_members_for_daily_archive(str(day_zst))
  finally:
    reset_worker_pool_kind(token)
  assert sealed_calls["n"] == 1
  assert tar_calls["n"] == 0


def test_populate_tar_read_lock_wait_uses_populate_max_seconds(
    monkeypatch, tmp_path,
):
  """Tar populate fnctl wait uses populate_max_seconds when INI > 0."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2024-06-04.tar"
  tar_path.write_bytes(b"x")
  captured = {}

  def _capture_wait(path, timeout_seconds=60, expiry_seconds=None):
    captured["timeout"] = timeout_seconds
    from hpcperfstats.dbload.lib.file_locking import file_read_lock_wait as _real
    return _real(path, timeout_seconds=timeout_seconds, expiry_seconds=expiry_seconds)

  monkeypatch.setattr(helpers, "file_read_lock_wait", _capture_wait)
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_populate_max_seconds", lambda: 7200,
  )
  with helpers._populate_tar_file_read_lock_wait(str(tar_path)):
    pass
  assert captured["timeout"] == 7200.0


def test_tar_populate_waits_fnctl_until_write_lock_released(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """Tar populate blocks on fnctl read lock until append write lock releases."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.file_locking import file_write_lock
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

  day_tar = tmp_path / "2024-06-04.tar"
  inner = tmp_path / "member.txt"
  inner.write_text("payload")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="host/member")
  day_zst = tmp_path / "2024-06-04.tar.zst"
  day_zst.write_bytes(b"z")
  canonical = str(day_zst)

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: FakeRedis(),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 30,
  )

  lock_held = threading.Event()
  writer_errors = []

  def _hold_write_lock():
    try:
      with file_write_lock(str(day_tar), timeout_seconds=5):
        lock_held.set()
        time.sleep(0.35)
    except Exception as exc:
      writer_errors.append(exc)

  writer = threading.Thread(target=_hold_write_lock)
  writer.start()
  assert lock_held.wait(timeout=2)

  token = set_worker_pool_kind("populate-pool")
  started = time.time()
  try:
    members = helpers.execute_archive_members_populate_for_canonical(canonical)
  finally:
    reset_worker_pool_kind(token)
  writer.join(timeout=2)

  assert not writer_errors
  assert time.time() - started >= 0.25
  assert members.get("host/member") == inner.stat().st_size


def test_tar_append_merges_redis_without_invalidate(monkeypatch, tmp_path):
  """Successful tar_append merges Redis L2 instead of full invalidation."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  import hpcperfstats.dbload.sync_timedb as st
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      reset_archive_members_redis_client_for_tests,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
      build_archive_members_redis_keys,
      store_complete_members_in_redis,
  )

  reset_archive_members_redis_client_for_tests()
  raw_file = tmp_path / "1709123456"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-04.tar.zst")
  tar_path = daily_tar_path_from_compressed(archive_key)
  os.makedirs(os.path.dirname(tar_path) or ".", exist_ok=True)
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(raw_file), arcname=helpers.get_tar_member_name(str(raw_file)))
  open(archive_key, "wb").write(b"sealed")

  fake = FakeRedis()
  cache_key = helpers._daily_archive_members_cache_key(
      normalize_daily_compressed_path(archive_key),
  )
  keys = build_archive_members_redis_keys(cache_key)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )
  store_complete_members_in_redis(keys, {"existing": 10}, saw_duplicates=False)

  invalidated = []
  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "_append_to_tar", lambda *_a, **_k: None)
  monkeypatch.setattr(
      st.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      st,
      "invalidate_after_daily_tar_mutation",
      lambda path, **kw: invalidated.append((path, kw.get("reason"))),
  )

  new_raw = tmp_path / "1709123457"
  new_raw.write_text("1709123457 job2 cn002\n")
  assert st._archive_stats_files_body((archive_key, [str(new_raw)]))
  assert not invalidated
  member_name = helpers.get_tar_member_name(str(new_raw))
  assert fake.hget(keys.hash_key, member_name) == str(new_raw.stat().st_size)
  assert fake.hget(keys.hash_key, "existing") == "10"
  assert fake.get(keys.complete_key) == "1"


def test_archive_stats_files_body_prefers_redis_over_tar_scan_when_warm(
    monkeypatch, tmp_path,
):
  """Regression: archive pool append must not full-scan sibling .tar when Redis warm."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  import hpcperfstats.dbload.sync_timedb as st
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      reset_archive_members_redis_client_for_tests,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
      build_archive_members_redis_keys,
      store_complete_members_in_redis,
  )

  reset_archive_members_redis_client_for_tests()
  raw_existing = tmp_path / "1709123456"
  raw_existing.write_text("1709123456 job1 cn001\n")
  new_raw = tmp_path / "1709123457"
  new_raw.write_text("1709123457 job2 cn002\n")
  archive_key = str(tmp_path / "2024-03-05.tar.zst")
  tar_path = daily_tar_path_from_compressed(archive_key)
  os.makedirs(os.path.dirname(tar_path) or ".", exist_ok=True)
  with tarfile.open(tar_path, "w") as tf:
    tf.add(
        str(raw_existing),
        arcname=helpers.get_tar_member_name(str(raw_existing)),
    )
  open(archive_key, "wb").write(b"sealed")

  fake = FakeRedis()
  cache_key = helpers._daily_archive_members_cache_key(
      normalize_daily_compressed_path(archive_key),
  )
  keys = build_archive_members_redis_keys(cache_key)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )
  monkeypatch.setattr(st.cfg, "get_sync_archive_members_redis_enabled", lambda: True)
  member_name = helpers.get_tar_member_name(str(raw_existing))
  store_complete_members_in_redis(
      keys, {member_name: raw_existing.stat().st_size}, saw_duplicates=False,
  )

  tar_scan_calls = {"n": 0}
  original_tar_scan = st.get_existing_archive_members

  def counting_tar_scan(tar_p):
    tar_scan_calls["n"] += 1
    return original_tar_scan(tar_p)

  monkeypatch.setattr(st, "get_existing_archive_members", counting_tar_scan)

  logs = []
  monkeypatch.setattr(st, "log_print", lambda msg, **kw: logs.append(str(msg)))
  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "_append_to_tar", lambda *_a, **_k: None)

  assert st._archive_stats_files_body((archive_key, [str(new_raw)]))
  assert tar_scan_calls["n"] == 0
  assert any(
      "archive_job_begin" in line and "members_source=redis" in line
      for line in logs
  )
  assert any(
      "archive_job_done" in line and "elapsed_s=" in line and "outcome=ok" in line
      for line in logs
  )


def test_archive_append_cold_redis_completes_with_inflight_first(
    monkeypatch, tmp_path, capsys,
):
  """F6: inflight-first archive job must still reach archive_job_begin on cold Redis."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  import hpcperfstats.dbload.sync_timedb as st
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      reset_archive_members_redis_client_for_tests,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  reset_archive_members_redis_client_for_tests()
  raw_existing = tmp_path / "1709123456"
  raw_existing.write_text("1709123456 job1 cn001\n")
  new_raw = tmp_path / "1709123457"
  new_raw.write_text("1709123457 job2 cn002\n")
  archive_key = str(tmp_path / "2026-06-03.tar.zst")
  tar_path = daily_tar_path_from_compressed(archive_key)
  os.makedirs(os.path.dirname(tar_path) or ".", exist_ok=True)
  with tarfile.open(tar_path, "w") as tf:
    tf.add(
        str(raw_existing),
        arcname=helpers.get_tar_member_name(str(raw_existing)),
    )
  open(archive_key, "wb").write(b"sealed")

  fake = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )
  monkeypatch.setattr(st.cfg, "get_sync_archive_members_redis_enabled", lambda: True)

  import hpcperfstats.dbload.lib.sync_timedb_archive_members_redis as redis_mod

  call_order = []
  original_set_inflight = redis_mod.set_archive_append_inflight

  def inflight_first(*args, **kwargs):
    call_order.append("inflight")
    return original_set_inflight(*args, **kwargs)

  original_lookup = st.get_existing_archive_members_for_daily_archive

  def lookup_traced(canonical):
    call_order.append("lookup")
    return original_lookup(canonical)

  monkeypatch.setattr(redis_mod, "set_archive_append_inflight", inflight_first)
  monkeypatch.setattr(
      st,
      "get_existing_archive_members_for_daily_archive",
      lookup_traced,
  )

  logs = []
  monkeypatch.setattr(st, "log_print", lambda msg, **kw: logs.append(str(msg)))
  _patch_archive_gate_pass(monkeypatch)
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "_append_to_tar", lambda *_a, **_k: None)

  assert st._archive_stats_files_body((archive_key, [str(new_raw)]))
  assert call_order.index("inflight") < call_order.index("lookup")
  combined = "\n".join(logs) + capsys.readouterr().out
  assert "archive_job_begin" in combined and "members_source=tar_scan" in combined
  assert "populate_source=tar" in combined
  assert "archive_job_done" in combined


def test_select_ingest_chunk_paths_oldest_tar_only(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  p1 = tmp_path / "p1"
  p2 = tmp_path / "p2"
  p3 = tmp_path / "p3"
  for path in (p1, p2, p3):
    path.write_text("x")
  os.utime(p1, (d1.timestamp(), d1.timestamp()))
  os.utime(p2, (d1.timestamp(), d1.timestamp()))
  os.utime(p3, (d2.timestamp(), d2.timestamp()))
  pending = [str(p1), str(p2), str(p3)]
  unprocessed = {tar_a: [str(p1), str(p2)]}
  chunk = select_ingest_chunk_paths(
      pending,
      oldest_tar=tar_a,
      unprocessed_by_tar=unprocessed,
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=10,
  )
  # Oldest day first, then pad from remaining pending up to chunk_size.
  assert chunk == [str(p1), str(p2), str(p3)]
  assert chunk[:2] == [str(p1), str(p2)]


def test_select_ingest_chunk_paths_pads_to_chunk_size_after_oldest(tmp_path):
  """Gated oldest-day paths stay first; later-day pending fills to chunk_size."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  oldest_paths = []
  for i in range(2):
    path = tmp_path / ("o%d" % i)
    path.write_text("x")
    os.utime(path, (d1.timestamp(), d1.timestamp()))
    oldest_paths.append(str(path))
  later_paths = []
  for i in range(5):
    path = tmp_path / ("l%d" % i)
    path.write_text("x")
    os.utime(path, (d2.timestamp(), d2.timestamp()))
    later_paths.append(str(path))
  pending = oldest_paths + later_paths
  unprocessed = {tar_a: list(oldest_paths)}
  logs = []
  chunk = select_ingest_chunk_paths(
      pending,
      oldest_tar=tar_a,
      unprocessed_by_tar=unprocessed,
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=5,
      log_fn=logs.append,
  )
  assert len(chunk) == 5
  assert chunk[:2] == oldest_paths
  assert chunk[2:] == later_paths[:3]
  assert any("chunk_pad_n=3" in line for line in logs)


def test_select_ingest_chunk_paths_no_pad_when_oldest_fills_chunk(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  oldest_paths = []
  for i in range(4):
    path = tmp_path / ("o%d" % i)
    path.write_text("x")
    os.utime(path, (d1.timestamp(), d1.timestamp()))
    oldest_paths.append(str(path))
  later = tmp_path / "later"
  later.write_text("x")
  os.utime(later, (d2.timestamp(), d2.timestamp()))
  pending = oldest_paths + [str(later)]
  unprocessed = {tar_a: list(oldest_paths)}
  logs = []
  chunk = select_ingest_chunk_paths(
      pending,
      oldest_tar=tar_a,
      unprocessed_by_tar=unprocessed,
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=3,
      log_fn=logs.append,
  )
  assert chunk == oldest_paths[:3]
  assert str(later) not in chunk
  assert not any("chunk_pad_n=" in line for line in logs)


def test_select_ingest_chunk_paths_oldest_tar_1500_paths(tmp_path):
  """Oldest blocked tar monopolizes chunk even with 1500+ handoff paths."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  handoff_paths = []
  for i in range(1578):
    path = tmp_path / ("h%d" % i)
    path.write_text("x")
    os.utime(path, (d1.timestamp(), d1.timestamp()))
    handoff_paths.append(str(path))
  tail_path = tmp_path / "tail"
  tail_path.write_text("x")
  os.utime(tail_path, (d2.timestamp(), d2.timestamp()))
  pending = handoff_paths + [str(tail_path)]
  unprocessed = {tar_a: list(handoff_paths)}
  chunk = select_ingest_chunk_paths(
      pending,
      oldest_tar=tar_a,
      unprocessed_by_tar=unprocessed,
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=1000,
  )
  assert len(chunk) == 1000
  assert str(tail_path) not in chunk
  assert all(path in handoff_paths for path in chunk)


def test_try_reuse_pending_reconcile_unprocessed_cache_skip_vs_rescan():
  """Same oldest+incomplete within TTL reuses cache; TTL expiry or zero incomplete rescans."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      try_reuse_pending_reconcile_unprocessed_cache,
  )

  cached = {"/archive/2020-01-01.tar": ["/a/1", "/a/2"]}
  reused = try_reuse_pending_reconcile_unprocessed_cache(
      cached=cached,
      last_mono=100.0,
      mono_now=150.0,
      ttl_s=120.0,
      last_incomplete_n=72,
      last_oldest_tar="/archive/2020-01-01.tar",
  )
  assert reused is not None
  assert reused[0] is cached
  assert reused[1] == "/archive/2020-01-01.tar"
  assert reused[2] == 72
  assert reused[3] == "unchanged_incomplete"

  expired = try_reuse_pending_reconcile_unprocessed_cache(
      cached=cached,
      last_mono=100.0,
      mono_now=230.0,
      ttl_s=120.0,
      last_incomplete_n=72,
      last_oldest_tar="/archive/2020-01-01.tar",
  )
  assert expired is None

  zero_inc = try_reuse_pending_reconcile_unprocessed_cache(
      cached=cached,
      last_mono=100.0,
      mono_now=110.0,
      ttl_s=120.0,
      last_incomplete_n=0,
      last_oldest_tar="/archive/2020-01-01.tar",
  )
  assert zero_inc is None

  stall = try_reuse_pending_reconcile_unprocessed_cache(
      cached=cached,
      last_mono=100.0,
      mono_now=110.0,
      ttl_s=120.0,
      last_incomplete_n=72,
      last_oldest_tar="/archive/2020-01-01.tar",
      stall_incomplete_n=72,
  )
  assert stall is not None
  assert stall[3] == "oldest_day_gate_stall_unchanged"


def test_handoff_priority_cap_explicit_wave_when_priority_exceeds_max():
  """When handoff_priority_n >= ingest_queue_max, cap is an explicit head wave."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      prepend_checkpoint_incomplete_paths_to_pending,
  )

  handoff = ["/h%d" % i for i in range(2500)]
  pending = ["/t%d" % i for i in range(100)]
  merged = prepend_checkpoint_incomplete_paths_to_pending(pending, handoff)
  ingest_queue_max = 2000
  priority_n = len(handoff)
  assert priority_n >= ingest_queue_max
  capped = merged[:ingest_queue_max]
  assert len(capped) == ingest_queue_max
  assert capped[0] == "/h0"
  assert capped[-1] == "/h1999"
  assert "/t0" not in capped


def test_reconcile_orphan_inflight_for_oldest_tar_reclaims_without_archive_job(
    tmp_path,
):
  """Orphan inflight paths on oldest tar are reclaimed when no archive owns them."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      reconcile_orphan_inflight_for_oldest_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_a, "wb").close()
  blocked = []
  for i in range(3):
    path = tmp_path / ("b%d" % i)
    path.write_text("1000 job cn001\n")
    os.utime(path, (d1.timestamp(), d1.timestamp()))
    blocked.append(str(path))
  inflight = set(blocked)
  reclaimed = reconcile_orphan_inflight_for_oldest_tar(
      oldest_tar=tar_a,
      blocked_paths=blocked,
      inflight_archive_paths=inflight,
      pending_append_by_daily_tar={},
      in_flight_archive_tars=set(),
      tgz_archive_dir=str(daily_dir),
      reclaim_throttle_s=0.0,
  )
  assert set(reclaimed) == set(blocked)


def test_reconcile_orphan_inflight_skips_active_archive_job(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      reconcile_orphan_inflight_for_oldest_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_a, "wb").close()
  path = str(tmp_path / "blocked")
  path_obj = tmp_path / "blocked"
  path_obj.write_text("1000 job cn001\n")
  os.utime(path_obj, (d1.timestamp(), d1.timestamp()))
  reclaimed = reconcile_orphan_inflight_for_oldest_tar(
      oldest_tar=tar_a,
      blocked_paths=[path],
      inflight_archive_paths={path},
      pending_append_by_daily_tar={},
      in_flight_archive_tars={tar_a},
      tgz_archive_dir=str(daily_dir),
      reclaim_throttle_s=0.0,
  )
  assert reclaimed == []


def test_reconcile_orphan_inflight_reclaims_cross_day_bucket_mismatch(tmp_path):
  """Cross-day bucket blocked paths in inflight are reclaimed without archive job."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      reconcile_orphan_inflight_for_oldest_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  path_obj = tmp_path / "cross_day_blocked"
  path_obj.write_text("1000 job cn001\n")
  os.utime(path_obj, (d2.timestamp(), d2.timestamp()))
  path = str(path_obj)
  logs = []
  reclaimed = reconcile_orphan_inflight_for_oldest_tar(
      oldest_tar=tar_a,
      blocked_paths=[path],
      inflight_archive_paths={path},
      pending_append_by_daily_tar={},
      in_flight_archive_tars=set(),
      tgz_archive_dir=str(daily_dir),
      reclaim_throttle_s=0.0,
      log_fn=lambda msg, **kwargs: logs.append(str(msg)),
  )
  assert reclaimed == [path]
  assert any("cross_day_n=1" in line for line in logs)
  assert any("detail=cross_day_bucket" in line for line in logs)


def test_reconcile_orphan_inflight_skips_cross_day_when_calendar_tar_active(
    tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      reconcile_orphan_inflight_for_oldest_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  path_obj = tmp_path / "cross_day_blocked"
  path_obj.write_text("1000 job cn001\n")
  os.utime(path_obj, (d2.timestamp(), d2.timestamp()))
  path = str(path_obj)
  reclaimed = reconcile_orphan_inflight_for_oldest_tar(
      oldest_tar=tar_a,
      blocked_paths=[path],
      inflight_archive_paths={path},
      pending_append_by_daily_tar={},
      in_flight_archive_tars={tar_b},
      tgz_archive_dir=str(daily_dir),
      reclaim_throttle_s=0.0,
  )
  assert reclaimed == []


def test_select_ingest_chunk_paths_cross_day_inflight_returns_chunk_after_reclaim(
    tmp_path,
):
  """Fallback chunk is empty while cross-day path is inflight; non-empty after reclaim."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  blocked_obj = tmp_path / "cross_day_blocked"
  tail_obj = tmp_path / "tail"
  blocked_obj.write_text("1000 job cn001\n")
  tail_obj.write_text("2000 job cn002\n")
  os.utime(blocked_obj, (d2.timestamp(), d2.timestamp()))
  os.utime(tail_obj, (d2.timestamp(), d2.timestamp()))
  blocked = str(blocked_obj)
  unprocessed = {tar_a: [blocked]}
  pending = [str(tail_obj)]
  inflight = {blocked}
  empty_chunk = select_ingest_chunk_paths(
      pending,
      oldest_tar=tar_a,
      unprocessed_by_tar=unprocessed,
      inflight_archive_paths=inflight,
      tgz_archive_dir=str(daily_dir),
      chunk_size=10,
  )
  assert empty_chunk == [str(tail_obj)]
  after_reclaim = select_ingest_chunk_paths(
      pending,
      oldest_tar=tar_a,
      unprocessed_by_tar=unprocessed,
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=10,
  )
  assert after_reclaim == [str(tail_obj)]


def test_select_ingest_chunk_paths_cross_day_only_defers_to_pending_head(tmp_path):
  """Cross-day-only under oldest_tar: gate inactive (aligned empty), pending head wins."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  may26_tar = os.path.normpath(str(daily_dir / "2026-05-26.tar"))
  open(may26_tar, "wb").close()
  d_may23 = datetime(2026, 5, 23, 12, tzinfo=timezone.utc)
  d_jun23 = datetime(2026, 6, 23, 12, tzinfo=timezone.utc)
  d_jun24 = datetime(2026, 6, 24, 12, tzinfo=timezone.utc)
  may_pending = tmp_path / "host" / "may_pending"
  jun_blocked1 = tmp_path / "host" / "jun_blocked1"
  jun_blocked2 = tmp_path / "host" / "jun_blocked2"
  may_pending.parent.mkdir(parents=True)
  for path in (may_pending, jun_blocked1, jun_blocked2):
    path.write_text("x")
  os.utime(may_pending, (d_may23.timestamp(), d_may23.timestamp()))
  os.utime(jun_blocked1, (d_jun23.timestamp(), d_jun23.timestamp()))
  os.utime(jun_blocked2, (d_jun24.timestamp(), d_jun24.timestamp()))
  pending = [str(may_pending)]
  blocked = [str(jun_blocked1), str(jun_blocked2)]
  chunk = select_ingest_chunk_paths(
      pending,
      oldest_tar=may26_tar,
      unprocessed_by_tar={may26_tar: blocked},
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=1000,
  )
  assert chunk == pending


def test_select_ingest_chunk_paths_cross_day_only_empty_pending_uses_fallback(
    tmp_path,
):
  """Cross-day-only under oldest_tar with empty pending: no aligned work to dispatch."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
  tar_a = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_a, "wb").close()
  blocked_obj = tmp_path / "cross_day_blocked"
  blocked_obj.write_text("1000 job cn001\n")
  os.utime(blocked_obj, (d2.timestamp(), d2.timestamp()))
  blocked = str(blocked_obj)
  chunk = select_ingest_chunk_paths(
      [],
      oldest_tar=tar_a,
      unprocessed_by_tar={tar_a: [blocked]},
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=10,
  )
  assert chunk == []


def test_select_ingest_chunk_prefers_handoff_across_oldest_tar(tmp_path):
  """Cross-day handoff paths dispatch before oldest_tar gate (May vs June stall)."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  may_tar = os.path.normpath(str(daily_dir / "2026-05-27.tar"))
  jun_tar = os.path.normpath(str(daily_dir / "2026-06-07.tar"))
  open(may_tar, "wb").close()
  open(jun_tar, "wb").close()
  d_may = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
  d_jun = datetime(2026, 6, 7, 12, tzinfo=timezone.utc)
  may_handoff = tmp_path / "host" / "may_handoff"
  may_handoff.parent.mkdir(parents=True)
  may_handoff.write_text("1000 job cn001\n")
  os.utime(may_handoff, (d_may.timestamp(), d_may.timestamp()))
  jun_paths = []
  for i in range(5):
    path = tmp_path / "host" / ("jun_%d" % i)
    path.write_text("2000 job cn002\n")
    os.utime(path, (d_jun.timestamp(), d_jun.timestamp()))
    jun_paths.append(str(path))
  pending = list(jun_paths) + [str(may_handoff)]
  chunk = select_ingest_chunk_paths(
      pending,
      oldest_tar=jun_tar,
      unprocessed_by_tar={jun_tar: jun_paths},
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=1000,
      handoff_priority_paths={str(may_handoff)},
  )
  assert chunk[0] == str(may_handoff)
  assert str(may_handoff) in chunk
  assert all(path in jun_paths for path in chunk[1:])


def test_select_ingest_chunk_skips_misbucket_handoff_without_daily_archive(tmp_path):
  """Misbucket epoch path (e.g. 1783181172 → July) must not pin every June chunk."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      handoff_path_lacks_daily_archive,
      select_ingest_chunk_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  may_tar = os.path.normpath(str(daily_dir / "2026-05-30.tar"))
  jun_tar = os.path.normpath(str(daily_dir / "2026-06-07.tar"))
  open(may_tar, "wb").close()
  open(jun_tar, "wb").close()
  # Basename epoch 1783181172 → 2026-07-04; no July tar/zst on disk.
  misbucket = tmp_path / "i617-114.vista.tacc.utexas.edu" / "1783181172"
  misbucket.parent.mkdir(parents=True)
  misbucket.write_text("1000 job cn001\n")
  d_jun = datetime(2026, 6, 7, 12, tzinfo=timezone.utc)
  jun_path = tmp_path / "host" / "jun_0"
  jun_path.parent.mkdir(parents=True, exist_ok=True)
  jun_path.write_text("2000 job cn002\n")
  os.utime(jun_path, (d_jun.timestamp(), d_jun.timestamp()))
  assert handoff_path_lacks_daily_archive(str(misbucket), str(daily_dir))
  logs = []
  chunk = select_ingest_chunk_paths(
      [str(jun_path)],
      oldest_tar=jun_tar,
      unprocessed_by_tar={jun_tar: [str(jun_path)]},
      inflight_archive_paths=set(),
      tgz_archive_dir=str(daily_dir),
      chunk_size=1000,
      handoff_priority_paths={str(misbucket)},
      log_fn=lambda msg, **_k: logs.append(str(msg)),
  )
  assert str(misbucket) not in chunk
  assert chunk == [str(jun_path)]
  assert any("handoff_cross_day_skip" in line and "no_daily_archive" in line for line in logs)


def test_age_misbucket_handoff_priority_paths_clears_and_returns_source(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      age_misbucket_handoff_priority_paths,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  may_tar = os.path.normpath(str(daily_dir / "2026-05-30.tar"))
  open(may_tar, "wb").close()
  misbucket = tmp_path / "i617-114.vista.tacc.utexas.edu" / "1783181172"
  misbucket.parent.mkdir(parents=True)
  misbucket.write_text("x")
  handoff = {str(misbucket)}
  source_map = {str(misbucket): may_tar}
  logs = []
  clear_sources = age_misbucket_handoff_priority_paths(
      handoff,
      tgz_archive_dir=str(daily_dir),
      handoff_source_tar_by_path=source_map,
      log_fn=lambda msg, **_k: logs.append(str(msg)),
  )
  assert handoff == set()
  assert source_map == {}
  assert clear_sources == {may_tar}
  assert any("handoff_priority_age" in line and "2026-07-04" in line for line in logs)


def test_sort_pending_stats_paths_oldest_first(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      sort_pending_stats_paths_oldest_first,
  )

  base_ts = int(datetime(2020, 6, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp())
  paths = []
  for offset in (2, 0, 1):
    path = tmp_path / str(base_ts + offset * 60)
    path.write_text("x")
    paths.append(str(path))
  sorted_paths = sort_pending_stats_paths_oldest_first(paths)
  assert [os.path.basename(path) for path in sorted_paths] == [
      str(base_ts),
      str(base_ts + 60),
      str(base_ts + 120),
  ]


def test_merge_rescan_discovered_into_pending_retains_quiet_host(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      merge_rescan_discovered_into_pending,
  )

  host_a = tmp_path / ("a." + _ARCH_HOST_SUFFIX)
  host_b = tmp_path / ("b." + _ARCH_HOST_SUFFIX)
  host_a.mkdir()
  host_b.mkdir()
  may_ts = int(datetime(2026, 5, 22, 12, tzinfo=timezone.utc).timestamp())
  jun_ts = int(datetime(2026, 6, 24, 12, tzinfo=timezone.utc).timestamp())
  may_path = host_a / str(may_ts)
  jun_path = host_b / str(jun_ts)
  may_path.write_text("x")
  jun_path.write_text("x")
  existing = [str(may_path)]
  discovered = [str(jun_path)]
  merged = merge_rescan_discovered_into_pending(
      existing,
      discovered,
      processed_exclude=set(),
  )
  assert merged == [str(may_path), str(jun_path)]


def test_cap_pending_sort_retains_global_oldest_head():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      cap_pending_stats_file_list,
      sort_pending_stats_paths_oldest_first,
  )

  may_paths = ["/archive/host/%d" % (1_000_000 + index) for index in range(1000)]
  handoff_paths = ["/archive/host/%d" % (2_000_000 + index) for index in range(1500)]
  merged = handoff_paths + may_paths
  capped = cap_pending_stats_file_list(
      sort_pending_stats_paths_oldest_first(merged),
      2000,
      log_fn=lambda *_a, **_k: None,
  )
  assert capped[0] == may_paths[0]
  assert len(capped) == 2000
  assert may_paths[-1] in capped


def test_handoff_cap_preserves_oldest_blocked_head():
  """Oldest-tar blocked paths stay in capped pending when handoff tail is trimmed."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      prepend_checkpoint_incomplete_paths_to_pending,
  )

  handoff = ["/handoff/%d" % i for i in range(1500)]
  blocked = ["/blocked/%d" % i for i in range(5)]
  tail = ["/tail/%d" % i for i in range(100)]
  merged = prepend_checkpoint_incomplete_paths_to_pending(
      tail,
      blocked,
  )
  merged = prepend_checkpoint_incomplete_paths_to_pending(
      merged,
      handoff,
  )
  ingest_queue_max = 2000
  priority_n = len(handoff)
  reserved = blocked[:3]
  reserved_set = set(reserved)
  tail_budget = max(0, ingest_queue_max - priority_n - len(reserved))
  head = merged[:priority_n]
  tail_paths = [path for path in merged[priority_n:] if path not in reserved_set]
  capped = reserved + head + tail_paths[:tail_budget]
  assert all(path in capped for path in reserved)
  assert capped[0] == "/blocked/0"
  assert "/handoff/0" in capped
  assert len(capped) <= ingest_queue_max


def test_rescan_force_snapshot_paths_uses_closed_list_despite_rescan_count(
    tmp_path,
    monkeypatch,
):
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  host_dir = tmp_path / "host.cluster.test"
  host_dir.mkdir(parents=True)
  snap_path = host_dir / "snap.stats"
  walk_path = host_dir / "walk.stats"
  snap_path.write_text("1\n", encoding="utf-8")
  walk_path.write_text("1\n", encoding="utf-8")
  hints = {"__rescan_count__": 3}
  monkeypatch.setattr(
      helpers,
      "collect_stats_files_in_range",
      lambda *_a, **_k: [str(walk_path)],
  )
  result = helpers.rescan_pending_stats_files(
      str(tmp_path),
      "all",
      None,
      "cluster.test",
      set(),
      host_scan_hints=hints,
      startup_closed_paths=[str(snap_path)],
      force_snapshot_paths=True,
  )
  assert result == [str(snap_path)]
  assert hints["__rescan_count__"] == 4


def test_supplement_pending_paths_from_closed_paths_refills_toward_max(
    tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      supplement_pending_paths_from_closed_paths,
  )

  host_dir = tmp_path / "host.cluster.test"
  host_dir.mkdir(parents=True)
  closed = []
  for index in range(10):
    path = host_dir / ("seg_%d.stats" % index)
    path.write_text("1\n", encoding="utf-8")
    closed.append(str(path))
  capped = supplement_pending_paths_from_closed_paths(
      [closed[0]],
      closed_paths=closed,
      max_size=5,
      processed_exclude=set(),
      log_fn=lambda *_a, **_k: None,
  )
  assert len(capped) == 5
  assert capped[0] == closed[0]


def test_cap_pending_with_blocked_retention_keeps_global_head():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      cap_pending_stats_with_blocked_retention,
  )

  blocked = ["/blocked/%d" % i for i in range(2)]
  tail = ["/tail/%d" % i for i in range(100)]
  merged = blocked + tail
  capped = cap_pending_stats_with_blocked_retention(
      merged,
      max_size=20,
      blocked_paths=blocked,
      handoff_priority_paths=[],
      log_fn=lambda *_a, **_k: None,
  )
  assert len(capped) == 20
  assert capped[0] == blocked[0]
  assert capped[1] == blocked[1]


def test_cross_day_db_complete_helper_contract():
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      all_ingest_outcomes_db_skip_head_tail,
      chunk_was_cross_day_defer_dispatch,
  )

  oldest_tar = "/archive/daily/2026-05-26.tar"
  tgz = "/archive/daily"
  june_path = "/archive/host.cluster.test/2026-06-01_12-00-00.stats"
  assert chunk_was_cross_day_defer_dispatch(
      [june_path],
      oldest_tar,
      incomplete_n=2,
      tgz_archive_dir=tgz,
  )
  outcomes = [(june_path, "db_skip", "head_tail")]
  assert all_ingest_outcomes_db_skip_head_tail(outcomes)
  assert not all_ingest_outcomes_db_skip_head_tail(
      [(june_path, "ingested", "no")],
  )


def test_oldest_checkpoint_incomplete_tar_skips_cross_day_only(tmp_path):
  """May-27 with only July-mapped path is not oldest; next aligned day wins."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      oldest_checkpoint_incomplete_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  may27 = os.path.normpath(str(daily_dir / "2026-05-27.tar"))
  may29 = os.path.normpath(str(daily_dir / "2026-05-29.tar"))
  open(may27, "wb").close()
  open(may29, "wb").close()
  d_july = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
  d_may29 = datetime(2026, 5, 29, 12, tzinfo=timezone.utc)
  cross = tmp_path / "cross_july"
  aligned = tmp_path / "aligned_may29"
  cross.write_text("x")
  aligned.write_text("x")
  os.utime(cross, (d_july.timestamp(), d_july.timestamp()))
  os.utime(aligned, (d_may29.timestamp(), d_may29.timestamp()))
  unprocessed = {
      may27: [str(cross)],
      may29: [str(aligned)],
  }
  assert oldest_checkpoint_incomplete_tar(
      unprocessed, tgz_archive_dir=str(daily_dir)) == may29


def test_day_close_eligibility_ignores_cross_day_unprocessed(tmp_path):
  """checkpoint_incomplete is false when only misaligned paths remain."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      daily_tar_eligible_for_day_close_submit,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-27.tar"))
  open(tar_path, "wb").close()
  d_july = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
  cross = tmp_path / "cross_july"
  cross.write_text("x")
  os.utime(cross, (d_july.timestamp(), d_july.timestamp()))
  eligible, reason = daily_tar_eligible_for_day_close_submit(
      tar_path,
      unprocessed_by_tar={tar_path: [str(cross)]},
      disqualified_daily_tars=set(),
      local_tz=timezone.utc,
      tgz_archive_dir=str(daily_dir),
  )
  assert eligible
  assert reason == ""


def test_disq_reasons_checkpoint_incomplete_aligned_only(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      build_disqualification_reasons_by_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  may27 = os.path.normpath(str(daily_dir / "2026-05-27.tar"))
  open(may27, "wb").close()
  d_july = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
  cross = tmp_path / "cross_july"
  cross.write_text("x")
  os.utime(cross, (d_july.timestamp(), d_july.timestamp()))
  reasons = build_disqualification_reasons_by_tar(
      tgz_archive_dir=str(daily_dir),
      unprocessed_by_tar={may27: [str(cross)]},
  )
  assert "checkpoint_incomplete" not in reasons.get(may27, set())


def test_classify_no_stale_checkpoint_incomplete_when_unprocessed_zero(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-31.tar"))
  open(tar_path, "wb").close()
  d_july = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
  cross = tmp_path / "cross_july"
  cross.write_text("x")
  os.utime(cross, (d_july.timestamp(), d_july.timestamp()))
  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      unprocessed_by_tar={tar_path: [str(cross)]},
      disqualification_reasons={tar_path: {"checkpoint_incomplete"}},
      local_tz=timezone.utc,
  )
  by_tar = {e["tar_path"]: e for e in entries}
  assert by_tar[tar_path]["status"] == "ready_for_enqueue"
  assert "checkpoint_incomplete" not in by_tar[tar_path]["reasons"]
  assert "awaiting_janitor_discover" in by_tar[tar_path]["reasons"]
  assert "mutable_tar_present" in by_tar[tar_path]["reasons"]
  assert by_tar[tar_path]["unprocessed"] == 0
  assert by_tar[tar_path]["unprocessed_cross_day_n"] == 1


def test_classify_mutable_tar_present_reason(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-01.tar"))
  open(tar_path, "wb").close()
  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      unprocessed_by_tar={},
      disqualification_reasons={},
      local_tz=timezone.utc,
  )
  by_tar = {e["tar_path"]: e for e in entries}
  assert by_tar[tar_path]["status"] == "ready_for_enqueue"
  assert by_tar[tar_path]["mutable_tar"] is True
  assert "mutable_tar_present" in by_tar[tar_path]["reasons"]


def test_log_day_close_candidate_report_date_order_across_statuses(
    capsys, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      log_day_close_candidate_report,
  )

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod

  monkeypatch.setattr(cfg_mod, "get_sync_day_close_candidate_report", lambda: True)
  log_day_close_candidate_report(
      [
          {
              "tar_path": "/arch/2026-06-07.tar",
              "status": "waiting_on_ingest",
              "reasons": ["checkpoint_incomplete"],
              "unprocessed": 10,
              "phase": "",
              "mutable_tar": True,
          },
          {
              "tar_path": "/arch/2026-05-26.tar",
              "status": "queued",
              "reasons": ["day_close_in_progress"],
              "unprocessed": 0,
              "phase": "",
              "mutable_tar": True,
          },
          {
              "tar_path": "/arch/2026-05-31.tar",
              "status": "disqualified",
              "reasons": ["inflight_append_path"],
              "unprocessed": 0,
              "phase": "",
              "mutable_tar": True,
          },
          {
              "tar_path": "/arch/2026-05-22.tar",
              "status": "queued",
              "reasons": ["day_close_in_progress"],
              "unprocessed": 0,
              "phase": "",
              "mutable_tar": True,
          },
      ],
      reason="test",
  )
  out = capsys.readouterr().out
  lines = [
      line for line in out.splitlines()
      if "day_close candidate tar=" in line
  ]
  assert len(lines) == 4
  assert "2026-05-22" in lines[0]
  assert "2026-05-26" in lines[1]
  assert "2026-05-31" in lines[2]
  assert "2026-06-07" in lines[3]
  assert "queue_order=1" in lines[0]
  assert "queue_order=2" in lines[1]
  assert "queue_order=" in lines[2] and "queue_order=1" not in lines[2]
  assert "queue_order=" in lines[3] and "queue_order=1" not in lines[3]
  assert "mutable_tar=yes" in lines[0]
  assert "mutable_tar_n=4" in out


def test_classify_reports_aligned_unprocessed_and_cross_day_n(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-27.tar"))
  open(tar_path, "wb").close()
  d_may = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
  d_july = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
  aligned = tmp_path / "aligned"
  cross = tmp_path / "cross"
  aligned.write_text("x")
  cross.write_text("x")
  os.utime(aligned, (d_may.timestamp(), d_may.timestamp()))
  os.utime(cross, (d_july.timestamp(), d_july.timestamp()))
  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      unprocessed_by_tar={tar_path: [str(aligned), str(cross)]},
      disqualification_reasons={},
      local_tz=timezone.utc,
  )
  by_tar = {e["tar_path"]: e for e in entries}
  assert by_tar[tar_path]["status"] == "waiting_on_ingest"
  assert by_tar[tar_path]["unprocessed"] == 1
  assert by_tar[tar_path]["unprocessed_cross_day_n"] == 1


def test_cross_day_remaining_raw_does_not_block_filesystem_complete(tmp_path):
  """Filename-day misbucket under May-28 must not keep fs_complete false."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      day_close_filesystem_complete,
      filter_remaining_raw_aligned_to_tar,
      remaining_raw_on_disk_counts_for_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-28.tar"))
  zst_path = os.path.normpath(str(daily_dir / "2026-05-28.tar.zst"))
  open(zst_path, "wb").write(b"sealed")
  d_may = datetime(2026, 5, 28, 12, tzinfo=timezone.utc)
  d_july = datetime(2026, 7, 6, 12, tzinfo=timezone.utc)
  aligned = tmp_path / str(int(d_may.timestamp()))
  cross = tmp_path / str(int(d_july.timestamp()))
  aligned.write_text("x")
  cross.write_text("x")
  remaining = {zst_path: [str(aligned), str(cross)]}
  filtered = filter_remaining_raw_aligned_to_tar(
      remaining,
      tar_path,
      tgz_archive_dir=str(daily_dir),
  )
  assert filtered[zst_path] == [str(aligned)]
  aligned_n, cross_n = remaining_raw_on_disk_counts_for_tar(
      remaining,
      tar_path,
      tgz_archive_dir=str(daily_dir),
  )
  assert aligned_n == 1
  assert cross_n == 1
  assert day_close_filesystem_complete(
      tar_path,
      remaining_raw_by_gz={zst_path: [str(cross)]},
      use_blocking_remaining=False,
      tgz_archive_dir=str(daily_dir),
  ) is True
  assert day_close_filesystem_complete(
      tar_path,
      remaining_raw_by_gz={zst_path: [str(aligned)]},
      use_blocking_remaining=False,
      tgz_archive_dir=str(daily_dir),
  ) is False


def test_classify_reports_processed_but_on_disk_and_cross_day(tmp_path, monkeypatch):
  import hpcperfstats.dbload.lib.conf_parser as cfg_mod
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
      log_day_close_candidate_report,
  )

  monkeypatch.setattr(cfg_mod, "get_sync_day_close_candidate_report", lambda: True)
  # Avoid live archive-dir scan from blocking map during needs_work.
  monkeypatch.setattr(
      helpers,
      "day_close_filesystem_complete",
      lambda *_a, **_k: True,
  )
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-28.tar"))
  zst_path = os.path.normpath(str(daily_dir / "2026-05-28.tar.zst"))
  open(zst_path, "wb").write(b"sealed")
  d_may = datetime(2026, 5, 28, 12, tzinfo=timezone.utc)
  d_july = datetime(2026, 7, 6, 12, tzinfo=timezone.utc)
  aligned = tmp_path / str(int(d_may.timestamp()))
  cross = tmp_path / str(int(d_july.timestamp()))
  aligned.write_text("x")
  cross.write_text("x")
  remaining = {zst_path: [str(aligned), str(cross)]}
  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      remaining_raw_by_gz=remaining,
      unprocessed_by_tar={},
      disqualification_reasons={},
      local_tz=timezone.utc,
  )
  by_tar = {e["tar_path"]: e for e in entries}
  assert by_tar[tar_path]["processed_but_on_disk"] == 1
  assert by_tar[tar_path]["processed_cross_day_n"] == 1
  logs = []
  log_day_close_candidate_report(
      [
          {
              **by_tar[tar_path],
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
          }
      ],
      reason="test",
      log_fn=lambda msg, flush=False: logs.append(msg),
  )
  assert any("processed_but_on_disk=1" in line for line in logs)
  assert any("processed_cross_day_n=1" in line for line in logs)


def test_blocking_tar_drop_excludes_cross_day_filename(tmp_path, monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_day_raw_removal as drm

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-28.tar"))
  zst_path = os.path.normpath(str(daily_dir / "2026-05-28.tar.zst"))
  open(zst_path, "wb").write(b"sealed")
  d_may = datetime(2026, 5, 28, 12, tzinfo=timezone.utc)
  d_july = datetime(2026, 7, 6, 12, tzinfo=timezone.utc)
  aligned = tmp_path / str(int(d_may.timestamp()))
  cross = tmp_path / str(int(d_july.timestamp()))
  aligned.write_text("x")
  cross.write_text("x")
  monkeypatch.setattr(
      drm,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {zst_path: [str(aligned), str(cross)]},
  )
  blocking = drm.remaining_raw_by_gz_blocking_tar_drop(
      tar_path=tar_path,
      archive_data_dir=str(tmp_path),
      host_name_ext="example.edu",
      tgz_archive_dir=str(daily_dir),
      get_quarantine_skip_paths=lambda: set(),
  )
  paths = [p for ps in blocking.values() for p in ps]
  assert str(aligned) in paths
  assert str(cross) not in paths


def test_cap_merges_all_unprocessed_days_into_pending(tmp_path):
  """Cap input missing May-30 paths but unprocessed map has them → head includes May-30."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      all_on_disk_unprocessed_paths,
      prepend_checkpoint_incomplete_paths_to_pending,
      sort_pending_stats_paths_oldest_first,
      cap_pending_stats_with_blocked_retention,
      oldest_checkpoint_incomplete_tar,
      aligned_on_disk_unprocessed_paths_for_tar,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  may27 = os.path.normpath(str(daily_dir / "2026-05-27.tar"))
  may30 = os.path.normpath(str(daily_dir / "2026-05-30.tar"))
  open(may27, "wb").close()
  open(may30, "wb").close()
  d_july = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
  d_may30 = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
  d_june = datetime(2026, 6, 7, 12, tzinfo=timezone.utc)
  cross = tmp_path / "cross_july"
  may30_path = tmp_path / "may30_seg"
  june_path = tmp_path / "june_seg"
  for path, day in (
      (cross, d_july),
      (may30_path, d_may30),
      (june_path, d_june),
  ):
    path.write_text("x")
    os.utime(path, (day.timestamp(), day.timestamp()))
  unprocessed = {
      may27: [str(cross)],
      may30: [str(may30_path)],
  }
  # Pending only has June (scan gap); map still has May-30.
  paths = [str(june_path)]
  all_unprocessed = sort_pending_stats_paths_oldest_first(
      all_on_disk_unprocessed_paths(unprocessed),
  )
  tar_norm = oldest_checkpoint_incomplete_tar(
      unprocessed, tgz_archive_dir=str(daily_dir))
  assert tar_norm == may30
  reserved = aligned_on_disk_unprocessed_paths_for_tar(
      unprocessed, tar_norm, tgz_archive_dir=str(daily_dir))
  capped = cap_pending_stats_with_blocked_retention(
      prepend_checkpoint_incomplete_paths_to_pending(paths, all_unprocessed),
      max_size=2000,
      blocked_paths=reserved,
      handoff_priority_paths=[],
      log_fn=lambda *_a, **_k: None,
  )
  assert str(may30_path) in capped
  assert capped[0] == str(may30_path)


def test_supplement_at_max_replaces_with_older_closed_paths(tmp_path):
  """Full queue of June paths + older closed_paths → head is older."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      supplement_pending_paths_from_closed_paths,
  )

  host_dir = tmp_path / "host.cluster.test"
  host_dir.mkdir(parents=True)
  d_may = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
  d_june = datetime(2026, 6, 7, 12, tzinfo=timezone.utc)
  may_paths = []
  june_paths = []
  for index in range(5):
    may_path = host_dir / ("may_%d" % index)
    june_path = host_dir / ("june_%d" % index)
    may_path.write_text("1\n", encoding="utf-8")
    june_path.write_text("1\n", encoding="utf-8")
    os.utime(may_path, (d_may.timestamp() + index, d_may.timestamp() + index))
    os.utime(june_path, (d_june.timestamp() + index, d_june.timestamp() + index))
    may_paths.append(str(may_path))
    june_paths.append(str(june_path))
  # Use numeric basenames so oldest-first sort uses epochs.
  may_epoch = int(d_may.timestamp())
  june_epoch = int(d_june.timestamp())
  may_named = []
  june_named = []
  for index in range(5):
    may_p = host_dir / str(may_epoch + index)
    june_p = host_dir / str(june_epoch + index)
    may_p.write_text("1\n", encoding="utf-8")
    june_p.write_text("1\n", encoding="utf-8")
    may_named.append(str(may_p))
    june_named.append(str(june_p))
  logs = []
  capped = supplement_pending_paths_from_closed_paths(
      june_named,
      closed_paths=may_named + june_named,
      max_size=5,
      processed_exclude=set(),
      log_fn=lambda msg, **_k: logs.append(str(msg)),
  )
  assert len(capped) == 5
  assert capped[0] == may_named[0]
  assert all(path in may_named for path in capped)
  assert any("pending cap supplement replace" in line for line in logs)


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_atomic_seal_refuses_unreadable_tar(tmp_path):
  """Fix A: refuse seal when tar fails full readability scan."""
  tar_path = tmp_path / "2021-06-07.tar"
  zst_path = tmp_path / "2021-06-07.tar.zst"
  tar_path.write_bytes(b"not-a-valid-tar-archive")
  result = atomic_seal_tar_to_zst(
      str(tar_path),
      str(zst_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert result is None
  assert not zst_path.is_file()


def test_verify_tar_read_lock_timeout_not_treated_as_corrupt(monkeypatch, tmp_path):
  """Fix I: fnctl read lock timeout must propagate, not return False."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2021-06-08.tar"
  member = tmp_path / "payload.txt"
  member.write_text("ok")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="host/payload")

  def _timeout_lock(_path):
    raise TimeoutError("timed out waiting for fnctl.lock on %s" % _path)

  monkeypatch.setattr(helpers, "file_read_lock_wait", _timeout_lock)
  with pytest.raises(TimeoutError, match="fnctl.lock"):
    helpers.verify_tar_archive_readable(str(tar_path))


def test_decompress_restore_keeps_zst_on_active_ingest_day(monkeypatch, tmp_path):
  """Restore keeps sealed sibling (day-close owns unlink after blocking empty)."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2024-06-07.tar"
  zst_path = tmp_path / "2024-06-07.tar.zst"
  zst_path.write_bytes(b"placeholder-zst")
  captured = {}

  def _fake_decompress(src, dst, zstd_threads, remove_compressed=True):
    captured["remove_compressed"] = remove_compressed
    with tarfile.open(dst, "w") as tf:
      tf.addfile(tarfile.TarInfo(name="host/raw"), io.BytesIO(b"x"))
    return True

  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _fake_decompress)
  assert helpers.ensure_daily_tar_restored_for_append(str(tar_path), 1) is True
  assert captured.get("remove_compressed") is False
  assert zst_path.is_file()


def test_ensure_restore_does_not_build_full_maint_snapshot(monkeypatch, tmp_path):
  """Gated restore must reach decompress without full remaining-raw census."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2026-06-02.tar"
  zst_path = tmp_path / "2026-06-02.tar.zst"
  zst_path.write_bytes(b"placeholder-zst")
  snapshot_calls = []
  decomp_calls = []

  def _boom(*_a, **_k):
    snapshot_calls.append(1)
    raise AssertionError("must not build full maint snapshot on restore")

  def _fake_decompress(src, dst, zstd_threads, remove_compressed=True):
    decomp_calls.append((src, remove_compressed))
    with tarfile.open(dst, "w") as tf:
      tf.addfile(tarfile.TarInfo(name="host/raw"), io.BytesIO(b"x"))
    return True

  monkeypatch.setattr(helpers, "build_remaining_raw_for_daily_tar", _boom)
  monkeypatch.setattr(helpers, "build_remaining_raw_stats_by_daily_gz", _boom)
  monkeypatch.setattr(helpers, "decompress_compressed_to_tar", _fake_decompress)

  assert helpers.ensure_daily_tar_restored_for_append(str(tar_path), 1) is True
  assert snapshot_calls == []
  assert len(decomp_calls) == 1
  assert decomp_calls[0][1] is False


def test_needs_work_false_when_filesystem_complete_without_phase(
    monkeypatch, tmp_path,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2026-06-04.tar"
  zst_path = tmp_path / "2026-06-04.tar.zst"
  zst_path.write_bytes(b"sealed")
  monkeypatch.setattr(
      helpers,
      "day_close_filesystem_complete",
      lambda *_a, **_k: True,
  )
  assert helpers.daily_tar_needs_day_close_work(
      str(tar_path),
      day_phases={},
      remaining_raw_by_gz={},
  ) is False


def test_populate_uses_tar_when_sealed_missing_even_if_not_dirty_mtime(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """Fix B: missing sealed sibling must populate from mutable tar."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_tar = tmp_path / "2024-06-09.tar"
  inner = tmp_path / "member.txt"
  inner.write_text("payload")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="host/member")
  canonical = str(tmp_path / "2024-06-09.tar.zst")

  sealed_calls = {"n": 0}
  tar_calls = {"n": 0}

  def _forbidden_sealed(*_a, **_k):
    sealed_calls["n"] += 1
    raise AssertionError("sealed missing; must not scan sealed")

  def _counting_tar(*_a, **_k):
    tar_calls["n"] += 1
    return {"host/member": inner.stat().st_size}

  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_sealed_scan", _forbidden_sealed,
  )
  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_tar_scan", _counting_tar,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: FakeRedis(),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".redis_lookup_full_members",
      lambda keys: None,
  )

  token = set_worker_pool_kind("populate-pool")
  try:
    members = helpers.get_existing_archive_members_for_daily_archive(canonical)
  finally:
    reset_worker_pool_kind(token)
  assert tar_calls["n"] == 1
  assert sealed_calls["n"] == 0
  assert members.get("host/member") == inner.stat().st_size


def test_populate_uses_tar_for_active_ingest_day(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """Fix B: active-ingest day must prefer tar even when sealed mtime is current."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
      FakeRedis,
  )

  day_zst = tmp_path / "2024-06-10.tar.zst"
  day_tar = tmp_path / "2024-06-10.tar"
  inner = tmp_path / "member.txt"
  inner.write_text("payload")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="host/member")
  day_zst.write_bytes(b"sealed-placeholder")
  os.utime(day_tar, (1000, 1000))
  os.utime(day_zst, (2000, 2000))

  sealed_calls = {"n": 0}
  tar_calls = {"n": 0}

  def _forbidden_sealed(*_a, **_k):
    sealed_calls["n"] += 1
    raise AssertionError("active ingest must use tar populate")

  def _counting_tar(*_a, **_k):
    tar_calls["n"] += 1
    return {"host/member": inner.stat().st_size}

  monkeypatch.setattr(
      helpers, "remaining_raw_by_gz_has_paths_on_disk", lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_sealed_scan", _forbidden_sealed,
  )
  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_tar_scan", _counting_tar,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_cache_enabled", lambda: True,
  )
  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: FakeRedis(),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".redis_lookup_full_members",
      lambda keys: None,
  )

  token = set_worker_pool_kind("populate-pool")
  try:
    members = helpers.get_existing_archive_members_for_daily_archive(str(day_zst))
  finally:
    reset_worker_pool_kind(token)
  assert tar_calls["n"] == 1
  assert sealed_calls["n"] == 0
  assert members.get("host/member") == inner.stat().st_size


def test_classify_on_disk_equals_unprocessed_plus_processed(tmp_path, monkeypatch):
  """Three-counter invariant: on_disk == unprocessed + processed_but_on_disk."""
  import hpcperfstats.dbload.lib.conf_parser as cfg_mod
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      classify_day_close_candidates,
      log_day_close_candidate_report,
  )

  monkeypatch.setattr(cfg_mod, "get_sync_day_close_candidate_report", lambda: True)
  monkeypatch.setattr(
      helpers,
      "day_close_filesystem_complete",
      lambda *_a, **_k: False,
  )
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-28.tar"))
  open(tar_path, "wb").close()
  zst_path = os.path.normpath(str(daily_dir / "2026-05-28.tar.zst"))
  open(zst_path, "wb").write(b"sealed")
  d_may = datetime(2026, 5, 28, 12, tzinfo=timezone.utc)
  unproc = tmp_path / str(int(d_may.timestamp()))
  leftover = tmp_path / str(int(d_may.timestamp()) + 1)
  unproc.write_text("u")
  leftover.write_text("p")
  remaining = {zst_path: [str(unproc), str(leftover)]}
  unprocessed_by_tar = {tar_path: [str(unproc)]}
  entries = classify_day_close_candidates(
      tgz_archive_dir=str(daily_dir),
      remaining_raw_by_gz=remaining,
      unprocessed_by_tar=unprocessed_by_tar,
      disqualification_reasons={},
      local_tz=timezone.utc,
  )
  by_tar = {e["tar_path"]: e for e in entries}
  entry = by_tar[tar_path]
  assert entry["unprocessed"] == 1
  assert entry["processed_but_on_disk"] == 1
  assert entry["on_disk"] == entry["unprocessed"] + entry["processed_but_on_disk"]
  logs = []
  log_day_close_candidate_report(
      [entry],
      reason="test",
      log_fn=lambda msg, flush=False: logs.append(msg),
  )
  assert any("on_disk=2" in line for line in logs)
  assert any("unprocessed=1" in line for line in logs)
  assert any("processed_but_on_disk=1" in line for line in logs)


def test_ingest_worker_never_streams_sealed_when_populate_pool_down(
    monkeypatch, tmp_path, _clear_daily_archive_members_cache,
):
  """Ingest-pool must enqueue (not stream) when controller is None (spawn reality)."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
      _POPULATE_QUEUE_KEY,
      request_archive_members_populate_and_wait,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  fake = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_populate_pool.get_populate_pool_controller",
      lambda: None,
  )
  stream_calls = {"n": 0}
  original_stream = helpers._stream_compressed_archive_members

  def _counting_stream(*args, **kwargs):
    stream_calls["n"] += 1
    return original_stream(*args, **kwargs)

  monkeypatch.setattr(helpers, "_stream_compressed_archive_members", _counting_stream)
  members = {"host/raw": 4}

  def _fake_wait(*_a, **_k):
    return dict(members)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.wait_for_complete_members",
      _fake_wait,
  )
  token = set_worker_pool_kind("ingest-pool")
  try:
    got = request_archive_members_populate_and_wait(str(day_gz))
    assert got == members
    with pytest.raises(ArchiveMembersRedisUnavailableError, match="forbidden on ingest-pool"):
      helpers.execute_archive_members_populate_for_canonical(str(day_gz))
  finally:
    reset_worker_pool_kind(token)
  assert stream_calls["n"] == 0
  assert _POPULATE_QUEUE_KEY in fake._lists
  assert len(fake._lists[_POPULATE_QUEUE_KEY]) == 1
