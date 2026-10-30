"""
Archive free-space pressure for sync_timedb: growth yields to day close.

``sync_archive_space_pressure_free_bytes`` (X, default 1T; 0 = off). Pressure
starts when the daily archive filesystem has less than X free and lasts until
it has 2X free. Under pressure:

- appends wait while any day close is finishing a day (a stage in
  ``FINISHING_STAGES``; the checks, pre-seal verify batches and the seal's
  own yield checks do not count);
- otherwise one day at a time (the oldest parked day whose next batch fits)
  gets appends; once that day has no parked or in-flight appends, the next
  day waits until a day close gives space back (``note_space_freed``: raw
  deleted, tar sealed away or dropped; or
  ``DAY_CLOSE_START_GRACE_S`` passes with none starting, so a day that cannot
  close yet does not stop appends);
- restores through ``space_gate="growth"`` (the default of
  ``ensure_daily_tar_restored_for_append``) are denied; no production caller
  uses it today (ingest prewarm passes no restore days). Day close and
  repair restores (``finish``) and appends keep the hard reserve
  (``sync_archive_min_free_bytes``) plus room for the later reseal, net of
  the bytes other admitted work has reserved (``reserve`` / ``reserving``);
- seal compression runs without the nice/ionice wrapper so appends cannot
  starve day close.

INV-NEG: a held append day clears when a day close gives space back,
retries after ``DAY_CLOSE_START_GRACE_S`` without one, and pressure itself ends at 2X
free (day close frees space and is never gated here).

Attributes:
  ALL_DAYS: Sentinel from ``select_append_day``: no pressure, every day.
  DAY_CLOSE_START_GRACE_S: Seconds a finished append day waits for its close.
  FINISHING_STAGES: Day-close stages that finish a day (give space back).
  UNKNOWN_RATIO: Restore size multiple of a sealed file with no size header.
  _DENY_LOG_STATE: Rate-limit state for ``space_pressure deny`` lines.
  _LOCK: Guards ``_STATE`` across append, day-close and census threads.
  _STATE: Pressure flag, day-close stages by job identity, append day,
    hold timestamps, byte reservations and the pressure-end ``epoch``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print

ALL_DAYS = object()
DAY_CLOSE_START_GRACE_S = 600.0
UNKNOWN_RATIO = 10
# pre_seal_verify is left out: it runs one classify batch per claim and
# yields, so with many days queued it is almost always running somewhere.
FINISHING_STAGES = frozenset(
  {
    "reconcile_merge",
    "dedupe",
    "raw_delete_pre_seal",
    "seal",
    "post_seal_verify",
    "raw_delete",
    "tar_drop",
  }
)

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}
_DENY_LOG_STATE: dict[str, dict[str, float]] = {}


def reset_for_tests() -> None:
  """
  Clear pressure, day-close stages and the append-day slot.

  Returns:
    None

  Examples:
    >>> reset_for_tests()
  """
  _DENY_LOG_STATE.clear()
  with _LOCK:
    _STATE.clear()
    _STATE.update(
      pressure=False,
      stages={},
      append_day=None,
      hold_since=None,
      finished_since_hold=False,
      reserved={},
      epoch=0,
    )


reset_for_tests()


def _free_bytes(path: str) -> int:
  """
  Return free bytes on the filesystem holding ``path`` (nearest existing dir).

  Args:
    path (str): File or directory on the archive filesystem.

  Returns:
    int: Free bytes, or 0 when no ancestor can be read.

  Examples:
    >>> _free_bytes("/") >= 0
    True
  """
  target = os.path.abspath(str(path or "/"))
  while not os.path.exists(target) and os.path.dirname(target) != target:
    target = os.path.dirname(target)
  try:
    return int(shutil.disk_usage(target).free)
  except OSError:
    return 0


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
    if threshold > 0 and free < threshold:
      _STATE["pressure"] = True
    elif threshold <= 0 or free >= 2 * threshold:
      _STATE.update(pressure=False, append_day=None, hold_since=None)
      _STATE["epoch"] += 1  # a selection in progress must not write back
    return bool(_STATE["pressure"])


def note_day_close_stage(job: str, stage: str) -> None:
  """
  Record the current stage of day-close job ``job`` (from ``stage_enter``).

  Keyed by job identity, not calendar day: a bare-day and a tar-path
  identity for one day are two jobs, and one ending must not clear the
  other's stage.

  Args:
    job (str): Day-close job identity (daily tar path or bare day).
    stage (str): Stage name, for example ``seal`` or ``disk_remaining_raw``.

  Returns:
    None

  Examples:
    >>> note_day_close_stage("/d/2026-09-07.tar", "seal")
    >>> clear_day_close("/d/2026-09-07.tar")
  """
  with _LOCK:
    _STATE["stages"][str(job)] = str(stage)


def note_space_freed() -> None:
  """
  Record that a day close gave space back (raw deleted, tar sealed/dropped).

  This, not entering a ``FINISHING_STAGES`` stage, ends the hold after an
  append day: a day close that reconciles and then yields frees nothing.

  Returns:
    None

  Examples:
    >>> note_space_freed()
  """
  with _LOCK:
    _STATE["finished_since_hold"] = True


def clear_day_close(job: str) -> None:
  """
  Forget day-close job ``job``'s stage once the job has ended.

  Args:
    job (str): Day-close job identity passed to ``note_day_close_stage``.

  Returns:
    None

  Examples:
    >>> clear_day_close("/d/2026-09-07.tar")
  """
  with _LOCK:
    _STATE["stages"].pop(str(job), None)


def _finishing_locked() -> bool:
  """
  Return True while any day close is in a ``FINISHING_STAGES`` stage.

  The caller holds ``_LOCK``.

  Returns:
    bool: True when a stage that finishes a day is running.

  Examples:
    >>> with _LOCK:
    ...   isinstance(_finishing_locked(), bool)
    True
  """
  return any(s in FINISHING_STAGES for s in _STATE["stages"].values())


def select_append_day(
  parked_days: Iterable[str],
  busy_days: Iterable[str],
  fits: Callable[[str], bool] | None = None,
) -> Any:
  """
  Return which day may get appends now.

  Without pressure every day may (``ALL_DAYS``). Under pressure: ``None``
  while a day close is finishing a day; else the day holding the slot while
  it has an in-flight batch or parked appends that fit; else, right after
  such a day finished its appends, ``None`` until a day close has given
  space back (``note_space_freed``; or the grace passed without one); else the oldest parked day
  whose next batch fits, or ``""`` when none is parked or none fits (keep
  claiming; choose again next tick). A held day whose batch no longer fits
  gives up the slot without a hold, so it cannot block every other day.

  Args:
    parked_days (Iterable[str]): Days with parked append claims.
    busy_days (Iterable[str]): Days with an append batch in flight.
    fits (Callable[[str], bool] | None): Whether a day's next batch fits on
      disk (``_append_batch_fits_space``); None treats every day as fitting.

  Returns:
    Any: ``ALL_DAYS``, a day string, ``""`` (no day chosen yet) or
    ``None`` (appends wait).

  Examples:
    >>> reset_for_tests()
    >>> select_append_day(["2026-09-01"], []) is not None
    True
  """
  if not under_pressure():
    return ALL_DAYS
  fits = fits or (lambda _day: True)
  parked = sorted(str(d) for d in parked_days)
  busy = {str(d) for d in busy_days}
  now = time.monotonic()
  with _LOCK:
    finishing = _finishing_locked()
    current = _STATE["append_day"]
    epoch = _STATE["epoch"]
  if current in busy or (current in parked and fits(current)):
    return None if finishing else current
  candidates = [] if finishing else [d for d in parked if d != current]
  with _LOCK:
    if _STATE["epoch"] != epoch:
      return ALL_DAYS  # pressure ended while ``fits`` ran
    if current is not None and current not in parked:
      _STATE.update(hold_since=now, finished_since_hold=False)
    _STATE["append_day"] = None
    hold_since = _STATE["hold_since"]
    waiting = hold_since is not None and not _STATE["finished_since_hold"]
    if finishing or (waiting and now - hold_since < DAY_CLOSE_START_GRACE_S):
      return None
    _STATE["hold_since"] = None
  chosen = next((d for d in candidates if fits(d)), "")
  with _LOCK:
    if _STATE["epoch"] != epoch:
      return ALL_DAYS
    _STATE["append_day"] = chosen or None
  return chosen


def fits_hard_reserve(path: str, need: int, *, restore_need: int = 0) -> bool:
  """
  Return True when ``need`` bytes plus a later reseal keep the hard reserve.

  Args:
    path (str): Path on the archive filesystem.
    need (int): Bytes the operation writes (restore plus raw appended).
    restore_need (int): Restored tar bytes; half is kept for its reseal.

  Returns:
    bool: True when ``free - need - restore_need / 2`` stays at or above
    ``sync_archive_min_free_bytes``, or when nothing is written.

  Examples:
    >>> fits_hard_reserve("/", 0)
    True
  """
  if int(need) <= 0:
    return True
  reserve = int(cfg.get_sync_archive_min_free_bytes())
  left = (
    _free_bytes(path) - reserved_bytes() - int(need) - int(restore_need) // 2
  )
  return left >= reserve


def reserve(key: str, nbytes: int) -> None:
  """
  Hold ``nbytes`` for admitted work that has not written them yet.

  ``fits_hard_reserve`` subtracts every held reservation, so concurrent
  admissions (append batches, day-close restores) see each other.

  Args:
    key (str): Owner key (the daily tar path).
    nbytes (int): Bytes the work will write.

  Returns:
    None

  Examples:
    >>> reserve("/d/2026-09-07.tar", 10)
    >>> release("/d/2026-09-07.tar")
  """
  with _LOCK:
    _STATE["reserved"][str(key)] = max(0, int(nbytes))


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
  Return the bytes held by all current reservations.

  Returns:
    int: Sum of reservations.

  Examples:
    >>> reserved_bytes() >= 0
    True
  """
  with _LOCK:
    return sum(_STATE["reserved"].values())


