"""
fd/fdfind ``-X`` GNU stat discovery for sync_timedb raw stats files.

Emits path/mtime/size/inode from GNU ``stat --printf`` so discovery does not
re-stat those fields in Python. Fail-closed when ``fdfind``/``fd`` or GNU
stat ``--printf`` is unavailable. There is no GNU find fallback.

On macOS host tests, prefer Homebrew ``fd`` via PATH order or
``HPCPERFSTATS_FIND_BIN``; the image ships Debian ``fdfind``.

Attributes:
  FD_BATCH_SIZE: Attribute.
  FD_THREADS: Attribute.
  JID_NEIGHBOR_FILES: Attribute.
  STAT_CURRENT_INODE_PRINTF: Attribute.
  STAT_PRINTF_FORMAT: Attribute.
  _FNCTL_LOCK_ENOENT_RE: Attribute.
  _host_fp_cache: Attribute.
  _path_fp_cache: Attribute.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from hpcperfstats.dbload.lib.file_locking import LOCK_SUFFIX
import hpcperfstats.dbload.lib.conf_parser as cfg

# GNU stat --printf argv: interpret \0 as NUL (do not embed real NULs —
# Python subprocess rejects embedded null bytes in argv on some platforms).
STAT_PRINTF_FORMAT = "%n\\0%Y\\0%s\\0%i\\0"
STAT_CURRENT_INODE_PRINTF = "%n\\0%i\\0"
FD_THREADS = 4
FD_BATCH_SIZE = 1000

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
  """
  Hold FindStatsRecord state and behavior.
  
  Attributes:
    inode: ``inode``.
    mtime: ``mtime``.
    path: ``path``.
    size: ``size``.
  """
  path: str
  mtime: float
  size: int
  inode: int


class FindStatsDiscoveryError(RuntimeError):
  """
  Raised when fd/fdfind -X GNU stat discovery cannot run (fail-closed).
  """


def clear_fingerprint_caches() -> None:
  """
  Clear fingerprint caches.
  
  Returns:
    None
  
  Examples:
    >>> clear_fingerprint_caches()  # doctest: +SKIP
  """
  _path_fp_cache.clear()
  _host_fp_cache.clear()


def lookup_path_fingerprint(path: str) -> Optional[Tuple[int, int]]:
  """
  Lookup path fingerprint.
  
  Args:
    path (str): String for path.
  
  Returns:
    Optional[Tuple[int, int]]: Optional[Tuple[int, int]] — the result, or None
    when unavailable.
  
  Examples:
    >>> lookup_path_fingerprint("x")  # doctest: +SKIP
  """
  return _path_fp_cache.get(os.path.normpath(path))


def lookup_host_dir_fingerprint(host_dir: str) -> Optional[Tuple[int, int]]:
  """
  Lookup host dir fingerprint.
  
  Args:
    host_dir (str): String for host dir.
  
  Returns:
    Optional[Tuple[int, int]]: Optional[Tuple[int, int]] — the result, or None
    when unavailable.
  
  Examples:
    >>> lookup_host_dir_fingerprint("x")  # doctest: +SKIP
  """
  return _host_fp_cache.get(os.path.normpath(host_dir))


def update_fingerprint_caches_from_records(
  records: Sequence[FindStatsRecord],
) -> None:
  """
  Refresh path/host fingerprint caches from fd -X GNU stat records.
  
  Args:
    records (Sequence[FindStatsRecord]): records as
    ``Sequence[FindStatsRecord]``.
  
  Returns:
    None
  
  Examples:
    >>> update_fingerprint_caches_from_records([])  # doctest: +SKIP
  """
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


def host_dir_is_internal_for_stats_discovery(host_dir: str) -> bool:
  """
  Return True when the archive child dir is dot-prefixed (not a monitor host).

  Monitor host directories never start with ``.``; sidecars and metadata trees
  under ``archive/`` do (``.sync_timedb_day_raw_removal``, etc.).

  Args:
    host_dir (str): Parent directory of a depth-2 stats file under ``archive/``.

  Returns:
    bool: True when ``host_dir`` basename starts with ``.``.

  Examples:
    >>> host_dir_is_internal_for_stats_discovery("/a/.sync_timedb_day_raw_removal")
    True
    >>> host_dir_is_internal_for_stats_discovery("/a/.hidden_sidecar")
    True
    >>> host_dir_is_internal_for_stats_discovery("/a/i614.host.example.edu")
    False
  """
  base = os.path.basename(os.path.normpath(str(host_dir or "")))
  if not base or base in (".", ".."):
    return True
  return base.startswith(".")


def is_internal_archive_stats_path(path: str) -> bool:
  """
  Return True when ``path`` is under a dot-prefixed archive child (not a host).

  Args:
    path (str): Candidate closed raw stats path under ``archive/``.

  Returns:
    bool: True when discover/ingest must ignore the path.

  Examples:
    >>> is_internal_archive_stats_path(
    ...   "/archive/.sync_timedb_day_raw_removal/2026-08-07.json"
    ... )
    True
    >>> is_internal_archive_stats_path(
    ...   "/archive/i614.host.example.edu/1787359835"
    ... )
    False
  """
  if not path:
    return False
  return host_dir_is_internal_for_stats_discovery(os.path.dirname(path))


def _fd_x_stat_argv(
  *,
  find_bin: str,
  stat_bin: str,
  search_path: str,
  min_depth: int,
  max_depth: int,
  printf_format: str,
  pattern: str = ".",
  glob: bool = False,
  exclude: Optional[str] = None,
  mtime_days: Optional[int] = None,
) -> List[str]:
  """
  Build one ``fd``/``fdfind -X`` GNU stat argv (no shell, no xargs).

  Args:
    find_bin (str): Walker binary (``fdfind`` or ``fd``).
    stat_bin (str): GNU ``stat`` / ``gstat`` binary.
    search_path (str): Directory to walk (absolutized).
    min_depth (int): fd ``--min-depth``.
    max_depth (int): fd ``--max-depth``.
    printf_format (str): GNU ``stat --printf`` format with ``\\0`` escapes.
    pattern (str): fd search pattern (``.`` or glob ``current``).
    glob (bool): When True, pass ``--glob``.
    exclude (Optional[str]): Optional fd ``--exclude`` glob.
    mtime_days (Optional[int]): Optional ``--changed-within Nd`` window.

  Returns:
    List[str]: argv for a single ``Popen`` (``-X`` last before ``stat``).

  Examples:
    >>> _fd_x_stat_argv(
    ...   find_bin="fd",
    ...   stat_bin="stat",
    ...   search_path="/a",
    ...   min_depth=2,
    ...   max_depth=2,
    ...   printf_format=STAT_PRINTF_FORMAT,
    ... )[0]
    'fd'
  """
  argv = [
      find_bin,
      "--threads",
      str(int(FD_THREADS)),
      "--no-ignore",
      "--absolute-path",
      "--max-depth",
      str(int(max_depth)),
      "--min-depth",
      str(int(min_depth)),
      "--type",
      "f",
  ]
  if glob:
    argv.append("--glob")
  if exclude:
    argv.extend(["--exclude", str(exclude)])
  if mtime_days is not None and int(mtime_days) > 0:
    argv.extend(["--changed-within", "%dd" % int(mtime_days)])
  argv.extend(
      [
          "--batch-size",
          str(int(FD_BATCH_SIZE)),
          pattern,
          os.path.abspath(search_path),
          "-X",
          stat_bin,
          "--printf=" + printf_format,
      ]
  )
  return argv


def build_find_stats_argv(
  archive_dir: str,
  *,
  mtime_days: Optional[int] = None,
  find_bin: str = "fd",
  stat_bin: str = "stat",
) -> List[str]:
  """
  Build fd ``-X`` GNU stat argv for archive stats discovery (depth 2).

  Args:
    archive_dir (str): String for archive dir.
    mtime_days (Optional[int]): Mtime days, or None when absent.
    find_bin (str): Walker binary.
    stat_bin (str): GNU stat binary.

  Returns:
    List[str]: List[str] produced by this call.

  Examples:
    >>> "--threads" in build_find_stats_argv("/a", find_bin="fd")
    True
  """
  return _fd_x_stat_argv(
      find_bin=find_bin,
      stat_bin=stat_bin,
      search_path=archive_dir,
      min_depth=2,
      max_depth=2,
      printf_format=STAT_PRINTF_FORMAT,
      exclude="current*",
      mtime_days=mtime_days,
  )


def build_find_current_inode_argv(
  archive_dir: str,
  *,
  find_bin: str = "fd",
  stat_bin: str = "stat",
) -> List[str]:
  """
  Find host ``current`` files and emit ``path`` + inode via GNU stat.

  Args:
    archive_dir (str): String for archive dir.
    find_bin (str): Walker binary.
    stat_bin (str): GNU stat binary.

  Returns:
    List[str]: List[str] produced by this call.

  Examples:
    >>> "--glob" in build_find_current_inode_argv("/a", find_bin="fd")
    True
  """
  return _fd_x_stat_argv(
      find_bin=find_bin,
      stat_bin=stat_bin,
      search_path=archive_dir,
      min_depth=2,
      max_depth=2,
      printf_format=STAT_CURRENT_INODE_PRINTF,
      pattern="current",
      glob=True,
  )


def build_find_host_scoped_argv(
  host_dir: str,
  *,
  find_bin: str = "fd",
  stat_bin: str = "stat",
) -> List[str]:
  """
  Build fd ``-X`` GNU stat argv for one host directory (depth 1).

  Args:
    host_dir (str): Absolute or relative host directory under archive.
    find_bin (str): Walker binary.
    stat_bin (str): GNU stat binary.

  Returns:
    List[str]: List[str] produced by this call.

  Examples:
    >>> build_find_host_scoped_argv("/a/h", find_bin="fd")[0]
    'fd'
  """
  return _fd_x_stat_argv(
      find_bin=find_bin,
      stat_bin=stat_bin,
      search_path=host_dir,
      min_depth=1,
      max_depth=1,
      printf_format=STAT_PRINTF_FORMAT,
  )


def parse_find_printf_records(data: bytes) -> List[FindStatsRecord]:
  """
  Parse NUL records produced by GNU ``stat --printf`` with
  ``STAT_PRINTF_FORMAT``.

  Args:
    data (bytes): Full find stdout buffer (may be empty).

  Returns:
    List[FindStatsRecord]: Parsed records in stream order.

  Raises:
    FindStatsDiscoveryError: When token count is not a multiple of 4 or a
      field fails to parse.

  Examples:
    >>> parse_find_printf_records(b"/a\\x001.5\\x0010\\x009\\x00")[0].path
    '/a'
  """
  return list(iter_find_printf_records_streaming([data] if data else []))


def iter_find_printf_records_streaming(
  chunks: Iterable[bytes],
) -> Iterator[FindStatsRecord]:
  """
  Yield fd ``-X`` GNU stat records as each four-field NUL group completes.

  Does not wait for the producer to finish: the first complete record is
  yielded as soon as its trailing NUL arrives, even when more chunks follow.
  Trailing incomplete fields after the final chunk raise
  :class:`FindStatsDiscoveryError`.

  Args:
    chunks (Iterable[bytes]): Successive stdout chunks (empty chunks allowed).

  Yields:
    FindStatsRecord: One record per complete ``path/mtime/size/inode`` group.

  Raises:
    FindStatsDiscoveryError: On invalid field values or leftover incomplete
      fields at end of stream.

  Examples:
    >>> recs = list(
    ...   iter_find_printf_records_streaming(
    ...     [b"/a\\x001.0\\x001\\x002\\x00", b"/b\\x002.0\\x003\\x004\\x00"]
    ...   )
    ... )
    >>> [r.path for r in recs]
    ['/a', '/b']
  """
  buf = b""
  fields: List[bytes] = []
  index = 0
  for chunk in chunks:
    if chunk:
      buf += chunk
    while True:
      nul = buf.find(b"\0")
      if nul < 0:
        break
      fields.append(buf[:nul])
      buf = buf[nul + 1 :]
      if len(fields) < 4:
        continue
      path_b, mtime_b, size_b, inode_b = fields
      fields = []
      try:
        path = os.fsdecode(path_b)
        mtime = float(mtime_b)
        size = int(size_b)
        inode = int(inode_b)
      except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise FindStatsDiscoveryError(
            "invalid fd -X stat record at index %d: %s" % (index, exc)
        ) from exc
      index += 1
      yield FindStatsRecord(
          path=path, mtime=mtime, size=size, inode=inode
      )
  if fields or buf:
    raise FindStatsDiscoveryError(
        "fd -X stat record stream length is not a multiple of 4 fields "
        "(got %d leftover tokens, %d leftover bytes)"
        % (len(fields) + (1 if buf else 0), len(buf))
    )


def parse_current_inode_records(data: bytes) -> Dict[str, int]:
  """
  Map host_dir → inode for ``current`` files (pathinode).
  
  Args:
    data (bytes): Data.
  
  Returns:
    Dict[str, int]: Dict[str, int] produced by this call.
  
  Raises:
    FindStatsDiscoveryError: Raised when ``parse_current_inode_records`` hits
    a ``FindStatsDiscoveryError`` failure path.
  
  Examples:
    >>> parse_current_inode_records(None)  # doctest: +SKIP
  """
  if not data:
    return {}
  parts = data.split(b"\0")
  while parts and parts[-1] == b"":
    parts.pop()
  if len(parts) % 2 != 0:
    raise FindStatsDiscoveryError(
        "fd -X stat current inode stream length is not a multiple of 2 fields"
    )
  out: Dict[str, int] = {}
  for i in range(0, len(parts), 2):
    path = os.fsdecode(parts[i])
    inode = int(parts[i + 1])
    out[os.path.dirname(os.path.normpath(path))] = inode
  return out


def _stderr_is_only_fnctl_races(stderr: str) -> bool:
  """
  Internal helper to handle stderr is only fnctl races.
  
  Args:
    stderr (str): String for stderr.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _stderr_is_only_fnctl_races("x")  # doctest: +SKIP
  """
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


def _resolve_named_binary(candidate: str, *, kind: str) -> str:
  """
  Resolve an executable path or PATH name.

  Args:
    candidate (str): Absolute path, relative path, or PATH basename.
    kind (str): Label for error messages (``fd/fdfind`` or ``GNU stat``).

  Returns:
    str: Resolved executable path.

  Raises:
    FindStatsDiscoveryError: When the binary is missing or not executable.

  Examples:
    >>> _resolve_named_binary("/bin/sh", kind="fd/fdfind")  # doctest: +SKIP
  """
  if os.path.isabs(candidate) or os.sep in candidate:
    if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
      raise FindStatsDiscoveryError("%s binary not found: %s" % (kind, candidate))
    return candidate
  resolved = shutil.which(candidate)
  if not resolved:
    raise FindStatsDiscoveryError(
        "%s binary not found on PATH: %s" % (kind, candidate)
    )
  return resolved


def _gnu_stat_supports_printf(stat_bin: str) -> bool:
  """
  Return True when ``stat_bin`` accepts GNU ``--printf``.

  Args:
    stat_bin (str): Candidate ``stat`` / ``gstat`` path.

  Returns:
    bool: True when ``--printf=%n`` succeeds against ``stat_bin``.

  Examples:
    >>> isinstance(_gnu_stat_supports_printf("/bin/sh"), bool)
    True
  """
  try:
    proc = subprocess.run(
        [stat_bin, "--printf=%n", stat_bin],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=5,
    )
  except (OSError, subprocess.TimeoutExpired):
    return False
  return proc.returncode == 0 and bool(proc.stdout)


def _resolve_find_bin(find_bin: Optional[str] = None) -> str:
  """
  Resolve the archive walker (``fdfind`` then Homebrew ``fd``). Never GNU find.

  Args:
    find_bin (Optional[str]): Walker override, or None to use env then PATH.

  Returns:
    str: Resolved ``fdfind`` or ``fd`` path.

  Raises:
    FindStatsDiscoveryError: When no walker is available.

  Examples:
    >>> _resolve_find_bin("fd")  # doctest: +SKIP
  """
  if find_bin:
    candidate = find_bin
  else:
    env_bin = (os.environ.get("HPCPERFSTATS_FIND_BIN") or "").strip()
    candidate = env_bin or None

  if candidate:
    return _resolve_named_binary(candidate, kind="fd/fdfind")

  for name in ("fdfind", "fd"):
    resolved = shutil.which(name)
    if resolved:
      return resolved
  raise FindStatsDiscoveryError(
      "fd/fdfind not found on PATH (install fd-find in the image or fd on "
      "the host; required for stats discovery)"
  )


def _resolve_stat_bin(stat_bin: Optional[str] = None) -> str:
  """
  Resolve GNU ``stat`` (prefer Homebrew ``gstat``, then ``stat``).

  Args:
    stat_bin (Optional[str]): Override path or PATH name, or None.

  Returns:
    str: Resolved GNU stat path that supports ``--printf``.

  Raises:
    FindStatsDiscoveryError: When GNU ``stat --printf`` is unavailable.

  Examples:
    >>> _resolve_stat_bin(None)  # doctest: +SKIP
  """
  if stat_bin:
    candidates = [stat_bin]
  else:
    candidates = ["gstat", "stat"]
  last_error = "GNU stat --printf not found"
  for name in candidates:
    try:
      resolved = _resolve_named_binary(name, kind="GNU stat")
    except FindStatsDiscoveryError as exc:
      last_error = str(exc)
      continue
    if _gnu_stat_supports_printf(resolved):
      return resolved
    last_error = (
        "GNU stat --printf is required for stats discovery (rejected %s)"
        % resolved
    )
  raise FindStatsDiscoveryError(last_error)


def _run_find_capture(
  argv: Sequence[str],
  *,
  allow_fnctl_race_exit: bool = True,
) -> bytes:
  """
  Internal helper to run the find capture.
  
  Args:
    argv (Sequence[str]): Sequence for argv.
    allow_fnctl_race_exit (bool): Boolean flag for allow fnctl race exit.
  
  Returns:
    bytes: bytes produced by this call.
  
  Raises:
    FindStatsDiscoveryError: Raised when ``_run_find_capture`` hits a
    ``FindStatsDiscoveryError`` failure path.
  
  Examples:
    >>> _run_find_capture([], True)  # doctest: +SKIP
  """
  try:
    proc = subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
  except FileNotFoundError as exc:
    raise FindStatsDiscoveryError(
        "fd/fdfind or GNU stat not found (required for stats discovery)"
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
  raise FindStatsDiscoveryError(
      "fd/fdfind -X stat failed exit=%d: %s"
      % (proc.returncode, stderr_text.strip() or "(no stderr)")
  )


def iter_find_stats_stdout_chunks(
  archive_dir: str,
  *,
  mtime_days: Optional[int] = None,
  find_bin: Optional[str] = None,
  stat_bin: Optional[str] = None,
  chunk_size: int = 65536,
) -> Iterator[bytes]:
  """
  Run fd ``-X`` GNU stat and yield stdout chunks as they arrive (streaming).

  Does not buffer the entire walker output before the first yield. Callers
  should feed chunks into
  :func:`iter_find_printf_records_streaming` / discover enqueue helpers.

  Args:
    archive_dir (str): Archive data directory (walker root).
    mtime_days (Optional[int]): Optional ``--changed-within`` window.
    find_bin (Optional[str]): Override walker binary path.
    stat_bin (Optional[str]): Override GNU stat binary path.
    chunk_size (int): Read size for ``stdout`` (minimum 1).

  Yields:
    bytes: Successive non-empty stdout chunks from GNU stat via fd ``-X``.

  Raises:
    FindStatsDiscoveryError: When fd/fdfind or GNU stat is missing or the
      walker exits non-zero (except benign fnctl race exit 1).

  Examples:
    >>> list(iter_find_stats_stdout_chunks("/nope"))
    []
  """
  if not archive_dir or not os.path.isdir(archive_dir):
    return
  bin_path = _resolve_find_bin(find_bin)
  stat_path = _resolve_stat_bin(stat_bin)
  argv = build_find_stats_argv(
      archive_dir,
      mtime_days=mtime_days,
      find_bin=bin_path,
      stat_bin=stat_path,
  )
  read_n = max(1, int(chunk_size))
  stderr_file = tempfile.TemporaryFile()
  try:
    proc = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=stderr_file,
    )
  except FileNotFoundError as exc:
    stderr_file.close()
    raise FindStatsDiscoveryError(
        "fd/fdfind or GNU stat not found (required for stats discovery)"
    ) from exc
  assert proc.stdout is not None
  last_progress = time.monotonic()
  idle_s = float(cfg.get_sync_ingest_stall_idle_s())
  bytes_seen = 0
  try:
    while True:
      chunk = proc.stdout.read(read_n)
      if not chunk:
        break
      bytes_seen += len(chunk)
      now = time.monotonic()
      # Unit progress = bytes streamed from walker stdout.
      last_progress = now
      from hpcperfstats.dbload.lib.sync_timedb_progress_io import log_progress_sop

      log_progress_sop(
          stage="find_stats",
          path=str(archive_dir),
          advancing=True,
          idle_s=0.0,
          last_progress=last_progress,
          metric="bytes",
          force=False,
      )
      yield chunk
      if idle_s > 0.0 and (now - last_progress) >= idle_s:
        # Unreachable while chunks arrive; kept for contract clarity.
        pass
  finally:
    try:
      rc = proc.wait()
      stderr_file.seek(0)
      stderr_b = stderr_file.read()
    finally:
      stderr_file.close()
  stderr_text = stderr_b.decode("utf-8", errors="replace")
  if rc == 0:
    return
  if rc == 1 and _stderr_is_only_fnctl_races(stderr_text):
    return
  raise FindStatsDiscoveryError(
      "fd/fdfind -X stat failed exit=%d: %s"
      % (rc, stderr_text.strip() or "(no stderr)")
  )


def run_find_stats(
  archive_dir: str,
  *,
  mtime_days: Optional[int] = None,
  find_bin: Optional[str] = None,
  stat_bin: Optional[str] = None,
  log_fn: Optional[Callable[..., None]] = None,
) -> List[FindStatsRecord]:
  """
  Run fd ``-X`` GNU stat and return parsed stats records (fail-closed).

  Prefer :func:`iter_find_stats_stdout_chunks` for orchestrator boot so
  enqueue can start before the scan finishes.

  Args:
    archive_dir (str): String for archive dir.
    mtime_days (Optional[int]): Mtime days, or None when absent.
    find_bin (Optional[str]): Walker bin, or None when absent.
    stat_bin (Optional[str]): GNU stat bin, or None when absent.
    log_fn (Optional[Callable[..., None]]): Log fn, or None when absent.

  Returns:
    List[FindStatsRecord]: List[FindStatsRecord] produced by this call.

  Examples:
    >>> run_find_stats("x", None, None, None)  # doctest: +SKIP
  """
  if not archive_dir or not os.path.isdir(archive_dir):
    return []
  t0 = time.monotonic()
  bin_path = _resolve_find_bin(find_bin)
  stat_path = _resolve_stat_bin(stat_bin)
  argv = build_find_stats_argv(
      archive_dir,
      mtime_days=mtime_days,
      find_bin=bin_path,
      stat_bin=stat_path,
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
  stat_bin: Optional[str] = None,
) -> Dict[str, int]:
  """
  Return host_dir → inode for each host ``current`` file via fd ``-X`` stat.

  Args:
    archive_dir (str): String for archive dir.
    find_bin (Optional[str]): Walker bin, or None when absent.
    stat_bin (Optional[str]): GNU stat bin, or None when absent.

  Returns:
    Dict[str, int]: Dict[str, int] produced by this call.

  Examples:
    >>> load_current_inode_map("x", None)  # doctest: +SKIP
  """
  if not archive_dir or not os.path.isdir(archive_dir):
    return {}
  bin_path = _resolve_find_bin(find_bin)
  stat_path = _resolve_stat_bin(stat_bin)
  argv = build_find_current_inode_argv(
      archive_dir, find_bin=bin_path, stat_bin=stat_path,
  )
  raw = _run_find_capture(argv, allow_fnctl_race_exit=True)
  return parse_current_inode_records(raw)


def _is_lock_name(name: str) -> bool:
  """
  Internal helper to check if lock name.
  
  Args:
    name (str): String for name.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _is_lock_name("x")  # doctest: +SKIP
  """
  return name.endswith(LOCK_SUFFIX) or name.endswith(".lock")


def filter_and_sort_find_records(
  records: Iterable[FindStatsRecord],
  host_name_ext: str,
  startdate: Any,
  enddate: Any,
  current_inodes: Optional[Dict[str, int]] = None,
  *,
  newest_first: bool = False,
) -> List[FindStatsRecord]:
  """
  Filter find records by host suffix / locks / active inode / date; sort.
  
  Args:
    records (Iterable[FindStatsRecord]): Records.
    host_name_ext (str): String for host name ext.
    startdate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    enddate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    current_inodes (Optional[Dict[str, int]]): Current inodes, or None when
    absent.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    List[FindStatsRecord]: List[FindStatsRecord] produced by this call.
  
  Examples:
    >>> filter_and_sort_find_records(None, "x", None, None, None, True)
  """
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
    if host_dir_is_internal_for_stats_discovery(host_dir):
      continue
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

    if startdate in ("all", "backlog", "current"):
      selected.append((rec, sort_epoch))
      continue

    def _in_range(ts: Any) -> Any:
      """
      Internal helper to handle in range.
      
      Args:
        ts (Any): Time value (``datetime``, ISO string, sentinel, or
        ``None``).
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> _in_range(None)  # doctest: +SKIP
      """
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
  startdate: Any,
  enddate: Any,
  host_name_ext: str,
  *,
  mtime_days: Optional[int] = None,
  newest_first: bool = False,
  find_bin: Optional[str] = None,
  log_fn: Optional[Callable[..., None]] = None,
) -> List[FindStatsRecord]:
  """
  Full discovery pipeline: find → current inode map → filter/sort.
  
  Args:
    archive_dir (str): String for archive dir.
    startdate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    enddate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    host_name_ext (str): String for host name ext.
    mtime_days (Optional[int]): Mtime days, or None when absent.
    newest_first (bool): Boolean flag for newest first.
    find_bin (Optional[str]): Find bin, or None when absent.
    log_fn (Optional[Callable[..., None]]): Log fn, or None when absent.
  
  Returns:
    List[FindStatsRecord]: List[FindStatsRecord] produced by this call.
  
  Examples:
    >>> discover_stats_records("x", None, None, "x", None, True, None, None)
  """
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


# Adjacent files beyond the padded --jid window (per host, sorted by epoch).
JID_NEIGHBOR_FILES = 1


def expand_sorted_records_with_window_neighbors(
  sorted_items: Sequence[Tuple[FindStatsRecord, int]],
  window_start: datetime,
  window_end: datetime,
  *,
  neighbor_files: int = JID_NEIGHBOR_FILES,
) -> List[FindStatsRecord]:
  """
  Keep in-window records plus ±N neighbors from an epoch-sorted host list.
  
  ``sorted_items`` must already be sorted ascending by epoch. When the core
  (in-window) set is empty, take the last file before ``window_start`` and the
  first file after ``window_end`` (when present).
  
  Args:
    sorted_items (Sequence[Tuple[FindStatsRecord, int]]): Sequence for sorted
    items.
    window_start (datetime): Window start.
    window_end (datetime): Window end.
    neighbor_files (int): Integer value for neighbor files.
  
  Returns:
    List[FindStatsRecord]: List[FindStatsRecord] produced by this call.
  
  Examples:
    >>> expand_sorted_records_with_window_neighbors([], None, None, 0)
  """
  if not sorted_items:
    return []
  n = max(0, int(neighbor_files))
  try:
    start_ts = float(window_start.timestamp())
    end_ts = float(window_end.timestamp())
  except (AttributeError, OSError, OverflowError, TypeError, ValueError):
    return []

  epochs = [int(ep) for _, ep in sorted_items]
  core_idxs = [
      i for i, ep in enumerate(epochs)
      if start_ts <= float(ep) <= end_ts
  ]
  take: set[int] = set()
  if core_idxs:
    lo = min(core_idxs)
    hi = max(core_idxs)
    take.update(range(lo, hi + 1))
    for k in range(1, n + 1):
      if lo - k >= 0:
        take.add(lo - k)
      if hi + k < len(sorted_items):
        take.add(hi + k)
  else:
    last_before: Optional[int] = None
    first_after: Optional[int] = None
    for i, ep in enumerate(epochs):
      if float(ep) < start_ts:
        last_before = i
      elif float(ep) > end_ts:
        first_after = i
        break
    if last_before is not None:
      take.add(last_before)
    if first_after is not None:
      take.add(first_after)

  return [sorted_items[i][0] for i in sorted(take)]


def filter_host_scoped_window_records(
  records: Iterable[FindStatsRecord],
  host_fqdns: Sequence[str],
  window_start: datetime,
  window_end: datetime,
  current_inodes: Optional[Dict[str, int]] = None,
  *,
  neighbor_files: int = JID_NEIGHBOR_FILES,
) -> List[FindStatsRecord]:
  """
  Filter records to allowed hosts; keep padded window plus ±N neighbors.
  
  Skips locks, ``current*``, dotfiles, and live ``current`` inodes (same as
  continuous sync). Epoch basename is preferred; mtime is the fallback clock.
  Neighbor expansion runs **independently per host** on that host's
  epoch-sorted eligible list.
  
  Args:
    records (Iterable[FindStatsRecord]): Records.
    host_fqdns (Sequence[str]): Sequence for host fqdns.
    window_start (datetime): Window start.
    window_end (datetime): Window end.
    current_inodes (Optional[Dict[str, int]]): Current inodes, or None when
    absent.
    neighbor_files (int): Integer value for neighbor files.
  
  Returns:
    List[FindStatsRecord]: List[FindStatsRecord] produced by this call.
  
  Examples:
    >>> filter_host_scoped_window_records(None, [], None, None, None, 0)
  """
  allow = {
      str(h).strip()
      for h in (host_fqdns or ())
      if str(h or "").strip()
  }
  if not allow:
    return []
  current_inodes = current_inodes or {}
  by_host: Dict[str, List[Tuple[FindStatsRecord, int]]] = {}
  for rec in records:
    path = rec.path
    name = os.path.basename(path)
    host_dir = os.path.dirname(path)
    host_base = os.path.basename(host_dir)
    if host_dir_is_internal_for_stats_discovery(host_dir):
      continue
    if host_base not in allow:
      continue
    if name.startswith(".") or name.startswith("current"):
      continue
    if _is_lock_name(name):
      continue
    if current_inodes.get(host_dir) == rec.inode:
      continue

    try:
      sort_epoch = int(name)
    except (TypeError, ValueError):
      sort_epoch = int(rec.mtime)
    by_host.setdefault(host_base, []).append((rec, sort_epoch))

  selected: List[Tuple[FindStatsRecord, int]] = []
  for host_base in by_host:
    items = by_host[host_base]
    items.sort(key=lambda item: item[1])
    for rec in expand_sorted_records_with_window_neighbors(
        items,
        window_start,
        window_end,
        neighbor_files=neighbor_files,
    ):
      name = os.path.basename(rec.path)
      try:
        ep = int(name)
      except (TypeError, ValueError):
        ep = int(rec.mtime)
      selected.append((rec, ep))

  selected.sort(key=lambda item: item[1])
  return [rec for rec, _ in selected]


def discover_host_scoped_stats_records(
  archive_dir: str,
  host_fqdns: Sequence[str],
  window_start: datetime,
  window_end: datetime,
  *,
  find_bin: Optional[str] = None,
  log_fn: Optional[Callable[..., None]] = None,
) -> List[FindStatsRecord]:
  """
  Discover stats under named host dirs only (no full-archive walk).

  Runs fd ``-X`` GNU stat per existing ``{archive_dir}/{fqdn}`` directory.
  Missing host dirs are skipped. Does not walk unrelated hosts.
  
  Args:
    archive_dir (str): String for archive dir.
    host_fqdns (Sequence[str]): Sequence for host fqdns.
    window_start (datetime): Window start.
    window_end (datetime): Window end.
    find_bin (Optional[str]): Find bin, or None when absent.
    log_fn (Optional[Callable[..., None]]): Log fn, or None when absent.
  
  Returns:
    List[FindStatsRecord]: List[FindStatsRecord] produced by this call.
  
  Examples:
    >>> discover_host_scoped_stats_records("x", [], None, None, None, None)
  """
  if not archive_dir or not os.path.isdir(archive_dir):
    return []
  hosts = [
      str(h).strip()
      for h in (host_fqdns or ())
      if str(h or "").strip()
  ]
  if not hosts:
    return []
  bin_path = _resolve_find_bin(find_bin)
  stat_path = _resolve_stat_bin(None)
  records: List[FindStatsRecord] = []
  for host in hosts:
    host_dir = os.path.join(archive_dir, host)
    if not os.path.isdir(host_dir):
      if log_fn is not None:
        log_fn(
            "jid discover: skip missing host_dir=%s" % host_dir,
            flush=True,
        )
      continue
    argv = build_find_host_scoped_argv(
        host_dir, find_bin=bin_path, stat_bin=stat_path,
    )
    t0 = time.monotonic()
    raw = _run_find_capture(argv, allow_fnctl_race_exit=True)
    host_recs = parse_find_printf_records(raw)
    if log_fn is not None:
      log_fn(
          "jid discover: host=%s find_records=%d elapsed_s=%.3f"
          % (host, len(host_recs), time.monotonic() - t0),
          flush=True,
      )
    records.extend(host_recs)

  current_inodes = load_current_inode_map(archive_dir, find_bin=bin_path)
  filtered = filter_host_scoped_window_records(
      records,
      hosts,
      window_start,
      window_end,
      current_inodes,
  )
  update_fingerprint_caches_from_records(filtered)
  return filtered


def collect_host_scoped_stats_paths(
  archive_dir: str,
  host_fqdns: Sequence[str],
  window_start: datetime,
  window_end: datetime,
  *,
  find_bin: Optional[str] = None,
  log_fn: Optional[Callable[..., None]] = None,
) -> List[str]:
  """
  Return host-scoped stats paths in padded window plus ±1 file neighbors.
  
  Args:
    archive_dir (str): String for archive dir.
    host_fqdns (Sequence[str]): Sequence for host fqdns.
    window_start (datetime): Window start.
    window_end (datetime): Window end.
    find_bin (Optional[str]): Find bin, or None when absent.
    log_fn (Optional[Callable[..., None]]): Log fn, or None when absent.
  
  Returns:
    List[str]: List[str] produced by this call.
  
  Examples:
    >>> collect_host_scoped_stats_paths("x", [], None, None, None, None)
  """
  return [
      rec.path
      for rec in discover_host_scoped_stats_records(
          archive_dir,
          host_fqdns,
          window_start,
          window_end,
          find_bin=find_bin,
          log_fn=log_fn,
      )
  ]
