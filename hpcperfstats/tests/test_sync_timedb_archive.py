"""Unit tests for sync_timedb archive helpers and main-block helpers (no Django)."""
import io
import os
import shutil
import subprocess
import tarfile
import time
from datetime import date, datetime, timedelta

import pytest

from hpcperfstats.dbload.pigz_cli import pigz_executable
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_gz,
    build_archive_mapping,
    collect_lock_sidecar_stats,
    collect_stats_files_in_range,
    dedupe_tar_keep_largest_file_per_member,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    get_existing_archive_members_for_daily_archive,
    get_file_member_sizes_from_gzip_archive,
    get_stats_chunk,
    get_tar_file_tasks,
    iter_tar_file_tasks,
    get_tar_member_name,
    get_verified_files_to_remove,
    collect_first_timestamps_by_path,
    is_daily_tar_gz_dirty,
    parse_archive_date_from_daily_tar_path,
    remove_verified_archived_raw_files,
    remove_verified_uncompressed_daily_tars,
    replace_corrupt_tar_from_gzip_backup,
    rescan_pending_stats_files,
    resolve_preferred_archive_path_for_read,
    should_seal_daily_tar,
    stats_file_is_active_segment,
    validate_sealed_daily_archive_for_raw_removal,
    tar_has_duplicate_file_members,
    verify_tar_archive_readable,
)
from hpcperfstats.dbload.sync_timedb_archive import _iter_tar_tasks_chunked
import hpcperfstats.dbload.sync_timedb_archive as sta
from hpcperfstats.dbload.sync_timedb import archive_stats_files
from hpcperfstats.dbload.sync_timedb_parsing import parse_first_timestamp_line
from hpcperfstats.file_locking import LOCK_SUFFIX
from hpcperfstats.shutdown_utils import shutdown_requested


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


# --- verify_tar_archive_readable / replace_corrupt_tar_from_gzip_backup ---


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


@pytest.mark.skipif(not shutil.which("pigz"), reason="pigz not on PATH")
@pytest.mark.skipif(not shutil.which("tar"), reason="tar not on PATH")
def test_verify_tar_archive_readable_accepts_valid_tgz_via_pigz_pipe(tmp_path):
  gz = tmp_path / "ok.tar.gz"
  inner = tmp_path / "inner.txt"
  inner.write_text("hello")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(inner), arcname="inner.txt")
  assert verify_tar_archive_readable(str(gz))


def test_verify_tar_gz_pigz_pipe_uses_helpers_pigz_thread_count(monkeypatch, tmp_path):
  """Regression: pigz -p matches ``sync_timedb_archive_helpers.pigz_thread_count``."""
  if not shutil.which("pigz") or not shutil.which("tar"):
    pytest.skip("need pigz and tar on PATH")
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

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

  monkeypatch.setattr(helpers.subprocess, "Popen", _wrap_popen)
  monkeypatch.setattr(helpers, "pigz_thread_count", 13)
  assert verify_tar_archive_readable(str(gz))
  pigz_cmds = [c for c in recorded if len(c) >= 4 and c[1] == "-d" and c[2] == "-c"]
  assert pigz_cmds, "expected pigz -d -c subprocess"
  assert "-p" in pigz_cmds[0]
  assert pigz_cmds[0][pigz_cmds[0].index("-p") + 1] == "13"


def test_get_file_member_sizes_from_gzip_uses_pigz_pipe_when_pigz_present(
    monkeypatch, tmp_path,
):
  if not shutil.which("pigz"):
    pytest.skip("pigz not on PATH")
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

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
  monkeypatch.setattr(helpers, "pigz_thread_count", 5)
  assert get_file_member_sizes_from_gzip_archive(str(gz)) == {"only": 3}
  pigz_cmds = [c for c in recorded if len(c) >= 4 and c[1] == "-d" and c[2] == "-c"]
  assert pigz_cmds[0][pigz_cmds[0].index("-p") + 1] == "5"


def test_replace_corrupt_tar_from_gzip_backup_without_gz_removes_tar(tmp_path):
  tar_path = tmp_path / "2020-01-01.tar"
  tar_path.write_text("corrupt")
  assert replace_corrupt_tar_from_gzip_backup(
      str(tar_path), str(tmp_path / "2020-01-01.tar.gz"), 1)
  assert not tar_path.exists()


