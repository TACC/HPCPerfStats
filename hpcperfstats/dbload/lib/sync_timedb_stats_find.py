"""GNU find -printf discovery for sync_timedb raw stats files.

Emits path/mtime/size/inode from find itself so discovery does not re-stat those
fields in Python. Fail-closed when GNU find / -printf is unavailable.

On macOS host tests, prefer Homebrew ``gfind`` (findutils) via PATH order or
``HPCPERFSTATS_FIND_BIN`` — do not emulate find.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from hpcperfstats.dbload.lib.file_locking import LOCK_SUFFIX

# -printf format string for argv: find interprets \0 as NUL (do not embed real NULs —
# Python subprocess rejects embedded null bytes in argv on some platforms).
FIND_PRINTF_FORMAT = "%p\\0%T@\\0%s\\0%i\\0"
FIND_CURRENT_INODE_PRINTF = "%p\\0%i\\0"

_FNCTL_LOCK_ENOENT_RE = re.compile(
    r"No such file or directory.*\.fnctl\.lock|"
    r"\.fnctl\.lock.*No such file or directory",
    re.I,
)

# Last successful find scan → fingerprint caches for maint hints (C9).
_path_fp_cache: Dict[str, Tuple[int, int]] = {}
_host_fp_cache: Dict[str, Tuple[int, int]] = {}


@dataclass(frozen=True)
class FindStatsRecord:
  path: str
  mtime: float
  size: int
  inode: int


class FindStatsDiscoveryError(RuntimeError):
  """Raised when GNU find discovery cannot run (fail-closed)."""


def clear_fingerprint_caches() -> None:
  _path_fp_cache.clear()
  _host_fp_cache.clear()


def lookup_path_fingerprint(path: str) -> Optional[Tuple[int, int]]:
  return _path_fp_cache.get(os.path.normpath(path))


def lookup_host_dir_fingerprint(host_dir: str) -> Optional[Tuple[int, int]]:
  return _host_fp_cache.get(os.path.normpath(host_dir))


def update_fingerprint_caches_from_records(
    records: Sequence[FindStatsRecord],
) -> None:
  """Refresh path/host fingerprint caches from find -printf records."""
  _path_fp_cache.clear()
  host_agg: Dict[str, List[FindStatsRecord]] = {}
  for rec in records:
    norm = os.path.normpath(rec.path)
    _path_fp_cache[norm] = (int(rec.mtime), int(rec.size))
    host_dir = os.path.dirname(norm)
    host_agg.setdefault(host_dir, []).append(rec)
  _host_fp_cache.clear()
  for host_dir, recs in host_agg.items():
    count = len(recs)
    max_mtime = max(int(r.mtime) for r in recs) if recs else 0
    _host_fp_cache[host_dir] = (max_mtime, count)


def build_find_stats_argv(
    archive_dir: str,
    *,
    mtime_days: Optional[int] = None,
    find_bin: str = "find",
) -> List[str]:
  """Build GNU find argv for archive stats discovery (-mindepth/maxdepth 2)."""
  argv = [
      find_bin,
      archive_dir,
      "-mindepth",
      "2",
      "-maxdepth",
      "2",
      "-type",
      "f",
  ]
  if mtime_days is not None and int(mtime_days) > 0:
    argv.extend(["-mtime", "-%d" % int(mtime_days)])
  argv.extend(
      [
          "!",
          "-name",
          ".*",
          "!",
          "-name",
          "current*",
          "-printf",
          FIND_PRINTF_FORMAT,
      ]
  )
  return argv


def build_find_current_inode_argv(
    archive_dir: str,
    *,
    find_bin: str = "find",
) -> List[str]:
  """Find host ``current`` files and emit ``path\\0inode\\0``."""
  return [
      find_bin,
      archive_dir,
      "-mindepth",
      "2",
      "-maxdepth",
      "2",
      "-type",
      "f",
      "-name",
      "current",
      "-printf",
      FIND_CURRENT_INODE_PRINTF,
  ]


def parse_find_printf_records(data: bytes) -> List[FindStatsRecord]:
  """Parse NUL records produced by GNU find ``-printf`` with ``FIND_PRINTF_FORMAT``."""
  if not data:
    return []
  parts = data.split(b"\0")
  # Trailing empty from final NUL is expected.
  while parts and parts[-1] == b"":
    parts.pop()
  if len(parts) % 4 != 0:
    raise FindStatsDiscoveryError(
        "find -printf record stream length is not a multiple of 4 fields "
        "(got %d tokens)" % len(parts)
    )
  records: List[FindStatsRecord] = []
  for i in range(0, len(parts), 4):
    path_b, mtime_b, size_b, inode_b = parts[i : i + 4]
    try:
      path = os.fsdecode(path_b)
      mtime = float(mtime_b)
      size = int(size_b)
      inode = int(inode_b)
    except (ValueError, TypeError) as exc:
      raise FindStatsDiscoveryError(
          "invalid find -printf record at index %d: %s" % (i // 4, exc)
      ) from exc
    records.append(
        FindStatsRecord(path=path, mtime=mtime, size=size, inode=inode)
    )
  return records


def parse_current_inode_records(data: bytes) -> Dict[str, int]:
  """Map host_dir → inode for ``current`` files (path\\0inode\\0)."""
  if not data:
    return {}
  parts = data.split(b"\0")
  while parts and parts[-1] == b"":
    parts.pop()
  if len(parts) % 2 != 0:
    raise FindStatsDiscoveryError(
        "find current inode stream length is not a multiple of 2 fields"
    )
  out: Dict[str, int] = {}
  for i in range(0, len(parts), 2):
    path = os.fsdecode(parts[i])
    inode = int(parts[i + 1])
    out[os.path.dirname(os.path.normpath(path))] = inode
  return out


def _stderr_is_only_fnctl_races(stderr: str) -> bool:
  lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
  if not lines:
    return True
  for line in lines:
    if "fnctl.lock" in line and (
        "No such file or directory" in line or "cannot" in line.lower()
    ):
      continue
    if _FNCTL_LOCK_ENOENT_RE.search(line):
      continue
    return False
  return True


def _resolve_find_bin(find_bin: Optional[str] = None) -> str:
  """Resolve GNU find binary (prefer ``gfind`` on macOS Homebrew)."""
  if find_bin:
    candidate = find_bin
  else:
    env_bin = (os.environ.get("HPCPERFSTATS_FIND_BIN") or "").strip()
    candidate = env_bin or None

  if candidate:
    if os.path.isabs(candidate) or os.sep in candidate:
      if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        raise FindStatsDiscoveryError("find binary not found: %s" % candidate)
      return candidate
    resolved = shutil.which(candidate)
    if not resolved:
      raise FindStatsDiscoveryError(
          "find binary not found on PATH: %s" % candidate
      )
    return resolved

  # Prefer Homebrew gfind (GNU findutils) before BSD /usr/bin/find on macOS.
  for name in ("gfind", "find"):
    resolved = shutil.which(name)
    if resolved:
      return resolved
  raise FindStatsDiscoveryError(
      "GNU find not found on PATH (install findutils / gfind; "
      "required for stats discovery)"
  )


def _run_find_capture(
    argv: Sequence[str],
    *,
    allow_fnctl_race_exit: bool = True,
) -> bytes:
  try:
    proc = subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
  except FileNotFoundError as exc:
    raise FindStatsDiscoveryError(
        "GNU find not found (required for stats discovery)"
    ) from exc
  stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace")
  if proc.returncode == 0:
    return proc.stdout or b""
  if (
      allow_fnctl_race_exit
      and proc.returncode == 1
      and _stderr_is_only_fnctl_races(stderr_text)
  ):
    return proc.stdout or b""
  lower = stderr_text.lower()
  if "unknown primary" in lower or ("printf" in lower and "illegal" in lower):
    raise FindStatsDiscoveryError(
        "find does not support -printf (GNU findutils required): %s"
        % stderr_text.strip()
    )
  raise FindStatsDiscoveryError(
      "find failed exit=%d: %s"
      % (proc.returncode, stderr_text.strip() or "(no stderr)")
  )


def run_find_stats(
    archive_dir: str,
    *,
    mtime_days: Optional[int] = None,
    find_bin: Optional[str] = None,
    log_fn: Optional[Callable[..., None]] = None,
) -> List[FindStatsRecord]:
  """Run GNU find and return parsed stats records (fail-closed)."""
  if not archive_dir or not os.path.isdir(archive_dir):
    return []
  t0 = time.monotonic()
  bin_path = _resolve_find_bin(find_bin)
  argv = build_find_stats_argv(
      archive_dir, mtime_days=mtime_days, find_bin=bin_path
  )
  raw = _run_find_capture(argv)
  records = parse_find_printf_records(raw)
  update_fingerprint_caches_from_records(records)
  if log_fn is not None:
    log_fn(
        "find_stats paths=%d elapsed_s=%.3f mtime_days=%s"
        % (
            len(records),
            time.monotonic() - t0,
            "None" if mtime_days is None else str(int(mtime_days)),
        ),
        flush=True,
    )
  return records


def load_current_inode_map(
    archive_dir: str,
    *,
    find_bin: Optional[str] = None,
) -> Dict[str, int]:
  """Return host_dir → inode for each host ``current`` file via find."""
  if not archive_dir or not os.path.isdir(archive_dir):
    return {}
  bin_path = _resolve_find_bin(find_bin)
  argv = build_find_current_inode_argv(archive_dir, find_bin=bin_path)
  raw = _run_find_capture(argv, allow_fnctl_race_exit=True)
  return parse_current_inode_records(raw)


def _is_lock_name(name: str) -> bool:
  return name.endswith(LOCK_SUFFIX) or name.endswith(".lock")


def filter_and_sort_find_records(
    records: Iterable[FindStatsRecord],
    host_name_ext: str,
    startdate,
    enddate,
    current_inodes: Optional[Dict[str, int]] = None,
    *,
    newest_first: bool = False,
) -> List[FindStatsRecord]:
  """Filter find records by host suffix / locks / active inode / date; sort."""
  suffix = (host_name_ext or "").strip()
  if not suffix:
    return []
  current_inodes = current_inodes or {}
  selected: List[Tuple[FindStatsRecord, Optional[int]]] = []
  for rec in records:
    path = rec.path
    name = os.path.basename(path)
    host_dir = os.path.dirname(path)
    host_base = os.path.basename(host_dir)
    if not host_base.endswith(suffix):
      continue
    if name.startswith(".") or name.startswith("current"):
      continue
    if _is_lock_name(name):
      continue
    if current_inodes.get(host_dir) == rec.inode:
      continue

    fdate_mtime = datetime.fromtimestamp(int(rec.mtime))
    fdate_name = None
    sort_epoch: Optional[int] = None
    try:
      fname_epoch = int(name)
      fdate_name = datetime.fromtimestamp(fname_epoch)
      sort_epoch = fname_epoch
    except (TypeError, ValueError):
      sort_epoch = int(rec.mtime)

    if startdate in ("all", "current"):
      selected.append((rec, sort_epoch))
      continue

    def _in_range(ts):
      if ts is None:
        return False
      return not (ts <= startdate - timedelta(days=1) or ts > enddate)

    if not (_in_range(fdate_mtime) or _in_range(fdate_name)):
      continue
    selected.append((rec, sort_epoch))

  selected.sort(key=lambda item: (item[1] is None, item[1]))
  if newest_first:
    selected.reverse()
  return [rec for rec, _ in selected]


def discover_stats_records(
    archive_dir: str,
    startdate,
    enddate,
    host_name_ext: str,
    *,
    mtime_days: Optional[int] = None,
    newest_first: bool = False,
    find_bin: Optional[str] = None,
    log_fn: Optional[Callable[..., None]] = None,
) -> List[FindStatsRecord]:
  """Full discovery pipeline: find → current inode map → filter/sort."""
  records = run_find_stats(
      archive_dir,
      mtime_days=mtime_days,
      find_bin=find_bin,
      log_fn=log_fn,
  )
  current_inodes = load_current_inode_map(archive_dir, find_bin=find_bin)
  return filter_and_sort_find_records(
      records,
      host_name_ext,
      startdate,
      enddate,
      current_inodes,
      newest_first=newest_first,
  )
