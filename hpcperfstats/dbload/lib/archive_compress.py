"""
Daily archive path suffixes and helpers (no Django).

Attributes:
  DAILY_ARCHIVE_GZ_SUFFIX: Attribute.
  DAILY_ARCHIVE_TAR_SUFFIX: Attribute.
  DAILY_ARCHIVE_ZST_SUFFIX: Attribute.
"""
from __future__ import annotations

from typing import Any

DAILY_ARCHIVE_ZST_SUFFIX = ".tar.zst"
DAILY_ARCHIVE_GZ_SUFFIX = ".tar.gz"
DAILY_ARCHIVE_TAR_SUFFIX = ".tar"


def detect_compressed_format(path: str) -> str | None:
  """
  Detect compressed format.
  
  Args:
    path (str): String for path.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> detect_compressed_format("x")  # doctest: +SKIP
  """
  if path.endswith(DAILY_ARCHIVE_ZST_SUFFIX):
    return "zst"
  if path.endswith(DAILY_ARCHIVE_GZ_SUFFIX):
    return "gz"
  return None


def daily_tar_path_from_compressed(path: str) -> str:
  """
  Strip ``.tar.gz`` or ``.tar.zst`` to sibling ``YYYY-MM-DD.tar``.
  
  Args:
    path (str): String for path.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> daily_tar_path_from_compressed("x")  # doctest: +SKIP
  """
  if path.endswith(DAILY_ARCHIVE_ZST_SUFFIX):
    return path[: -len(DAILY_ARCHIVE_ZST_SUFFIX)] + DAILY_ARCHIVE_TAR_SUFFIX
  if path.endswith(DAILY_ARCHIVE_GZ_SUFFIX):
    return path[: -len(DAILY_ARCHIVE_GZ_SUFFIX)] + DAILY_ARCHIVE_TAR_SUFFIX
  if path.endswith(DAILY_ARCHIVE_TAR_SUFFIX):
    return path
  return path


def compressed_sibling_paths(tar_path: str) -> tuple[str, str]:
  """
  Return ``(zst_path, gz_path)`` for ``YYYY-MM-DD.tar``.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    tuple[str, str]: tuple[str, str] produced by this call.
  
  Examples:
    >>> compressed_sibling_paths("x")  # doctest: +SKIP
  """
  if tar_path.endswith(DAILY_ARCHIVE_TAR_SUFFIX):
    base = tar_path[: -len(DAILY_ARCHIVE_TAR_SUFFIX)]
  else:
    base = tar_path
  return (
      base + DAILY_ARCHIVE_ZST_SUFFIX,
      base + DAILY_ARCHIVE_GZ_SUFFIX,
  )


def daily_compressed_path_for_date(tgz_archive_dir: str, file_date: Any) -> str:
  """
  Canonical sealed path ``.../YYYY-MM-DD.tar.zst``.
  
  Args:
    tgz_archive_dir (str): String for tgz archive dir.
    file_date (Any): File date passed to this helper.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> daily_compressed_path_for_date("x", None)  # doctest: +SKIP
  """
  import os

  return os.path.join(
      tgz_archive_dir,
      file_date.strftime("%Y-%m-%d") + DAILY_ARCHIVE_ZST_SUFFIX,
  )


def archive_member_maps_equivalent(map_a: dict, map_b: dict) -> bool:
  """
  Archive the member maps equivalent.
  
  Args:
    map_a (dict): Mapping for map a.
    map_b (dict): Mapping for map b.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> archive_member_maps_equivalent({}, {})  # doctest: +SKIP
  """
  return map_a == map_b


def archive_gz_members_contained_in_zst(
  gz_members: dict,
  zst_members: dict,
) -> bool:
  """
  True when every gzip member exists in zst with the same byte size.
  
  Extra members present only in ``zst_members`` are allowed.
  
  Args:
    gz_members (dict): Mapping for gz members.
    zst_members (dict): Mapping for zst members.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> archive_gz_members_contained_in_zst({}, {})  # doctest: +SKIP
  """
  for name, size in gz_members.items():
    if zst_members.get(name) != size:
      return False
  return True


def sum_member_bytes(members: dict) -> int:
  """
  Sum member bytes.
  
  Args:
    members (dict): Mapping for members.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> sum_member_bytes({})  # doctest: +SKIP
  """
  return int(sum(members.values()))


def normalize_daily_compressed_path(path: str) -> str:
  """
  Return canonical ``.tar.zst`` path for a daily ``.tar`` / ``.gz`` / ``.zst``.
  
  Args:
    path (str): String for path.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> normalize_daily_compressed_path("x")  # doctest: +SKIP
  """
  if path.endswith(DAILY_ARCHIVE_ZST_SUFFIX):
    return path
  if path.endswith(DAILY_ARCHIVE_GZ_SUFFIX):
    return (
        path[: -len(DAILY_ARCHIVE_GZ_SUFFIX)] + DAILY_ARCHIVE_ZST_SUFFIX
    )
  if path.endswith(DAILY_ARCHIVE_TAR_SUFFIX):
    return path[: -len(DAILY_ARCHIVE_TAR_SUFFIX)] + DAILY_ARCHIVE_ZST_SUFFIX
  return path
