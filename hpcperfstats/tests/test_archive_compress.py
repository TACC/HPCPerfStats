"""Unit tests for archive_compress path helpers (no Django)."""
from __future__ import annotations

from datetime import date

import pytest

from hpcperfstats.dbload.lib.archive_compress import (
    DAILY_ARCHIVE_GZ_SUFFIX,
    DAILY_ARCHIVE_TAR_SUFFIX,
    DAILY_ARCHIVE_ZST_SUFFIX,
    archive_gz_members_contained_in_zst,
    archive_member_maps_equivalent,
    compressed_sibling_paths,
    daily_compressed_path_for_date,
    daily_tar_path_from_compressed,
    detect_compressed_format,
    normalize_daily_compressed_path,
    sum_member_bytes,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/archive/2024-01-02.tar.zst", "zst"),
        ("/archive/2024-01-02.tar.gz", "gz"),
        ("/archive/2024-01-02.tar", None),
        ("/archive/foo.txt", None),
    ],
)
def test_detect_compressed_format(path, expected):
  assert detect_compressed_format(path) == expected


@pytest.mark.parametrize(
    ("path", "expected_tar"),
    [
        ("/a/2024-01-02.tar.zst", "/a/2024-01-02.tar"),
        ("/a/2024-01-02.tar.gz", "/a/2024-01-02.tar"),
        ("/a/2024-01-02.tar", "/a/2024-01-02.tar"),
        ("/a/other", "/a/other"),
    ],
)
def test_daily_tar_path_from_compressed(path, expected_tar):
  assert daily_tar_path_from_compressed(path) == expected_tar


def test_compressed_sibling_paths_from_tar():
  zst, gz = compressed_sibling_paths("/data/2024-06-01.tar")
  assert zst == "/data/2024-06-01.tar.zst"
  assert gz == "/data/2024-06-01.tar.gz"


def test_compressed_sibling_paths_without_tar_suffix():
  zst, gz = compressed_sibling_paths("/data/2024-06-01")
  assert zst == "/data/2024-06-01.tar.zst"
  assert gz == "/data/2024-06-01.tar.gz"


def test_daily_compressed_path_for_date():
  path = daily_compressed_path_for_date("/archive", date(2024, 3, 15))
  assert path == "/archive/2024-03-15.tar.zst"


def test_archive_member_maps_equivalent():
  m = {"a.txt": 10, "b.txt": 20}
  assert archive_member_maps_equivalent(m, dict(m))
  assert not archive_member_maps_equivalent(m, {"a.txt": 10})


def test_archive_gz_members_contained_in_zst_allows_extra_zst_files():
  gz_members = {"a.txt": 10}
  zst_members = {"a.txt": 10, "b.txt": 99}
  assert archive_gz_members_contained_in_zst(gz_members, zst_members)


def test_archive_gz_members_contained_in_zst_rejects_missing_or_wrong_size():
  gz_members = {"a.txt": 10}
  assert not archive_gz_members_contained_in_zst(gz_members, {})
  assert not archive_gz_members_contained_in_zst(gz_members, {"a.txt": 11})


def test_sum_member_bytes():
  assert sum_member_bytes({"a": 3, "b": 7}) == 10


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/a/2024-01-02.tar.zst", "/a/2024-01-02.tar.zst"),
        ("/a/2024-01-02.tar.gz", "/a/2024-01-02.tar.zst"),
        ("/a/2024-01-02.tar", "/a/2024-01-02.tar.zst"),
        ("/a/other", "/a/other"),
    ],
)
def test_normalize_daily_compressed_path(path, expected):
  assert normalize_daily_compressed_path(path) == expected


def test_suffix_constants():
  assert DAILY_ARCHIVE_ZST_SUFFIX == ".tar.zst"
  assert DAILY_ARCHIVE_GZ_SUFFIX == ".tar.gz"
  assert DAILY_ARCHIVE_TAR_SUFFIX == ".tar"