def test_replace_corrupt_tar_from_gzip_backup_restores_via_pigz(monkeypatch, tmp_path):
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2020-01-02.tar"
  gz_path = tmp_path / "2020-01-02.tar.gz"
  tar_path.write_text("bad")
  gz_path.write_text("not-used")

  def _fake_decomp(gz, threads):
    assert gz == str(gz_path)
    inner = tmp_path / "inn.txt"
    inner.write_text("ok")
    with tarfile.open(str(tar_path), "w") as tf:
      tf.add(str(inner), arcname="only.txt")

  monkeypatch.setattr(helpers, "pigz_decompress_verbose", _fake_decomp)
  assert replace_corrupt_tar_from_gzip_backup(
      str(tar_path), str(gz_path), 1)
  assert verify_tar_archive_readable(str(tar_path))


# --- parse_archive_date_from_daily_tar_path ---


def test_parse_archive_date_from_daily_tar_path_ok():
  assert parse_archive_date_from_daily_tar_path("/x/2020-06-15.tar") == date(2020, 6, 15)


def test_parse_archive_date_from_daily_tar_path_rejects_non_daily_name():
  assert parse_archive_date_from_daily_tar_path("/x/other.tar") is None


# --- is_daily_tar_gz_dirty / should_seal_daily_tar ---


def test_is_daily_tar_gz_dirty_missing_gz(tmp_path):
  tar_p = tmp_path / "2020-01-01.tar"
  tar_p.write_text("x")
  assert is_daily_tar_gz_dirty(str(tar_p), str(tmp_path / "2020-01-01.tar.gz"))


def test_is_daily_tar_gz_dirty_tar_newer(tmp_path):
  tar_p = tmp_path / "2020-01-01.tar"
  gz_p = tmp_path / "2020-01-01.tar.gz"
  tar_p.write_text("x")
  gz_p.write_text("y")
  old = datetime(2010, 1, 1).timestamp()
  os.utime(gz_p, (old, old))
  newer = datetime(2011, 1, 1).timestamp()
  os.utime(tar_p, (newer, newer))
  assert is_daily_tar_gz_dirty(str(tar_p), str(gz_p))


def test_should_seal_prior_calendar_day_without_waiting_idle(tmp_path, monkeypatch):
  """Dirty archive for a date before *today* seals even if idle_seconds is huge."""
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb_archive_helpers.time.time",
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
      "hpcperfstats.dbload.sync_timedb_archive_helpers.time.time",
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
      "hpcperfstats.dbload.sync_timedb_archive_helpers.time.time",
      lambda: base + 120,
  )
  assert should_seal_daily_tar(
      str(tar_p), str(gz_p), idle_seconds=60, today_local_date=today)


def test_should_seal_immediately_if_dirty_bypasses_idle(monkeypatch, tmp_path):
  base = 1_700_000_000
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb_archive_helpers.time.time",
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


@pytest.mark.skipif(not shutil.which("pigz"), reason="pigz not on PATH")
def test_atomic_seal_tar_to_gz_creates_valid_gzip(tmp_path):
  """Integration: temp gzip + pigz -t + replace; keeps .tar when requested."""
  tar_path = tmp_path / "2021-03-01.tar"
  gz_path = tmp_path / "2021-03-01.tar.gz"
  member = tmp_path / "a.txt"
  member.write_text("hello")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="a.txt")
  atomic_seal_tar_to_gz(
      str(tar_path),
      str(gz_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert gz_path.is_file()
  subprocess.run([pigz_executable(), "-t", "-p", "1", str(gz_path)], check=True)
  assert tar_path.is_file()
  assert not (tmp_path / "2021-03-01.tar.gz.tmp").exists()


@pytest.mark.skipif(not shutil.which("pigz"), reason="pigz not on PATH")
def test_atomic_seal_tar_to_gz_can_drop_uncompressed(tmp_path):
  tar_path = tmp_path / "2021-03-02.tar"
  gz_path = tmp_path / "2021-03-02.tar.gz"
  member = tmp_path / "b.txt"
  member.write_text("x")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="b.txt")
  atomic_seal_tar_to_gz(
      str(tar_path),
      str(gz_path),
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=False,
      log_fn=None,
  )
  assert gz_path.is_file()
  assert not tar_path.exists()


def test_atomic_seal_tar_to_gz_passes_thread_count_to_test_and_compress(monkeypatch, tmp_path):
  """Both pigz compression and pigz -t validation should use the requested -p count."""
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  tar_path = tmp_path / "2021-03-04.tar"
  gz_path = tmp_path / "2021-03-04.tar.gz"
  member = tmp_path / "d.txt"
  member.write_text("x")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="d.txt")

  calls = []

  def _fake_run(cmd, stdout=None, stderr=None, text=None, check=False, capture_output=False):
    calls.append(list(cmd))
    if "-c" in cmd and stdout is not None:
      stdout.write(b"fake-gzip-bytes")
      stdout.flush()
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

  monkeypatch.setattr(helpers.subprocess, "run", _fake_run)
  monkeypatch.setattr(helpers, "pigz_executable", lambda: "/usr/bin/pigz")

  atomic_seal_tar_to_gz(
      str(tar_path),
      str(gz_path),
      num_threads=3,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )

  assert ["/usr/bin/pigz", "-c", "-6", "-p", "3", str(tar_path)] in calls
  assert ["/usr/bin/pigz", "-t", "-p", "3", str(gz_path) + ".tmp"] in calls


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


