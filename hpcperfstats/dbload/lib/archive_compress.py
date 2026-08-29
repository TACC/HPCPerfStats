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


def archive_member_maps_union(
  tar_members: dict[str, int],
  zst_members: dict[str, int],
) -> dict[str, int]:
  """
  Union of tar and zst member maps; largest byte size wins per basename.

  When sizes tie, prefer the tar side (AR-06 open-tar authority).

  Args:
    tar_members (dict[str, int]): Open tar basename → byte size.
    zst_members (dict[str, int]): Sealed archive basename → byte size.

  Returns:
    dict[str, int]: Canonical union map.

  Examples:
    >>> archive_member_maps_union({"a": 10, "b": 5}, {"a": 20, "c": 3})
    {'a': 20, 'b': 5, 'c': 3}
  """
  union: dict[str, int] = dict(zst_members or {})
  for name, tar_size in (tar_members or {}).items():
    prev = union.get(name)
    if prev is None or tar_size > prev:
      union[name] = int(tar_size)
    elif tar_size == prev:
      union[name] = int(tar_size)
  return union


def classify_daily_tar_zst_reconcile(
  tar_members: dict[str, int],
  zst_members: dict[str, int],
  *,
  zst_exists: bool,
  zst_readable: bool,
  tar_gnu_readable: bool = True,
) -> tuple[str, str]:
  """
  Classify how an open daily tar and sealed zst should be reconciled.

  Returns ``(action, reason)`` where action is one of:

  ``noop``, ``repair_tar_only``, ``seal_from_tar``, ``merge_and_seal``,
  ``skip``.

  Args:
    tar_members (dict[str, int]): Open tar member map (may be empty when GNU
      scan failed).
    zst_members (dict[str, int]): Sealed member map.
    zst_exists (bool): Whether sibling ``.tar.zst`` exists.
    zst_readable (bool): Whether sealed scan / ``zstd -t`` succeeded.
    tar_gnu_readable (bool): Whether GNU ``tar tf`` passed on the open tar.

  Returns:
    tuple[str, str]: Action token and diagnostic reason.

  Examples:
    >>> classify_daily_tar_zst_reconcile({"a": 1}, {}, zst_exists=False,
    ...     zst_readable=False)
    ('seal_from_tar', 'missing_zst')
    >>> classify_daily_tar_zst_reconcile({"a": 1, "b": 2}, {"a": 1},
    ...     zst_exists=True, zst_readable=True)
    ('seal_from_tar', 'tar_superset_of_zst')
  """
  if not tar_gnu_readable:
    return "repair_tar_only", "tar_gnu_unreadable"
  if not zst_exists:
    return "seal_from_tar", "missing_zst"
  if not zst_readable or not zst_members:
    return "skip", "zst_unreadable"
  if archive_member_maps_equivalent(tar_members, zst_members):
    return "noop", "already_equivalent"
  if archive_gz_members_contained_in_zst(zst_members, tar_members):
    return "seal_from_tar", "tar_superset_of_zst"
  return "merge_and_seal", "divergent_maps"


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
