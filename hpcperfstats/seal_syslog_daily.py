"""
Seal prior-day per-host syslog files into one ``YYYY-MM-DD-syslog.tar.gz``.

Runs in the pipeline container (supervisord). Uses ``[DEFAULT] timezone`` and
paths under ``data_dir/logs/`` (see ``conf_parser`` syslog helpers). Filenames
must match syslog-ng: ``$HOST.$R_YEAR$R_MONTH$R_DAY.log`` (YYYYMMDD, no
separators in the date part).
"""
from __future__ import annotations

from typing import Any

import os
import sys
import tarfile
import time
from datetime import date, datetime, timedelta

from hpcperfstats.dbload.lib import conf_parser as cfg


def _sleep_seconds() -> Any:
  """
  Internal helper to sleep for the seconds.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _sleep_seconds()  # doctest: +SKIP
  """
  return max(60, int(os.environ.get("HPCPERFSTATS_SYSLOG_SEAL_POLL_SECONDS", "3600")))


def _seal_after_local_hour() -> Any:
  """
  Internal helper to seal the after local hour.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _seal_after_local_hour()  # doctest: +SKIP
  """
  return int(os.environ.get("HPCPERFSTATS_SYSLOG_SEAL_AFTER_HOUR", "0"))


def _seal_after_local_minute() -> Any:
  """
  Internal helper to seal the after local minute.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _seal_after_local_minute()  # doctest: +SKIP
  """
  return int(os.environ.get("HPCPERFSTATS_SYSLOG_SEAL_AFTER_MINUTE", "5"))


def _yyyymmdd(d: date) -> str:
  """
  Internal helper to handle yyyymmdd.
  
  Args:
    d (date): D.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _yyyymmdd(None)  # doctest: +SKIP
  """
  return "%04d%02d%02d" % (d.year, d.month, d.day)


def _paths_for_day(current_dir: str, day: date) -> Any:
  """
  Internal helper to handle paths for day.
  
  Args:
    current_dir (str): String for current dir.
    day (date): Day.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _paths_for_day("x", None)  # doctest: +SKIP
  """
  suffix = ".%s.log" % _yyyymmdd(day)
  out = []
  for name in os.listdir(current_dir):
    if name.endswith(suffix):
      out.append(os.path.join(current_dir, name))
  return sorted(out)


def seal_day(day: date, *, log_fn: Any = print) -> bool:
  """
  Build ``log_archive/{day}-syslog.tar.gz`` from per-host logs for ``day``.
  
  Args:
    day (date): Day.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> seal_day(None, None)  # doctest: +SKIP
  """
  current_dir = cfg.get_syslog_logs_current_path()
  archive_dir = cfg.get_syslog_logs_archive_path()
  os.makedirs(archive_dir, exist_ok=True)
  stamp = day.isoformat()
  tar_path = os.path.join(archive_dir, "%s-syslog.tar.gz" % stamp)
  if os.path.isfile(tar_path):
    try:
      with tarfile.open(tar_path, "r:gz") as tf:
        tf.getmembers()
    except (OSError, tarfile.TarError) as exc:
      log_fn("seal_syslog_daily: replacing invalid archive %s (%s)" % (tar_path, exc))
    else:
      log_fn("seal_syslog_daily: archive already exists %s" % tar_path)
      for p in _paths_for_day(current_dir, day):
        try:
          os.unlink(p)
        except OSError as exc:
          log_fn("seal_syslog_daily: warning removing leftover %s (%s)" % (p, exc))
      return True
  paths = _paths_for_day(current_dir, day)
  if not paths:
    log_fn("seal_syslog_daily: no files for %s under %s" % (stamp, current_dir))
    return True
  tmp = tar_path + ".tmp"
  try:
    with tarfile.open(tmp, "w:gz") as tf:
      for p in paths:
        arc = os.path.basename(p)
        tf.add(p, arcname=os.path.join("syslog", arc))
    os.replace(tmp, tar_path)
  except (OSError, tarfile.TarError) as exc:
    log_fn("seal_syslog_daily: failed to write %s (%s)" % (tar_path, exc))
    try:
      os.unlink(tmp)
    except OSError:
      pass
    return False
  for p in paths:
    try:
      os.unlink(p)
    except OSError as exc:
      log_fn("seal_syslog_daily: warning removing %s (%s)" % (p, exc))
  log_fn("seal_syslog_daily: wrote %s (%d members)" % (tar_path, len(paths)))
  return True


def _local_now() -> Any:
  """
  Internal helper to handle local now.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _local_now()  # doctest: +SKIP
  """
  return datetime.now(cfg.get_local_timezone())


def _should_run_seal_for_yesterday(now: datetime) -> bool:
  """
  Internal helper to check whether we should run seal for yesterday.
  
  Args:
    now (datetime): Now.
  
  Returns:
    bool: True or False for this check.
  
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
  Run the loop.
  
  Args:
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    None
  
  Examples:
    >>> run_loop(None)  # doctest: +SKIP
  """
  marker = os.path.join(cfg.get_syslog_logs_archive_path(), ".last_seal_yyyymmdd")
  while True:
    try:
      now = _local_now()
      if _should_run_seal_for_yesterday(now):
        yday = (now.date() - timedelta(days=1))
        key = yday.isoformat()
        last = None
        if os.path.isfile(marker):
          with open(marker, encoding="utf-8") as f:
            last = f.read().strip()
        if last != key:
          if seal_day(yday, log_fn=log_fn):
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
              f.write(key)
    except Exception as exc:
      log_fn("seal_syslog_daily: error %s" % (exc,))
    time.sleep(_sleep_seconds())


def main(argv: Any | None = None) -> Any:
  """
  Run this module's command-line entrypoint.
  
  Args:
    argv (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> main(None)  # doctest: +SKIP
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