def test_iter_tar_tasks_chunked_streams_without_accumulating(monkeypatch):
  """Task chunk iterator yields bounded chunks from multiple tar inputs."""
  by_tar = {
      "a.tar": [("a.tar", "m1"), ("a.tar", "m2"), ("a.tar", "m3")],
      "b.tar": [("b.tar", "m1")],
  }
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb_archive.iter_tar_file_tasks",
      lambda tar_path: iter(by_tar.get(tar_path, [])),
  )
  chunks = list(_iter_tar_tasks_chunked(["a.tar", "b.tar"], chunk_size=2))
  assert chunks == [
      [("a.tar", "m1"), ("a.tar", "m2")],
      [("a.tar", "m3"), ("b.tar", "m1")],
  ]


def test_archive_worker_process_count_halves_sync_ingest(monkeypatch):
  """Archive multiprocessing pool uses half of the configured sync ingest worker cap."""
  monkeypatch.setattr(sta.cfg, "get_sync_ingest_pool_processes", lambda: 10)
  assert sta._archive_worker_process_count() == 5
  monkeypatch.setattr(sta.cfg, "get_sync_ingest_pool_processes", lambda: 3)
  assert sta._archive_worker_process_count() == 1
  monkeypatch.setattr(sta.cfg, "get_sync_ingest_pool_processes", lambda: 1)
  assert sta._archive_worker_process_count() == 1


def test_get_tar_file_tasks_restores_corrupt_tar_from_gz(monkeypatch, tmp_path):
  """Corrupt tar with sibling .gz is restored via pigz and retried once."""
  import hpcperfstats.dbload.pigz_cli as pigz_cli
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  tar_path = str(tmp_path / "broken.tar")
  gz_path = "%s.gz" % tar_path

  class _NoOpLock:
    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc, tb):
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

    def __exit__(self, exc_type, exc, tb):
      return False

    def getmembers(self):
      return [_Member("a.txt", True), _Member("dir", False)]

  open_calls = {"count": 0}
  remove_calls = []
  pigz_calls = []

  def _open_mock(path, mode):
    assert path == tar_path
    assert mode == "r"
    open_calls["count"] += 1
    if open_calls["count"] == 1:
      raise tarfile.ReadError("corrupt tar")
    return _FakeTar()

  monkeypatch.setattr(helpers, "file_read_lock_wait", lambda _p: _NoOpLock())
  monkeypatch.setattr(helpers.tarfile, "open", _open_mock)
  monkeypatch.setattr(helpers.os.path, "exists", lambda p: p in (tar_path, gz_path))
  monkeypatch.setattr(
      helpers.os,
      "remove",
      lambda p: remove_calls.append(p),
  )
  monkeypatch.setattr(
      pigz_cli.subprocess,
      "run",
      lambda cmd, capture_output, text, check: pigz_calls.append(cmd) or subprocess.CompletedProcess(
          cmd, 0, stdout="", stderr=""
      ),
  )

  assert get_tar_file_tasks(tar_path) == [(tar_path, "a.txt")]
  assert open_calls["count"] == 2
  assert remove_calls == [tar_path]
  assert pigz_calls == [[
      pigz_executable(), "-v", "-d", "-p", str(helpers.pigz_thread_count), gz_path
  ]]


