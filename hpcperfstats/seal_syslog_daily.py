"""
Seal prior-day per-host syslog files into one ``YYYY-MM-DD-syslog.tar.gz``.

Runs in the pipeline container (supervisord). Uses ``[DEFAULT] timezone`` and
paths under ``data_dir/logs/`` (see ``conf_parser`` syslog helpers). Filenames
must match syslog-ng: ``$HOST.$R_YEAR$R_MONTH$R_DAY.log`` (YYYYMMDD, no
separators in the date part).
"""

from __future__ import annotations

import contextlib
import os
import sys
import tarfile
import time
from datetime import date, datetime, timedelta
from typing import Any

from hpcperfstats.dbload.lib import conf_parser as cfg


def _sleep_seconds() -> Any:
  """
  Return the run-loop poll interval from the environment (floor 60s).

  Returns:
    Any: ``int`` seconds from ``HPCPERFSTATS_SYSLOG_SEAL_POLL_SECONDS``.

  Examples:
    >>> _sleep_seconds()  # doctest: +SKIP
  """
  return max(
    60, int(os.environ.get("HPCPERFSTATS_SYSLOG_SEAL_POLL_SECONDS", "3600"))
  )


def _seal_after_local_hour() -> Any:
  """
  Return the local hour after which yesterday's logs may be sealed.

  Returns:
    Any: ``int`` hour from ``HPCPERFSTATS_SYSLOG_SEAL_AFTER_HOUR``.

  Examples:
    >>> _seal_after_local_hour()  # doctest: +SKIP
  """
  return int(os.environ.get("HPCPERFSTATS_SYSLOG_SEAL_AFTER_HOUR", "0"))


def _seal_after_local_minute() -> Any:
  """
  Return the minute within the seal hour after which sealing may run.

  Returns:
    Any: ``int`` minute from ``HPCPERFSTATS_SYSLOG_SEAL_AFTER_MINUTE``.

  Examples:
    >>> _seal_after_local_minute()  # doctest: +SKIP
  """
  return int(os.environ.get("HPCPERFSTATS_SYSLOG_SEAL_AFTER_MINUTE", "5"))


def _yyyymmdd(d: date) -> str:
  """
  Format ``d`` as the separator-free date syslog-ng puts in filenames.

  Args:
    d (date): Calendar day.

  Returns:
    str: ``YYYYMMDD``.

  Examples:
    >>> _yyyymmdd(date(2026, 1, 2))
    '20260102'
  """
  return f"{d.year:04}{d.month:02}{d.day:02}"


def _paths_for_day(current_dir: str, day: date) -> Any:
  """
  List per-host ``*.YYYYMMDD.log`` files for ``day`` in ``current_dir``.

  Args:
    current_dir (str): Live syslog directory (``logs/current``).
    day (date): Calendar day to match.

  Returns:
    Any: Sorted ``list[str]`` of absolute paths.

  Examples:
    >>> _paths_for_day("x", None)  # doctest: +SKIP
  """
  suffix = f".{_yyyymmdd(day)}.log"
  out = []
  for name in os.listdir(current_dir):
    if name.endswith(suffix):
      out.append(os.path.join(current_dir, name))
  return sorted(out)


