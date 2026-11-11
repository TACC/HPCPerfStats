"""
Archive free-space pressure for sync_timedb: growth freezes until day close.

``sync_archive_space_pressure_free_bytes`` (X, default 1T; 0 = off). Pressure
starts when the daily archive filesystem has less than X free and lasts until
it has 2X free. Under pressure nothing grows the disk but a seal and a
reflink clone append that cannot grow the filesystem (2026-11-11):

- only clone appends run: a day whose open ``.tar`` already exists (or a
  new tar) and whose raw ``clone_capable`` reflinks writes only headers
  (``sync_timedb_tar_clone_append``); the orchestrator keeps every other
  parked day (a restore from its sealed copy, or a copying append) parked;
- every restore is refused: ``admit_restore`` denies ``growth`` and
  ``finish`` (day-close verify, open-tar repair, salvage) alike and returns
  ``SPACE_DENIED``; the append dispatch refuses a batch that would restore;
- ``archive_space_admit`` (archive helpers) refuses every op but ``seal``:
  union rebuild, salvage, corrupt-tar restore, repair and the dedupe copy;
- seal, raw delete and tar drop are never gated here (they give space back).

Without pressure appends and restores keep the hard reserve
(``sync_archive_min_free_bytes``) plus room for the later reseal, net of the
bytes other admitted work has reserved (``reserve`` / ``reserving``).
INV-NEG: pressure ends at 2X free, and day close, which frees the space, is
never gated here; a refused restore, repair or rewrite defers
(``SPACE_DENIED`` / ``env:ENOSPC`` / parked claims), never fails.

Attributes:
  ALL_DAYS: Sentinel from ``_select_append_day``: no pressure, every day.
  SPACE_DENIED: Falsy restore result when ``admit_restore`` refused it.
  UNKNOWN_RATIO: Restore size multiple of a sealed file with no size header.
  _DENY_LOG_STATE: Rate-limit state for ``space_pressure deny`` lines.
  _LOCK: Guards ``_STATE`` across append, day-close and census threads.
  _STATE: Pressure flag and the byte reservations of admitted work.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
from collections.abc import Iterator
from typing import Any

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print

ALL_DAYS = object()
UNKNOWN_RATIO = 10


class _SpaceDenied:
  """
  Falsy result of a restore the space gate refused (``SPACE_DENIED``).

  ``ensure_daily_tar_restored_for_append`` returns it instead of ``False``
  so callers that only test truthiness keep deferring, and the open-tar
  repair path can tell an ``env:ENOSPC`` defer from a failed restore.
  """

  def __bool__(self) -> bool:
    """
    Return False so ``if not restored`` still treats a denial as no tar.

    Returns:
      bool: Always False.

    Examples:
      >>> bool(SPACE_DENIED)
      False
    """
    return False

  def __repr__(self) -> str:
    """
    Return the sentinel's name for logs and test failures.

    Returns:
      str: ``SPACE_DENIED``.

    Examples:
      >>> SPACE_DENIED
      SPACE_DENIED
    """
    return "SPACE_DENIED"


SPACE_DENIED = _SpaceDenied()
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}
_DENY_LOG_STATE: dict[str, dict[str, float]] = {}


def reset_for_tests() -> None:
  """
  Clear the pressure flag, reservations and deny-log rate limits.

  Returns:
    None

  Examples:
    >>> reset_for_tests()
  """
  _DENY_LOG_STATE.clear()
  with _LOCK:
    _STATE.clear()
    _STATE.update(pressure=False, reserved={})


reset_for_tests()


def _free_bytes(path: str) -> int | None:
  """
  Return free bytes on the filesystem holding ``path`` or its directory.

  Only ``path`` and its parent (the daily dir of a tar not restored yet)
  are tried: walking further up would measure the filesystem under a
  missing archive mount.

  Args:
    path (str): File or directory on the archive filesystem.

  Returns:
    int | None: Free bytes, or None when neither exists or statfs fails
    (unknown, not full).

  Examples:
    >>> _free_bytes("/") >= 0
    True
    >>> _free_bytes("/nonexistent-dir/2026-09-07.tar") is None
    True
  """
  target = os.path.abspath(str(path or "/"))
  if not os.path.exists(target):
    target = os.path.dirname(target)
  try:
    return int(shutil.disk_usage(target).free)
  except OSError:
    return None


def _size(path: str) -> int:
  """
  Return the size of ``path`` in bytes, or 0 when it is missing.

  Args:
    path (str): File path.

  Returns:
    int: Size in bytes.

  Examples:
    >>> _size("/nonexistent-path")
    0
  """
  try:
    return int(os.path.getsize(path))
  except OSError:
    return 0


def _tib(n: int) -> str:
  """
  Format bytes as TiB with one decimal and a ``T`` suffix.

  Args:
    n (int): Bytes.

  Returns:
    str: For example ``3.2T``.

  Examples:
    >>> _tib(1 << 40)
    '1.0T'
  """
  return f"{n / (1 << 40):.1f}T"


def under_pressure(path: str = "") -> bool:
  """
  Update and return the pressure state (enter below X, leave at 2X free).

  When free space cannot be read (statfs error, missing archive dir) the
  state is left as it was: an unknown is not a full disk.

  Args:
    path (str): Path on the archive filesystem; empty uses the INI
      ``daily_archive_dir``.

  Returns:
    bool: True while under pressure; always False when X is 0.

  Examples:
    >>> isinstance(under_pressure("/"), bool)
    True
  """
  threshold = int(cfg.get_sync_archive_space_pressure_free_bytes())
  free = (
    _free_bytes(path or cfg.get_daily_archive_dir_path() or "/")
    if threshold > 0
    else 0
  )
  with _LOCK:
    if free is None:
      pass  # unknown free space keeps the current state
    elif threshold > 0 and free < threshold:
      _STATE["pressure"] = True
    elif threshold <= 0 or free >= 2 * threshold:
      _STATE["pressure"] = False
    return bool(_STATE["pressure"])


def fits_hard_reserve(path: str, need: int, *, restore_need: int = 0) -> bool:
  """
  Return True when ``need`` bytes plus a later reseal keep the hard reserve.

  Args:
    path (str): Path on the archive filesystem.
    need (int): Bytes the operation writes (restore plus raw appended).
    restore_need (int): Restored tar bytes; half is kept for its reseal.

  Returns:
    bool: True when ``free - need - restore_need / 2`` stays at or above
    ``sync_archive_min_free_bytes``, or when nothing is written; False when
    free space cannot be read.

  Examples:
    >>> fits_hard_reserve("/", 0)
    True
  """
  if int(need) <= 0:
    return True
  free = _free_bytes(path)
  if free is None:
    return False  # cannot prove the reserve; callers defer
  reserve = int(cfg.get_sync_archive_min_free_bytes())
  left = free - reserved_bytes() - int(need) - int(restore_need) // 2
  return left >= reserve


def _tar_bytes_on_disk(tar_path: str) -> int:
  """
  Return the bytes a restore or append into ``tar_path`` has on disk.

  Counts the tar and its ``.decomp.tmp`` restore temp, so a restore's
  bytes are counted once before and after its ``os.replace``.

  Args:
    tar_path (str): Daily tar path, or ``""`` for none.

  Returns:
    int: Sum of both sizes (0 when ``tar_path`` is empty or missing).

  Examples:
    >>> _tar_bytes_on_disk("/nonexistent/2026-09-07.tar")
    0
  """
  if not tar_path:
    return 0
  return _size(tar_path) + _size(f"{tar_path}.decomp.tmp")


def reserve(key: str, nbytes: int, *, tar_path: str = "") -> None:
  """
  Hold ``nbytes`` for admitted work that has not written them yet.

  ``fits_hard_reserve`` subtracts every held reservation, so concurrent
  admissions (append batches, day-close restores) see each other. With
  ``tar_path`` the reservation shrinks as that tar (and its restore temp)
  grows, since ``free`` already reflects what was written.

  Args:
    key (str): Owner key (the daily tar path).
    nbytes (int): Bytes the work will write.
    tar_path (str): Tar the work writes into; ``""`` keeps the full
      reservation until ``release``.

  Returns:
    None

  Examples:
    >>> reserve("/d/2026-09-07.tar", 10, tar_path="/d/2026-09-07.tar")
    >>> release("/d/2026-09-07.tar")
  """
  base = _tar_bytes_on_disk(tar_path)
  with _LOCK:
    _STATE["reserved"][str(key)] = (max(0, int(nbytes)), tar_path, base)


def release(key: str) -> None:
  """
  Drop the reservation held under ``key`` (no-op when none).

  Args:
    key (str): Owner key passed to ``reserve``.

  Returns:
    None

  Examples:
    >>> release("/d/2026-09-07.tar")
  """
  with _LOCK:
    _STATE["reserved"].pop(str(key), None)


def reserved_bytes() -> int:
  """
  Return the bytes current reservations still have to write.

  Returns:
    int: Sum over reservations of ``nbytes`` minus what their tar has
    grown since ``reserve`` (never below 0).

  Examples:
    >>> reserved_bytes() >= 0
    True
  """
  with _LOCK:
    held = list(_STATE["reserved"].values())
  return sum(
    max(0, nbytes - max(0, _tar_bytes_on_disk(tar) - base))
    for nbytes, tar, base in held
  )


@contextlib.contextmanager
def reserving(key: str, nbytes: int, *, tar_path: str = "") -> Iterator[None]:
  """
  Hold a reservation for the duration of a ``with`` block.

  Args:
    key (str): Owner key (the daily tar path).
    nbytes (int): Bytes the block will write.
    tar_path (str): Tar the block writes into (see ``reserve``).

  Yields:
    None: While the reservation is held.

  Examples:
    >>> with reserving("/d/2026-09-07.tar", 5):
    ...   reserved_bytes() >= 5
    True
  """
  reserve(key, nbytes, tar_path=tar_path)
  try:
    yield
  finally:
    release(key)


def admit_restore(tar_path: str, mode: str) -> bool:
  """
  Gate a restore of ``tar_path`` from its sealed copy.

  Args:
    tar_path (str): Daily tar the restore would write.
    mode (str): ``growth`` (ingest prewarm) or ``finish`` (day close:
      verify, open-tar repair, salvage): both denied under pressure, else
      the hard reserve; ``off``: the append dispatch already gated it.

  Returns:
    bool: True when the restore may run.

  Examples:
    >>> admit_restore("/nonexistent/2026-09-07.tar", "off")
    True
  """
  if mode == "off":
    return True
  need = restore_bytes_estimate(tar_path)
  if need > 0 and under_pressure(tar_path):
    reason = "space_pressure"
  elif not fits_hard_reserve(tar_path, need, restore_need=need):
    reason = "hard_reserve"
  else:
    return True
  log_deny("restore", tar_path, need, reason=f"{reason} mode={mode}")
  return False


def log_deny(op: str, tar_path: str, need: int, *, reason: str) -> None:
  """
  Log a space-gate denial, at most once per 300 s per day and op.

  Args:
    op (str): ``restore`` or ``append``.
    tar_path (str): Daily tar the operation targets.
    need (int): Bytes the operation would write.
    reason (str): ``space_pressure`` or ``hard_reserve`` (plus detail).

  Returns:
    None

  Examples:
    >>> log_deny("append", "/d/2026-09-07.tar", 1, reason="hard_reserve")
    ... # doctest: +ELLIPSIS
    [...] WARNING: space_pressure deny op=append tar=/d/2026-09-07.tar need=1 ...
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    _rate_limited_day_info_log,
  )

  day = os.path.basename(str(tar_path))[:10]
  _rate_limited_day_info_log(
    _DENY_LOG_STATE,
    f"{day}:{op}",
    f"WARNING: space_pressure deny op={op} tar={tar_path} need={int(need)} "
    f"free={_free_bytes(tar_path)} reason={reason}",
    interval_s=300.0,
    log_fn=log_print,
  )