def test_get_tar_file_tasks_raises_when_corrupt_and_no_gz(monkeypatch, tmp_path):
  """Corrupt tar without sibling .gz surfaces the read error."""
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  tar_path = str(tmp_path / "broken.tar")

  class _NoOpLock:
    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc, tb):
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


def test_get_tar_file_tasks_raises_when_pigz_restore_fails(monkeypatch, tmp_path):
  """Corrupt tar with .gz still raises when pigz restore fails."""
  import hpcperfstats.dbload.pigz_cli as pigz_cli
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  tar_path = str(tmp_path / "broken.tar")
  gz_path = "%s.gz" % tar_path

  class _NoOpLock:
    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc, tb):
      return False

  monkeypatch.setattr(helpers, "file_read_lock_wait", lambda _p: _NoOpLock())
  monkeypatch.setattr(
      helpers.tarfile,
      "open",
      lambda _path, _mode: (_ for _ in ()).throw(tarfile.ReadError("corrupt tar")),
  )
  monkeypatch.setattr(helpers.os.path, "exists", lambda p: p == gz_path or p == tar_path)
  monkeypatch.setattr(helpers.os, "remove", lambda _p: None)
  monkeypatch.setattr(
      pigz_cli.subprocess,
      "run",
      lambda cmd, capture_output, text, check: subprocess.CompletedProcess(
          cmd, 2, stdout="", stderr=""
      ),
  )

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
  tar_path = gz_key[:-len(".gz")]
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
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  arch_suffix = "cluster.integration.test"
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir()
  ts = int(datetime(2026, 4, 15, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  gz_key = str(tgz_dir / "2026-04-15.tar.gz")
  assert not os.path.isfile(gz_key)
  assert not os.path.isfile(gz_key[:-3])

  archive_calls = []

  def fake_archive(info):
    archive_calls.append(info)
    return True

  member_name = get_tar_member_name(str(seg))

  def fake_validate(gz_path, log_fn=None, validation_cache=None):
    del validation_cache
    assert gz_path == gz_key
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
  )
  assert archive_calls == [(gz_key, [str(seg)])]
  assert not seg.is_file()


def test_remove_verified_uncompressed_daily_tars_removes_when_tar_matches_gz(tmp_path):
  day_tar = tmp_path / "2026-04-22.tar"
  day_gz = tmp_path / "2026-04-22.tar.gz"
  member = tmp_path / "member.txt"
  member.write_text("same-content")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(member), arcname="member.txt")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(member), arcname="member.txt")

  remove_verified_uncompressed_daily_tars(str(tmp_path), log_fn=None)

  assert not day_tar.exists()
  assert day_gz.is_file()


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


def test_validate_sealed_daily_archive_seals_from_valid_tar_when_gz_missing(tmp_path, monkeypatch):
  gz = tmp_path / "2022-01-03.tar.gz"
  tar_p = tmp_path / "2022-01-03.tar"
  f = tmp_path / "raw.txt"
  f.write_text("seal-me")
  with tarfile.open(tar_p, "w") as tf:
    tf.add(str(f), arcname="raw.txt")

  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  seal_calls = {"count": 0}

  def _fake_atomic_seal(
      _tar_path,
      _gz_path,
      num_threads,
      compress_level,
      keep_uncompressed_tar,
      log_fn=None,
  ):
    del num_threads, compress_level, keep_uncompressed_tar, log_fn
    seal_calls["count"] += 1
    with tarfile.open(_gz_path, "w:gz") as tf:
      tf.add(str(f), arcname="raw.txt")

  monkeypatch.setattr(helpers, "atomic_seal_tar_to_gz", _fake_atomic_seal)
  ok, members = validate_sealed_daily_archive_for_raw_removal(str(gz), log_fn=None)
  assert ok
  assert seal_calls["count"] == 1
  assert members["raw.txt"] == len("seal-me")