def seal_day(day: date, *, log_fn: Any = print) -> bool:
  """
  Build ``log_archive/{day}-syslog.tar.gz`` from per-host logs for ``day``.

  Removes the sealed source logs. A valid existing archive only clears
  leftovers; an unreadable one is rebuilt.

  Args:
    day (date): Calendar day to seal.
    log_fn (Any): ``print``-like callable for status lines.

  Returns:
    bool: False only when writing the archive failed.

  Examples:
    >>> seal_day(None, None)  # doctest: +SKIP
  """
  current_dir = cfg.get_syslog_logs_current_path()
  archive_dir = cfg.get_syslog_logs_archive_path()
  os.makedirs(archive_dir, exist_ok=True)
  stamp = day.isoformat()
  tar_path = os.path.join(archive_dir, f"{stamp}-syslog.tar.gz")
  if os.path.isfile(tar_path):
    try:
      with tarfile.open(tar_path, "r:gz") as tf:
        tf.getmembers()
    except (OSError, tarfile.TarError) as exc:
      log_fn(f"seal_syslog_daily: replacing invalid archive {tar_path} ({exc})")
    else:
      log_fn(f"seal_syslog_daily: archive already exists {tar_path}")
      for p in _paths_for_day(current_dir, day):
        try:
          os.unlink(p)
        except OSError as exc:
          log_fn(f"seal_syslog_daily: warning removing leftover {p} ({exc})")
      return True
  paths = _paths_for_day(current_dir, day)
  if not paths:
    log_fn(f"seal_syslog_daily: no files for {stamp} under {current_dir}")
    return True
  tmp = tar_path + ".tmp"
  try:
    with tarfile.open(tmp, "w:gz") as tf:
      for p in paths:
        arc = os.path.basename(p)
        tf.add(p, arcname=os.path.join("syslog", arc))
    os.replace(tmp, tar_path)
  except (OSError, tarfile.TarError) as exc:
    log_fn(f"seal_syslog_daily: failed to write {tar_path} ({exc})")
    with contextlib.suppress(OSError):
      os.unlink(tmp)
    return False
  for p in paths:
    try:
      os.unlink(p)
    except OSError as exc:
      log_fn(f"seal_syslog_daily: warning removing {p} ({exc})")
  log_fn(f"seal_syslog_daily: wrote {tar_path} ({len(paths)} members)")
  return True


def _local_now() -> Any:
  """
  Return the current time in the configured ``[DEFAULT] timezone``.

  Returns:
    Any: Timezone-aware ``datetime``.

  Examples:
    >>> _local_now()  # doctest: +SKIP
  """
  return datetime.now(cfg.get_local_timezone())


def _should_run_seal_for_yesterday(now: datetime) -> bool:
  """
  Return True once ``now`` is past the configured local seal hour:minute.

  Args:
    now (datetime): Current local time.

  Returns:
    bool: True when yesterday may be sealed.

  Examples:
    >>> _should_run_seal_for_yesterday(None)  # doctest: +SKIP
  """
  if now.hour > _seal_after_local_hour():
    return True
  if now.hour < _seal_after_local_hour():
    return False
  return now.minute >= _seal_after_local_minute()


def run_loop(*, log_fn: Any = print) -> None:
  """
  Poll forever, sealing yesterday once per day.

  ``log_archive/.last_seal_yyyymmdd`` records the last sealed day so a
  restart does not reseal it.

  Args:
    log_fn (Any): ``print``-like callable for status lines.

  Returns:
    None

  Examples:
    >>> run_loop(None)  # doctest: +SKIP
  """
  marker = os.path.join(
    cfg.get_syslog_logs_archive_path(), ".last_seal_yyyymmdd"
  )
  while True:
    try:
      now = _local_now()
      if _should_run_seal_for_yesterday(now):
        yday = now.date() - timedelta(days=1)
        key = yday.isoformat()
        last = None
        if os.path.isfile(marker):
          with open(marker, encoding="utf-8") as f:
            last = f.read().strip()
        if last != key and seal_day(yday, log_fn=log_fn):
          os.makedirs(os.path.dirname(marker), exist_ok=True)
          with open(marker, "w", encoding="utf-8") as f:
            f.write(key)
    except Exception as exc:
      log_fn(f"seal_syslog_daily: error {exc}")
    time.sleep(_sleep_seconds())


def main(argv: Any | None = None) -> Any:
  """
  Seal one day with ``--date YYYY-MM-DD``, else run the polling loop.

  Args:
    argv (Any | None): Argument list, or ``None`` for ``sys.argv``.

  Returns:
    Any: ``int`` exit status (1 when a ``--date`` seal failed).

  Examples:
    >>> main(["seal_syslog_daily", "--date", "2026-01-02"])  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_title import set_script_process_title

  set_script_process_title()
  argv = argv if argv is not None else sys.argv
  if len(argv) >= 3 and argv[1] == "--date":
    day = date.fromisoformat(argv[2])
    ok = seal_day(day)
    return 0 if ok else 1
  run_loop()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