def _zst_frame_content_size(zst_path: str) -> int | None:
  """
  Return the uncompressed size recorded in the first zstd frame header.

  Args:
    zst_path (str): ``.zst`` file.

  Returns:
    int | None: Content size, or None when the header does not carry it.

  Examples:
    >>> _zst_frame_content_size("/nonexistent.zst") is None
    True
  """
  try:
    with open(zst_path, "rb") as fh:
      head = fh.read(18)
  except OSError:
    return None
  if len(head) < 6 or head[:4] != b"\x28\xb5\x2f\xfd":
    return None
  fhd = head[4]
  single_segment = (fhd >> 5) & 1
  fcs_len = (0, 2, 4, 8)[fhd >> 6] or (1 if single_segment else 0)
  pos = 5 + (0 if single_segment else 1) + (0, 1, 2, 4)[fhd & 3]
  field = head[pos : pos + fcs_len]
  if not fcs_len or len(field) != fcs_len:
    return None
  value = int.from_bytes(field, "little")
  return value + 256 if fcs_len == 2 else value


def restore_bytes_estimate(tar_path: str) -> int:
  """
  Estimate the bytes a restore of ``tar_path`` from its sealed copy writes.

  Args:
    tar_path (str): Daily ``YYYY-MM-DD.tar`` path.

  Returns:
    int: 0 when the tar is present and non-empty or nothing is sealed; the
    zstd frame content size when recorded; else ``UNKNOWN_RATIO`` times the
    sealed file size.

  Examples:
    >>> restore_bytes_estimate("/nonexistent/2026-09-07.tar")
    0
  """
  if _size(tar_path) > 0:
    return 0
  for sealed in (f"{tar_path}.zst", f"{tar_path}.gz"):
    if os.path.isfile(sealed):
      known = sealed.endswith(".zst") and _zst_frame_content_size(sealed)
      return int(known) if known else _size(sealed) * UNKNOWN_RATIO
  return 0


def census_token(daily_dir: str) -> str:
  """
  Return the census token for the pressure state, or ``""`` when off.

  Args:
    daily_dir (str): Daily archive directory.

  Returns:
    str: `` space=ok free=… pressure_below=…`` or
    `` space=pressure free=… resume_at=…``.

  Examples:
    >>> isinstance(census_token("/"), str)
    True
  """
  threshold = int(cfg.get_sync_archive_space_pressure_free_bytes())
  if threshold <= 0:
    return ""
  pressure = under_pressure(daily_dir)
  known = _free_bytes(daily_dir or "/")
  free = "unknown" if known is None else _tib(known)
  if not pressure:
    return f" space=ok free={free} pressure_below={_tib(threshold)}"
  return f" space=pressure free={free} resume_at={_tib(2 * threshold)}"