def test_validate_sealed_daily_archive_validation_cache_hits(monkeypatch, tmp_path):
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  gz = tmp_path / "2022-02-01.tar.gz"
  f = tmp_path / "cache.txt"
  f.write_text("cache")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(f), arcname="cache.txt")

  calls = {"n": 0}
  real_scan = helpers._scan_gzip_archive_members_and_readable

  def wrapped_scan(path):
    calls["n"] += 1
    return real_scan(path)

  monkeypatch.setattr(helpers, "_scan_gzip_archive_members_and_readable", wrapped_scan)
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


def test_validate_sealed_daily_archive_validation_cache_invalidates_on_mtime_change(
    monkeypatch, tmp_path
):
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  gz = tmp_path / "2022-02-02.tar.gz"
  f = tmp_path / "stale.txt"
  f.write_text("v1")
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(f), arcname="stale.txt")

  calls = {"n": 0}
  real_scan = helpers._scan_gzip_archive_members_and_readable

  def wrapped_scan(path):
    calls["n"] += 1
    return real_scan(path)

  monkeypatch.setattr(helpers, "_scan_gzip_archive_members_and_readable", wrapped_scan)
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
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(helpers.cfg, "get_sync_archive_pool_processes", lambda: 6)
  monkeypatch.delenv("SYNC_ARCHIVE_VALIDATION_WORKERS", raising=False)
  assert helpers._get_archive_validation_worker_count(0) == 1
  assert helpers._get_archive_validation_worker_count(3) == 3
  assert helpers._get_archive_validation_worker_count(20) == 6

  monkeypatch.setenv("SYNC_ARCHIVE_VALIDATION_WORKERS", "2")
  assert helpers._get_archive_validation_worker_count(20) == 2


def test_remove_verified_archived_raw_files_streaming_apply_follows_completion_order(
    tmp_path, monkeypatch
):
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

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

  def fake_stream(_gz_paths, *, log_fn=None, validation_cache=None):
    del log_fn, validation_cache
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
  removal_logs = [l for l in logs if "removing stats file (scheduled pigz/removal):" in l]
  assert removal_logs
  assert str(seg_b) in removal_logs[0]


def test_remove_verified_uncompressed_daily_tars_streaming_apply_order(
    tmp_path, monkeypatch
):
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  tar_a = tmp_path / "2026-04-21.tar"
  tar_b = tmp_path / "2026-04-22.tar"
  tar_a.write_text("a")
  tar_b.write_text("b")
  gz_a = "%s.gz" % tar_a
  gz_b = "%s.gz" % tar_b
  monkeypatch.setattr(helpers, "iter_daily_tar_paths", lambda _d: [str(tar_a), str(tar_b)])

  def fake_stream(_gz_paths, *, log_fn=None, validation_cache=None):
    del log_fn, validation_cache
    yield gz_b, True, {"x": 1}
    yield gz_a, True, {"x": 1}

  monkeypatch.setattr(helpers, "_iter_archive_validation_results_stream", fake_stream)
  logs = []
  helpers.remove_verified_uncompressed_daily_tars(
      str(tmp_path),
      log_fn=lambda msg, flush=True: logs.append(msg),
  )
  assert not tar_a.exists()
  assert not tar_b.exists()
  removed_logs = [l for l in logs if "Final/exit maintenance removed verified uncompressed tar:" in l]
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
  tar_path = gz_key[:-len(".gz")]
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


def test_collect_stats_files_in_range_sorted_newest_first(tmp_path):
  """Results are sorted by effective timestamp, newest first."""
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
  # Expect newest (largest epoch) first.
  assert basenames == [str(epochs[2]), str(epochs[1]), str(epochs[0])]


def test_rescan_pending_stats_files_excludes_processed_and_keeps_newest_first(tmp_path):
  """Rescan excludes already processed files and keeps newest-first ordering."""
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

  assert [os.path.basename(p) for p in pending] == [str(mid_epoch), str(old_epoch)]


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


