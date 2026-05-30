"""Daily archive path suffixes and zstd long-mode helpers (no Django)."""
from __future__ import annotations

import os

DAILY_ARCHIVE_ZST_SUFFIX = ".tar.zst"
DAILY_ARCHIVE_GZ_SUFFIX = ".tar.gz"
DAILY_ARCHIVE_TAR_SUFFIX = ".tar"

ARCHIVE_ZSTD_LONG_MIN_BYTES = 2 * 1024**3
ARCHIVE_ZSTD_LONG_FLAG = "--long=31"


def zstd_long_flags_for_bytes(
    byte_count: int,
    long_enabled: bool,
) -> list[str]:
  """Return ``['--long=31']`` when long mode is enabled and size meets threshold."""
  if not long_enabled:
    return []
  if byte_count < ARCHIVE_ZSTD_LONG_MIN_BYTES:
    return []
  return [ARCHIVE_ZSTD_LONG_FLAG]


def zstd_use_long_for_path(path: str, long_enabled: bool) -> bool:
  """Whether decompress should pass ``--long=31`` for this archive path."""
  if not long_enabled:
    return False
  try:
    return os.path.getsize(path) >= ARCHIVE_ZSTD_LONG_MIN_BYTES
  except OSError:
    return False


def detect_compressed_format(path: str) -> str | None:
  if path.endswith(DAILY_ARCHIVE_ZST_SUFFIX):
    return "zst"
  if path.endswith(DAILY_ARCHIVE_GZ_SUFFIX):
    return "gz"
  return None


def daily_tar_path_from_compressed(path: str) -> str:
  """Strip ``.tar.gz`` or ``.tar.zst`` to sibling ``YYYY-MM-DD.tar``."""
  if path.endswith(DAILY_ARCHIVE_ZST_SUFFIX):
    return path[: -len(DAILY_ARCHIVE_ZST_SUFFIX)] + DAILY_ARCHIVE_TAR_SUFFIX
  if path.endswith(DAILY_ARCHIVE_GZ_SUFFIX):
    return path[: -len(DAILY_ARCHIVE_GZ_SUFFIX)] + DAILY_ARCHIVE_TAR_SUFFIX
  if path.endswith(DAILY_ARCHIVE_TAR_SUFFIX):
    return path
  return path


def compressed_sibling_paths(tar_path: str) -> tuple[str, str]:
  """Return ``(zst_path, gz_path)`` for ``YYYY-MM-DD.tar``."""
  if tar_path.endswith(DAILY_ARCHIVE_TAR_SUFFIX):
    base = tar_path[: -len(DAILY_ARCHIVE_TAR_SUFFIX)]
  else:
    base = tar_path
  return (
      base + DAILY_ARCHIVE_ZST_SUFFIX,
      base + DAILY_ARCHIVE_GZ_SUFFIX,
  )


def daily_compressed_path_for_date(tgz_archive_dir: str, file_date) -> str:
  """Canonical sealed path ``.../YYYY-MM-DD.tar.zst``."""
  return os.path.join(
      tgz_archive_dir,
      file_date.strftime("%Y-%m-%d") + DAILY_ARCHIVE_ZST_SUFFIX,
  )


def archive_member_maps_equivalent(map_a: dict, map_b: dict) -> bool:
  return map_a == map_b


def sum_member_bytes(members: dict) -> int:
  return int(sum(members.values()))


def normalize_daily_compressed_path(path: str) -> str:
  """Return canonical ``.tar.zst`` path for a daily ``.tar`` / ``.gz`` / ``.zst``."""
  if path.endswith(DAILY_ARCHIVE_ZST_SUFFIX):
    return path
  if path.endswith(DAILY_ARCHIVE_GZ_SUFFIX):
    return (
        path[: -len(DAILY_ARCHIVE_GZ_SUFFIX)] + DAILY_ARCHIVE_ZST_SUFFIX
    )
  if path.endswith(DAILY_ARCHIVE_TAR_SUFFIX):
    return path[: -len(DAILY_ARCHIVE_TAR_SUFFIX)] + DAILY_ARCHIVE_ZST_SUFFIX
  return path
