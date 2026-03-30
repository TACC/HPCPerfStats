"""Unit tests for sync_timedb archive helpers and main-block helpers (no Django)."""
import os
import subprocess
import tarfile
from datetime import datetime, timedelta

import pytest

from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    build_archive_mapping,
    collect_stats_files_in_range,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    get_stats_chunk,
    get_tar_file_tasks,
    get_tar_member_name,
    get_verified_files_to_remove,
    rescan_pending_stats_files,
    stats_file_is_active_segment,
)
from hpcperfstats.dbload.sync_timedb_parsing import parse_first_timestamp_line
from hpcperfstats.file_locking import LOCK_SUFFIX


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


# --- get_existing_archive_members ---


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


def test_get_tar_file_tasks_empty_tar(tmp_path):
  """Empty tar returns empty list."""
  tar_path = tmp_path / "empty.tar"
  with tarfile.open(tar_path, "w"):
    pass
  assert get_tar_file_tasks(str(tar_path)) == []


def test_get_tar_file_tasks_restores_corrupt_tar_from_gz(monkeypatch, tmp_path):
  """Corrupt tar with sibling .gz is restored via pigz and retried once."""
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
      helpers.subprocess,
      "run",
      lambda cmd, capture_output, text, check: pigz_calls.append(cmd) or subprocess.CompletedProcess(
          cmd, 0, stdout="", stderr=""
      ),
  )

  assert get_tar_file_tasks(tar_path) == [(tar_path, "a.txt")]
  assert open_calls["count"] == 2
  assert remove_calls == [tar_path]
  assert pigz_calls == [[
      "/usr/bin/pigz", "-v", "-d", "-p", str(helpers.pigz_thread_count), gz_path
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
      helpers.subprocess,
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