@contextlib.contextmanager
def reserving(key: str, nbytes: int) -> Iterator[None]:
  """
  Hold a reservation for the duration of a ``with`` block.

  Args:
    key (str): Owner key (the daily tar path).
    nbytes (int): Bytes the block will write.

  Yields:
    None: While the reservation is held.

  Examples:
    >>> with reserving("/d/2026-09-07.tar", 5):
    ...   reserved_bytes() >= 5
    True
  """
  reserve(key, nbytes)
  try:
    yield
  finally:
    release(key)


def admit_restore(tar_path: str, mode: str) -> bool:
  """
  Gate a restore of ``tar_path`` from its sealed copy.

  Args:
    tar_path (str): Daily tar the restore would write.
    mode (str): ``growth`` (denied under pressure, else hard reserve),
      ``finish`` (day close: hard reserve only) or ``off`` (the append
      dispatch already gated it).

  Returns:
    bool: True when the restore may run.

  Examples:
    >>> admit_restore("/nonexistent/2026-09-07.tar", "off")
    True
  """
  if mode == "off":
    return True
  need = restore_bytes_estimate(tar_path)
  if mode != "finish" and need > 0 and under_pressure(tar_path):
    reason = "space_pressure"
  elif fits_hard_reserve(tar_path, need, restore_need=need):
    return True
  else:
    reason = "hard_reserve"
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
    `` space=pressure free=… resume_at=… append_day=…``.

  Examples:
    >>> isinstance(census_token("/"), str)
    True
  """
  threshold = int(cfg.get_sync_archive_space_pressure_free_bytes())
  if threshold <= 0:
    return ""
  pressure = under_pressure(daily_dir)
  free = _tib(_free_bytes(daily_dir or "/"))
  if not pressure:
    return f" space=ok free={free} pressure_below={_tib(threshold)}"
  with _LOCK:
    day = _STATE["append_day"] or "none"
  return (
    f" space=pressure free={free} resume_at={_tib(2 * threshold)}"
    f" append_day={day}"
  )