@pytest.mark.skipif(not shutil.which("pigz"), reason="pigz not on PATH")
def test_atomic_seal_tar_to_gz_logs_retention_mode(tmp_path):
  tar_path = tmp_path / "2021-03-03.tar"
  gz_path = tmp_path / "2021-03-03.tar.gz"
  member = tmp_path / "c.txt"
  member.write_text("x")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="c.txt")
  logs = []
  atomic_seal_tar_to_gz(
      str(tar_path),
      str(gz_path),
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
  assert key.endswith(".tar.gz")
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
  assert key.endswith(datetime.today().strftime("%Y-%m-%d.tar.gz"))
  assert str(f1) in mapping[key]


def test_build_archive_mapping_uses_real_sample_timestamp(monkeypatch, tmp_path):
  """build_archive_mapping should derive archive date from sample content."""
  import datetime as _real_datetime
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

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
      sample_file_date.strftime("%Y-%m-%d.tar.gz"),
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
  assert only_key.endswith(".tar.gz")
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


def test_archive_stats_files_does_not_raise_on_append_failure(monkeypatch, tmp_path):
  """Append failure keeps raw files in place and returns cleanly for retry."""
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01.tar.gz")

  import hpcperfstats.dbload.sync_timedb as st

  monkeypatch.setattr(st, "_decompress_gz", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "get_existing_archive_members", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st, "filter_files_to_add_to_archive", lambda files, *_a, **_k: list(files))

  def _boom(*_a, **_k):
    raise subprocess.CalledProcessError(2, ["tar", "-r"])

  monkeypatch.setattr(st, "_append_to_tar", _boom)

  archive_stats_files((archive_key, [str(raw_file)]))
  assert raw_file.exists()


@pytest.mark.skipif(not shutil.which("tar"), reason="tar binary required")
def test_archive_stats_files_creates_new_tar_when_missing(tmp_path):
  """Missing daily archive should be bootstrapped as a new .tar."""
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01.tar.gz")
  archive_tar = archive_key[:-3]
  assert not os.path.exists(archive_key)
  assert not os.path.exists(archive_tar)

  assert archive_stats_files((archive_key, [str(raw_file)])) is True
  assert os.path.exists(archive_tar)
  members = get_existing_archive_members(archive_tar)
  assert get_tar_member_name(str(raw_file)) in members


def test_archive_stats_files_skips_dedupe_in_append_path(monkeypatch, tmp_path):
  """Duplicate-member dedupe should run in maintenance, not per append."""
  raw_file = tmp_path / "1000"
  raw_file.write_text("1709123456 job1 cn001\n")
  archive_key = str(tmp_path / "2024-03-01.tar.gz")

  import hpcperfstats.dbload.sync_timedb as st

  monkeypatch.setattr(st, "_decompress_gz", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "get_existing_archive_members", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st, "filter_files_to_add_to_archive", lambda files, *_a, **_k: list(files))
  monkeypatch.setattr(st, "_append_to_tar", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "tar_has_duplicate_file_members", lambda *_a, **_k: True)

  dedupe_calls = {"count": 0}
  monkeypatch.setattr(
      st,
      "dedupe_tar_keep_largest_file_per_member",
      lambda *_a, **_k: dedupe_calls.__setitem__("count", dedupe_calls["count"] + 1) or True,
  )

  archive_stats_files((archive_key, [str(raw_file)]))
  assert dedupe_calls["count"] == 0


def test_process_tar_chunk_stops_when_shutdown_requested():
  """Chunk processing should stop early when shutdown flips true."""
  class _FakePool:
    def imap_unordered(self, _worker, _chunk, chunksize=1):
      del chunksize
      yield "first"
      yield "second"

  shutdown_requested[0] = False
  try:
    results = []

    def _capture(item):
      results.append(item)
      shutdown_requested[0] = True

    sta._process_tar_chunk_interruptibly(
        _FakePool(), lambda x: x, [("a", "m1"), ("a", "m2")], _capture)
    assert results == ["first"]
  finally:
    shutdown_requested[0] = False


def test_process_tar_member_task_spawn_picklable():
  """Spawn Pool workers must unpickle the worker callable (regression guard)."""
  from multiprocessing.reduction import ForkingPickler
  import multiprocessing

  ForkingPickler.dumps(sta._process_tar_member_task)
  mgr = multiprocessing.Manager()
  try:
    lock = mgr.Lock()
    ForkingPickler.dumps((lock, "/tmp/x.tar", "member"))
  finally:
    mgr.shutdown()


def test_configure_blas_thread_env_idempotent():
  """Caps BLAS/OpenMP threads before numpy (avoids pthread EAGAIN under spawn)."""
  sta._configure_blas_thread_env()
  sta._configure_blas_thread_env()
