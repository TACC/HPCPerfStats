"""Unit tests for sync_timedb archive helpers and main-block helpers (no Django)."""
import os
import tarfile
from datetime import datetime, timedelta

import pytest

from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    build_archive_mapping,
    collect_stats_files_in_range,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    get_stats_chunk,
    get_tar_member_name,
    get_verified_files_to_remove,
)


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


def test_collect_stats_files_in_range_no_subdirs(tmp_path):
  """Directory with no c* or v* subdirs returns empty list."""
  (tmp_path / "other").mkdir()
  (tmp_path / "other" / "file").write_text("x")
  assert collect_stats_files_in_range(
      str(tmp_path),
      datetime(2020, 1, 1),
      datetime(2020, 1, 10)) == []


def test_collect_stats_files_in_range_skips_current(tmp_path):
  """Files named 'current*' are skipped."""
  cn = tmp_path / "cn001"
  cn.mkdir()
  (cn / "current").write_text("x")
  (cn / "12345").write_text("y")
  # Set mtime so 12345 is in range
  t = datetime(2020, 6, 15, 12, 0, 0).timestamp()
  os.utime(cn / "12345", (t, t))
  os.utime(cn / "current", (t, t))
  start = datetime(2020, 6, 1)
  end = datetime(2020, 7, 1)
  result = collect_stats_files_in_range(str(tmp_path), start, end)
  assert len(result) == 1
  assert result[0].endswith("12345")


def test_collect_stats_files_in_range_date_filter(tmp_path):
  """Files outside date range are excluded."""
  cn = tmp_path / "cn001"
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
  result = collect_stats_files_in_range(str(tmp_path), start, end)
  assert len(result) == 1
  assert result[0].endswith("2")


def test_collect_stats_files_in_range_sorted_by_basename(tmp_path):
  """Results are sorted by basename."""
  cn = tmp_path / "cn001"
  cn.mkdir()
  for name in ["30", "10", "20"]:
    p = cn / name
    p.write_text("x")
    os.utime(p, (datetime(2020, 6, 15).timestamp(),) * 2)
  start = datetime(2020, 6, 1)
  end = datetime(2020, 7, 1)
  result = collect_stats_files_in_range(str(tmp_path), start, end)
  basenames = [os.path.basename(p) for p in result]
  assert basenames == ["10", "20", "30"]


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


def test_build_archive_mapping_skips_today(tmp_path):
  """Files with timestamp today are skipped (not archived)."""
  tgz_dir = tmp_path / "tgz"
  tgz_dir.mkdir()
  f1 = tmp_path / "f1"
  today_ts = datetime.today().replace(hour=12, minute=0, second=0).timestamp()
  f1.write_text("%d job1 cn001\n" % today_ts)
  mapping = build_archive_mapping([str(f1)], str(tgz_dir))
  assert mapping == {}
