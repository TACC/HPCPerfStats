"""
Run zstd for daily archive compress/decompress (native .zst and --format=gzip).

Attributes:
  _GZIP_FORMAT: Attribute.
  _PRIORITY_TOOLS_WARNED: Attribute.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, BinaryIO

from hpcperfstats.dbload.lib.archive_compress import (
    DAILY_ARCHIVE_GZ_SUFFIX,
    DAILY_ARCHIVE_ZST_SUFFIX,
    detect_compressed_format,
)
from hpcperfstats.dbload.lib.file_locking import file_write_lock, try_file_write_lock
from hpcperfstats.dbload.lib.print_utils import log_print

_GZIP_FORMAT = ("--format=gzip",)
_PRIORITY_TOOLS_WARNED = False


def _page_cache_hints_enabled() -> bool:
  """
  Internal helper to handle page cache hints enabled.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _page_cache_hints_enabled()  # doctest: +SKIP
  """
  if sys.platform != "linux":
    return False
  if not hasattr(os, "posix_fadvise"):
    return False
  from hpcperfstats.dbload.lib import conf_parser as cfg_mod

  return cfg_mod.get_archive_zstd_drop_page_cache()


def _advise_path(path: str, advice: int) -> None:
  """
  Internal helper to handle advise path.
  
  Args:
    path (str): String for path.
    advice (int): Integer value for advice.
  
  Returns:
    None
  
  Examples:
    >>> _advise_path("x", 0)  # doctest: +SKIP
  """
  if not path or not _page_cache_hints_enabled():
    return
  try:
    fd = os.open(path, os.O_RDONLY)
  except OSError:
    return
  try:
    os.posix_fadvise(fd, 0, 0, advice)
  except (OSError, AttributeError):
    pass
  finally:
    os.close(fd)


def _advise_sequential_read(path: str) -> None:
  """
  Internal helper to handle advise sequential read.
  
  Args:
    path (str): String for path.
  
  Returns:
    None
  
  Examples:
    >>> _advise_sequential_read("x")  # doctest: +SKIP
  """
  if not _page_cache_hints_enabled():
    return
  _advise_path(path, os.POSIX_FADV_SEQUENTIAL)


def _advise_drop_cache(path: str) -> None:
  """
  Internal helper to handle advise drop cache.
  
  Args:
    path (str): String for path.
  
  Returns:
    None
  
  Examples:
    >>> _advise_drop_cache("x")  # doctest: +SKIP
  """
  if not _page_cache_hints_enabled():
    return
  _advise_path(path, os.POSIX_FADV_DONTNEED)


def zstd_drop_page_cache_for_paths(*paths: str) -> None:
  """
  Drop Linux page cache for archive paths after one-shot zstd I/O.
  
  Args:
    *paths (str): Variadic positional values for ``paths``; element types
    match the helper's documented protocol.
  
  Returns:
    None
  
  Examples:
    >>> zstd_drop_page_cache_for_paths()  # doctest: +SKIP
  """
  if not _page_cache_hints_enabled():
    return
  seen: set[str] = set()
  for path in paths:
    if not path or path in seen:
      continue
    seen.add(path)
    _advise_drop_cache(path)


def zstd_executable() -> str:
  """
  Zstd executable.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> zstd_executable()  # doctest: +SKIP
  """
  return shutil.which("zstd") or "/usr/bin/zstd"


def zstd_gzip_supported() -> bool:
  """
  True when the zstd binary reports gzip in supported formats.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> zstd_gzip_supported()  # doctest: +SKIP
  """
  try:
    result = subprocess.run(
        [zstd_executable(), "-vV"],
        capture_output=True,
        text=True,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return False
  combined = (result.stdout or "") + (result.stderr or "")
  return "gzip" in combined.lower()


def _thread_args(thread_count: int) -> list[str]:
  """
  Internal helper to handle thread args.
  
  Args:
    thread_count (int): Integer value for thread count.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _thread_args(0)  # doctest: +SKIP
  """
  if int(thread_count) == 0:
    return ["-T0"]
  return ["-T%d" % max(1, int(thread_count))]


def _archive_zstd_priority_settings() -> Any:
  """
  Internal helper to archive the zstd priority settings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_zstd_priority_settings()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib import conf_parser as cfg_mod

  return (
      cfg_mod.get_archive_zstd_nice(),
      cfg_mod.get_archive_zstd_ionice_class(),
      cfg_mod.get_archive_zstd_ionice_level(),
  )


def zstd_thread_cli_args(thread_count: int) -> list[str]:
  """
  Zstd thread cli args.
  
  Args:
    thread_count (int): Integer value for thread count.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> zstd_thread_cli_args(0)  # doctest: +SKIP
  """
  return _thread_args(thread_count)


def _wrap_zstd_cmd(cmd: list[str]) -> list[str]:
  """
  Prefix archive zstd with ionice/nice when configured and tools exist.
  
  Args:
    cmd (list[str]): Sequence for cmd.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _wrap_zstd_cmd([])  # doctest: +SKIP
  """
  global _PRIORITY_TOOLS_WARNED
  nice_inc, ionice_class, ionice_level = _archive_zstd_priority_settings()
  prefix: list[str] = []
  if ionice_class in (1, 2, 3):
    ionice_bin = shutil.which("ionice")
    if ionice_bin:
      prefix.extend([
          ionice_bin,
          "-c%d" % int(ionice_class),
          "-n%d" % max(0, min(7, int(ionice_level))),
      ])
    elif not _PRIORITY_TOOLS_WARNED:
      log_print(
          "archive zstd: ionice not on PATH; skipping I/O priority wrapper",
          flush=True,
      )
      _PRIORITY_TOOLS_WARNED = True
  if nice_inc > 0:
    nice_bin = shutil.which("nice")
    if nice_bin:
      prefix.extend([nice_bin, "-n%d" % int(nice_inc)])
    elif not _PRIORITY_TOOLS_WARNED:
      log_print(
          "archive zstd: nice not on PATH; skipping CPU priority wrapper",
          flush=True,
      )
      _PRIORITY_TOOLS_WARNED = True
  if prefix:
    return prefix + list(cmd)
  return list(cmd)


def wrap_archive_zstd_cmd(cmd: list[str]) -> list[str]:
  """
  Wrap archive zstd cmd.
  
  Args:
    cmd (list[str]): Sequence for cmd.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> wrap_archive_zstd_cmd([])  # doctest: +SKIP
  """
  return _wrap_zstd_cmd(cmd)


def _maybe_wrap_zstd_cmd(
  cmd: list[str],
  *,
  apply_priority_wrap: bool,
) -> list[str]:
  """
  Internal helper to handle maybe wrap zstd cmd.
  
  Args:
    cmd (list[str]): Sequence for cmd.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _maybe_wrap_zstd_cmd([], True)  # doctest: +SKIP
  """
  if apply_priority_wrap:
    return _wrap_zstd_cmd(cmd)
  return list(cmd)


def _tar_list_executable() -> str:
  """
  Internal helper to handle tar list executable.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _tar_list_executable()  # doctest: +SKIP
  """
  return shutil.which("tar") or "/bin/tar"


def _tar_readable_via_decompress_tar_pipe(
  decompress_cmd: list[str],
  tar_bin: str,
  *,
  input_path: str | None = None,
) -> bool:
  """
  Full list scan: ``decompress -c | tar tf -`` (both must exit 0).
  
  Args:
    decompress_cmd (list[str]): Sequence for decompress cmd.
    tar_bin (str): String for tar bin.
    input_path (str | None): One of ``str``, ``None``.
  
  Returns:
    bool: True or False for this check.
  
  Raises:
    Exception: Raised when ``_tar_readable_via_decompress_tar_pipe`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _tar_readable_via_decompress_tar_pipe([], "x", None)  # doctest: +SKIP
  """
  if input_path:
    _advise_sequential_read(input_path)
  p_decomp = subprocess.Popen(
      decompress_cmd,
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
  )
  try:
    p_tar = subprocess.Popen(
        [tar_bin, "tf", "-"],
        stdin=p_decomp.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
  except Exception:
    p_decomp.kill()
    try:
      p_decomp.wait(timeout=30)
    except (OSError, subprocess.SubprocessError):
      pass
    raise
  if p_decomp.stdout is not None:
    p_decomp.stdout.close()
  p_tar.communicate()
  decomp_rc = p_decomp.wait()
  ok = p_tar.returncode == 0 and decomp_rc == 0
  if input_path and ok:
    _advise_drop_cache(input_path)
  return ok


def zstd_compressed_archive_pipe_readable(
  compressed_path: str,
  thread_count: int,
  *,
  apply_priority_wrap: bool = True,
) -> bool:
  """
  Return True when ``zstd -d -c | tar tf -`` succeeds for a sealed daily.
  
    archive.
  
  Args:
    compressed_path (str): String for compressed path.
    thread_count (int): Integer value for thread count.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> zstd_compressed_archive_pipe_readable("x", 0, True)  # doctest: +SKIP
  """
  if not os.path.isfile(compressed_path):
    return False
  tar_bin = _tar_list_executable()
  if compressed_path.endswith(DAILY_ARCHIVE_ZST_SUFFIX) and shutil.which("zstd"):
    cmd = _maybe_wrap_zstd_cmd([
        zstd_executable(),
        "-d",
        "-c",
        *_thread_args(thread_count),
        "-q",
        compressed_path,
    ], apply_priority_wrap=apply_priority_wrap)
    return _tar_readable_via_decompress_tar_pipe(
        cmd,
        tar_bin,
        input_path=compressed_path,
    )
  if compressed_path.endswith(DAILY_ARCHIVE_GZ_SUFFIX) and zstd_gzip_supported():
    cmd = _maybe_wrap_zstd_cmd([
        zstd_executable(),
        "-d",
        "--format=gzip",
        "-c",
        *_thread_args(thread_count),
        "-q",
        compressed_path,
    ], apply_priority_wrap=apply_priority_wrap)
    return _tar_readable_via_decompress_tar_pipe(
        cmd,
        tar_bin,
        input_path=compressed_path,
    )
  return False


def _run_zstd(
  cmd: list[str],
  *,
  apply_priority_wrap: bool = True,
  **kwargs: Any,
) -> Any:
  """
  Internal helper to run the zstd.
  
  Args:
    cmd (list[str]): Sequence for cmd.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
    **kwargs (Any): Extra keyword arguments forwarded to the wrapped API; keys
    and value types match that callee's signature.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _run_zstd([], True)  # doctest: +SKIP
  """
  return subprocess.run(
      _maybe_wrap_zstd_cmd(cmd, apply_priority_wrap=apply_priority_wrap),
      **kwargs,
  )


def _popen_zstd(
  cmd: list[str],
  *,
  apply_priority_wrap: bool = True,
  **kwargs: Any,
) -> subprocess.Popen:
  """
  Internal helper to handle popen zstd.
  
  Args:
    cmd (list[str]): Sequence for cmd.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
    **kwargs (Any): Extra keyword arguments forwarded to the wrapped API; keys
    and value types match that callee's signature.
  
  Returns:
    subprocess.Popen: subprocess.Popen produced by this call.
  
  Examples:
    >>> _popen_zstd([], True)  # doctest: +SKIP
  """
  return subprocess.Popen(
      _maybe_wrap_zstd_cmd(cmd, apply_priority_wrap=apply_priority_wrap),
      **kwargs,
  )


def _verify_uncompressed_tar_readable(tar_path: str) -> bool:
  """
  Internal helper to handle verify uncompressed tar readable.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _verify_uncompressed_tar_readable("x")  # doctest: +SKIP
  """
  tar_bin = _tar_list_executable()
  _advise_sequential_read(tar_path)
  try:
    result = subprocess.run(
        [tar_bin, "tf", tar_path],
        capture_output=True,
        text=True,
        check=False,
    )
    ok = result.returncode == 0
  except (OSError, subprocess.SubprocessError):
    ok = False
  if ok:
    _advise_drop_cache(tar_path)
  return ok


def _decompress_to_path(
  compressed_path: str,
  output_path: str,
  thread_count: int,
) -> None:
  """
  Internal helper to handle decompress to path.
  
  Args:
    compressed_path (str): String for compressed path.
    output_path (str): String for output path.
    thread_count (int): Integer value for thread count.
  
  Returns:
    None
  
  Raises:
    ValueError: Raised when ``_decompress_to_path`` hits a ``ValueError``
    failure path.
    subprocess.CalledProcessError: Raised when ``_decompress_to_path`` hits a
    ``subprocess.CalledProcessError`` failure path.
  
  Examples:
    >>> _decompress_to_path("x", "x", 0)  # doctest: +SKIP
  """
  fmt = detect_compressed_format(compressed_path)
  _advise_sequential_read(compressed_path)
  cmd = [
      zstd_executable(),
      "-d",
      "-f",
      *_thread_args(thread_count),
      "-q",
      "-o",
      output_path,
  ]
  if fmt == "zst":
    cmd.append(compressed_path)
  elif fmt == "gz":
    cmd.extend(_GZIP_FORMAT)
    cmd.append(compressed_path)
  else:
    raise ValueError("unsupported compressed archive format: %s" % compressed_path)
  result = _run_zstd(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        cmd,
        output=result.stdout,
        stderr=result.stderr,
    )
  zstd_drop_page_cache_for_paths(compressed_path, output_path)


def decompress_compressed_to_tar(
  compressed_path: str,
  tar_path: str,
  thread_count: int,
  *,
  remove_compressed: bool = True,
  restore_reason: str = "missing_tar",
  restore_caller: str = "decompress_compressed_to_tar",
  wait_for_other_owner: bool = True,
  zstd_threads: int | None = None,
  num_threads: int | None = None,
  already_locked: bool = False,
) -> bool:
  """
  Decompress to a verified sibling ``.tar``; unlink compressed only on success.
  
  Exclusive ownership: in-process members-store ``daily_tar_restore`` lease
  when the store is installed; otherwise ``{tar}.decomp`` file write lock.
  Losers never touch
  ``.decomp.tmp`` or spawn a second ``zstd -o``. When ``wait_for_other_owner``
  is False (day-close pre_seal), losers return False immediately so the worker
  can defer and free its pool slot.
  
  ``thread_count`` is the canonical public parameter name. ``zstd_threads`` and
  ``num_threads`` are accepted as deprecated keyword aliases only.
  
  Args:
    compressed_path (str): String for compressed path.
    tar_path (str): String for tar path.
    thread_count (int): Integer value for thread count.
    remove_compressed (bool): Boolean flag for remove compressed.
    restore_reason (str): String for restore reason.
    restore_caller (str): String for restore caller.
    wait_for_other_owner (bool): Boolean flag for wait for other owner.
    zstd_threads (int | None): Deprecated alias for ``thread_count``.
    num_threads (int | None): Deprecated alias for ``thread_count``.
    already_locked (bool): Skip the inner ``file_write_lock`` on replace when
      the caller already holds the tar write lock.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> decompress_compressed_to_tar(0)  # doctest: +SKIP
  """
  if zstd_threads is not None:
    thread_count = zstd_threads
  elif num_threads is not None:
    thread_count = num_threads
  if not compressed_path or not os.path.isfile(compressed_path):
    return False
  if os.path.isfile(tar_path):
    return True
  from hpcperfstats.dbload.lib import conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
      invalidate_after_daily_tar_mutation,
      notify_daily_tar_restore_cleared,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
      clear_daily_tar_restore_in_progress,
      renew_daily_tar_restore_lease,
      try_acquire_daily_tar_restore,
      wait_for_daily_tar_restore_before_populate,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
      get_process_archive_members_store,
  )

  day = calendar_date_from_daily_tar_path(tar_path or "")
  day_token = day.isoformat() if day is not None else ""
  lease_value = ""
  use_store_lease = False
  if day_token and get_process_archive_members_store() is not None:
    lease_value = try_acquire_daily_tar_restore(
        day_token,
        reason=restore_reason,
        caller=restore_caller,
    )
    if lease_value:
      use_store_lease = True
    elif not wait_for_other_owner:
      return False
    else:
      wait_for_daily_tar_restore_before_populate(tar_path, log_fn=log_print)
      return os.path.isfile(tar_path)

  renew_stop = threading.Event()
  renew_thread = None

  def _renew_loop() -> None:
    """
    Internal helper to handle renew loop.
    
    Returns:
      None
    
    Examples:
      >>> _renew_loop()  # doctest: +SKIP
    """
    while not renew_stop.wait(60.0):
      if lease_value and day_token:
        renew_daily_tar_restore_lease(day_token, lease_value)

  def _run_owned_restore() -> bool:
    """
    Internal helper to run the owned restore.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _run_owned_restore()  # doctest: +SKIP
    """
    nonlocal renew_thread
    if os.path.isfile(tar_path):
      return True
    if lease_value and day_token:
      renew_thread = threading.Thread(
          target=_renew_loop,
          name="daily-tar-restore-renew",
          daemon=True,
      )
      renew_thread.start()
    tmp_path = "%s.decomp.tmp" % tar_path
    try:
      if os.path.exists(tmp_path):
        os.remove(tmp_path)
    except OSError:
      pass
    try:
      _decompress_to_path(compressed_path, tmp_path, thread_count)
    except (OSError, subprocess.CalledProcessError, ValueError):
      try:
        if os.path.exists(tmp_path):
          os.remove(tmp_path)
      except OSError:
        pass
      return False
    if not _verify_uncompressed_tar_readable(tmp_path):
      try:
        if os.path.exists(tmp_path):
          os.remove(tmp_path)
      except OSError:
        pass
      return False
    # Sealed membership maps are untrusted; drop pre-identity L1/Redis before
    # replace so warm sealed+tar=None keys cannot skip post-restore populate.
    invalidate_after_daily_tar_mutation(
        compressed_path,
        reason="tar_restore_pre",
        log_fn=log_print,
    )
    try:
      if already_locked:
        os.replace(tmp_path, tar_path)
      else:
        with file_write_lock(tar_path):
          os.replace(tmp_path, tar_path)
    except (OSError, TimeoutError):
      try:
        if os.path.exists(tmp_path):
          os.remove(tmp_path)
      except OSError:
        pass
      return False
    zstd_drop_page_cache_for_paths(compressed_path, tar_path)
    remove_ok = True
    if remove_compressed:
      try:
        with file_write_lock(compressed_path):
          if os.path.isfile(compressed_path):
            os.remove(compressed_path)
      except OSError:
        remove_ok = False
    # Post-identity drop (tar present; sealed may be gone when remove_compressed).
    invalidate_after_daily_tar_mutation(
        tar_path,
        reason="tar_restore",
        log_fn=log_print,
    )
    return remove_ok

  held_file_lock = False
  try:
    if use_store_lease:
      return _run_owned_restore()
    decomp_lock_target = "%s.decomp" % tar_path
    lease_s = max(60.0, float(cfg.get_sync_daily_tar_restore_lease_seconds()))
    if wait_for_other_owner:
      try:
        with file_write_lock(decomp_lock_target, timeout_seconds=lease_s):
          held_file_lock = True
          return _run_owned_restore()
      except TimeoutError:
        return False
    try:
      with try_file_write_lock(decomp_lock_target):
        held_file_lock = True
        return _run_owned_restore()
    except TimeoutError:
      return False
  finally:
    renew_stop.set()
    if renew_thread is not None and renew_thread.is_alive():
      renew_thread.join(timeout=1.0)
    if day_token and lease_value:
      clear_daily_tar_restore_in_progress(
          day_token,
          token=lease_value,
          ok=os.path.isfile(tar_path),
          reason=restore_reason,
      )
    elif held_file_lock and day_token:
      try:
        notify_daily_tar_restore_cleared(day_token)
      except Exception:
        pass


def _wait_decompress_proc(proc: subprocess.Popen, args: list) -> None:
  """
  Internal helper to wait for the decompress proc.
  
  Args:
    proc (subprocess.Popen): Proc.
    args (list): Sequence for args.
  
  Returns:
    None
  
  Raises:
    subprocess.CalledProcessError: Raised when ``_wait_decompress_proc`` hits
    a ``subprocess.CalledProcessError`` failure path.
  
  Examples:
    >>> _wait_decompress_proc(None, [])  # doctest: +SKIP
  """
  rc = proc.wait()
  if rc != 0:
    if os.name == "posix" and rc in (-signal.SIGPIPE, 128 + signal.SIGPIPE):
      return
    raise subprocess.CalledProcessError(rc, args)


@contextlib.contextmanager
def _decompress_stdout(
  cmd: list[str],
  *,
  apply_priority_wrap: bool = True,
  input_path: str | None = None,
) -> Iterator[BinaryIO]:
  """
  Internal helper to handle decompress stdout.
  
  Args:
    cmd (list[str]): Sequence for cmd.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
    input_path (str | None): One of ``str``, ``None``.
  
  Yields:
    Iterator[BinaryIO]: Iterator[BinaryIO] produced by this call.
  
  Examples:
    >>> _decompress_stdout([], True, None)  # doctest: +SKIP
  """
  if input_path:
    _advise_sequential_read(input_path)
  proc = _popen_zstd(
      cmd,
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
      apply_priority_wrap=apply_priority_wrap,
  )
  assert proc.stdout is not None
  try:
    yield proc.stdout
  finally:
    proc.stdout.close()
    _wait_decompress_proc(proc, cmd)
    if input_path:
      _advise_drop_cache(input_path)


def zstd_decompress_verbose(
  zst_path: str,
  thread_count: int,
) -> subprocess.CompletedProcess:
  """
  Restore sibling ``.tar`` from ``.tar.zst`` using the safe decompress helper.
  
  Args:
    zst_path (str): String for zst path.
    thread_count (int): Integer value for thread count.
  
  Returns:
    subprocess.CompletedProcess: subprocess.CompletedProcess produced by this
    call.
  
  Raises:
    ValueError: Raised when ``zstd_decompress_verbose`` hits a ``ValueError``
    failure path.
    subprocess.CalledProcessError: Raised when ``zstd_decompress_verbose``
    hits a ``subprocess.CalledProcessError`` failure path.
  
  Examples:
    >>> zstd_decompress_verbose("x", 0)  # doctest: +SKIP
  """
  if not zst_path.endswith(DAILY_ARCHIVE_ZST_SUFFIX):
    raise ValueError("expected .tar.zst path: %s" % zst_path)
  tar_path = zst_path[: -len(DAILY_ARCHIVE_ZST_SUFFIX)] + ".tar"
  ok = decompress_compressed_to_tar(zst_path, tar_path, thread_count)
  if not ok:
    raise subprocess.CalledProcessError(1, [zstd_executable(), "-d", zst_path])
  return subprocess.CompletedProcess([zstd_executable(), "-d", zst_path], 0)


@contextlib.contextmanager
def zstd_decompress_stdout(
  zst_path: str,
  thread_count: int,
  *,
  apply_priority_wrap: bool = True,
) -> Iterator[BinaryIO]:
  """
  Zstd decompress stdout.
  
  Args:
    zst_path (str): String for zst path.
    thread_count (int): Integer value for thread count.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Yields:
    Iterator[BinaryIO]: Iterator[BinaryIO] produced by this call.
  
  Examples:
    >>> zstd_decompress_stdout("x", 0, True)  # doctest: +SKIP
  """
  cmd = [
      zstd_executable(),
      "-d",
      "-c",
      *_thread_args(thread_count),
      "-q",
      zst_path,
  ]
  with _decompress_stdout(
      cmd,
      apply_priority_wrap=apply_priority_wrap,
      input_path=zst_path,
  ) as stdout:
    yield stdout


def zstd_test(
  zst_path: str,
  thread_count: int,
  *,
  apply_priority_wrap: bool = True,
) -> subprocess.CompletedProcess:
  """
  Zstd test.
  
  Args:
    zst_path (str): String for zst path.
    thread_count (int): Integer value for thread count.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Returns:
    subprocess.CompletedProcess: subprocess.CompletedProcess produced by this
    call.
  
  Raises:
    subprocess.CalledProcessError: Raised when ``zstd_test`` hits a
    ``subprocess.CalledProcessError`` failure path.
  
  Examples:
    >>> zstd_test("x", 0, True)  # doctest: +SKIP
  """
  _advise_sequential_read(zst_path)
  cmd = [
      zstd_executable(),
      "-t",
      *_thread_args(thread_count),
      "-q",
      zst_path,
  ]
  result = _run_zstd(
      cmd,
      capture_output=True,
      text=True,
      check=False,
      apply_priority_wrap=apply_priority_wrap,
  )
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        stderr=result.stderr,
    )
  _advise_drop_cache(zst_path)
  return result


def zstd_gzip_decompress_verbose(
  gz_path: str,
  thread_count: int,
) -> subprocess.CompletedProcess:
  """
  Restore sibling ``.tar`` from legacy ``.tar.gz`` using the safe decompress.
  
    helper.
  
  Args:
    gz_path (str): String for gz path.
    thread_count (int): Integer value for thread count.
  
  Returns:
    subprocess.CompletedProcess: subprocess.CompletedProcess produced by this
    call.
  
  Raises:
    ValueError: Raised when ``zstd_gzip_decompress_verbose`` hits a
    ``ValueError`` failure path.
    subprocess.CalledProcessError: Raised when
    ``zstd_gzip_decompress_verbose`` hits a ``subprocess.CalledProcessError``
    failure path.
  
  Examples:
    >>> zstd_gzip_decompress_verbose("x", 0)  # doctest: +SKIP
  """
  if not gz_path.endswith(DAILY_ARCHIVE_GZ_SUFFIX):
    raise ValueError("expected .tar.gz path: %s" % gz_path)
  tar_path = gz_path[: -len(DAILY_ARCHIVE_GZ_SUFFIX)] + ".tar"
  ok = decompress_compressed_to_tar(gz_path, tar_path, thread_count)
  if not ok:
    raise subprocess.CalledProcessError(1, [zstd_executable(), "-d", gz_path])
  return subprocess.CompletedProcess([zstd_executable(), "-d", gz_path], 0)


@contextlib.contextmanager
def zstd_gzip_decompress_stdout(
  gz_path: str,
  thread_count: int,
  *,
  apply_priority_wrap: bool = True,
) -> Iterator[BinaryIO]:
  """
  Zstd gzip decompress stdout.
  
  Args:
    gz_path (str): String for gz path.
    thread_count (int): Integer value for thread count.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Yields:
    Iterator[BinaryIO]: Iterator[BinaryIO] produced by this call.
  
  Examples:
    >>> zstd_gzip_decompress_stdout("x", 0, True)  # doctest: +SKIP
  """
  cmd = [
      zstd_executable(),
      "-d",
      *_GZIP_FORMAT,
      "-c",
      *_thread_args(thread_count),
      "-q",
      gz_path,
  ]
  with _decompress_stdout(
      cmd,
      apply_priority_wrap=apply_priority_wrap,
      input_path=gz_path,
  ) as stdout:
    yield stdout


def zstd_gzip_test(
  gz_path: str,
  thread_count: int,
) -> subprocess.CompletedProcess:
  """
  Zstd gzip test.
  
  Args:
    gz_path (str): String for gz path.
    thread_count (int): Integer value for thread count.
  
  Returns:
    subprocess.CompletedProcess: subprocess.CompletedProcess produced by this
    call.
  
  Raises:
    subprocess.CalledProcessError: Raised when ``zstd_gzip_test`` hits a
    ``subprocess.CalledProcessError`` failure path.
  
  Examples:
    >>> zstd_gzip_test("x", 0)  # doctest: +SKIP
  """
  _advise_sequential_read(gz_path)
  cmd = [
      zstd_executable(),
      "-t",
      *_GZIP_FORMAT,
      *_thread_args(thread_count),
      "-q",
      gz_path,
  ]
  result = _run_zstd(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        stderr=result.stderr,
    )
  _advise_drop_cache(gz_path)
  return result


def drain_subprocess_pipes(
  proc: Any,
  timeout_s: float = 1.0,
) -> tuple[bytes, bytes]:
  """
  Read stdout and stderr so a cooperative poll loop cannot deadlock.

  Uses ``os.read`` on real pipe fds and ``read()`` on file-like mocks
  (unit tests). Reader threads are daemons so a wedged peer cannot hang
  the caller past ``timeout_s``.

  Args:
    proc (Any): Subprocess or mock with ``stdout`` / ``stderr`` streams.
    timeout_s (float): Join budget for the reader threads.

  Returns:
    tuple[bytes, bytes]: ``(stdout_bytes, stderr_bytes)`` drained so far.

  Examples:
    >>> import io
    >>> from types import SimpleNamespace
    >>> out, err = drain_subprocess_pipes(
    ...   SimpleNamespace(stdout=io.BytesIO(b"a"), stderr=io.BytesIO(b"b")),
    ...   timeout_s=0.5,
    ... )
    >>> out == b"a" and err == b"b"
    True
  """
  stdout_chunks: list[bytes] = []
  stderr_chunks: list[bytes] = []

  def _read_stream(stream: Any, bucket: list[bytes]) -> None:
    """
    Drain one pipe or file-like into ``bucket``.

    Args:
      stream (Any): ``stdout``/``stderr`` handle or mock.
      bucket (list[bytes]): Destination chunks.

    Returns:
      None

    Examples:
      >>> _read_stream(None, [])
    """
    if stream is None:
      return
    try:
      fileno = getattr(stream, "fileno", None)
      fd = None
      if callable(fileno):
        try:
          fd = int(fileno())
        except (TypeError, ValueError, OSError, io.UnsupportedOperation):
          fd = None
      if fd is not None:
        import select
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
          remaining = deadline - time.monotonic()
          if remaining <= 0:
            break
          ready, _, _ = select.select([fd], [], [], min(0.05, remaining))
          if not ready:
            continue
          chunk = os.read(fd, 65536)
          if not chunk:
            break
          bucket.append(chunk)
        return
      data = stream.read()
      if data:
        bucket.append(data if isinstance(data, (bytes, bytearray)) else bytes(data))
    except Exception:
      return

  readers = [
      threading.Thread(
          target=_read_stream,
          args=(getattr(proc, "stdout", None), stdout_chunks),
          daemon=True,
      ),
      threading.Thread(
          target=_read_stream,
          args=(getattr(proc, "stderr", None), stderr_chunks),
          daemon=True,
      ),
  ]
  for thread in readers:
    thread.start()
  join_s = max(0.01, float(timeout_s))
  for thread in readers:
    thread.join(timeout=join_s)
  return b"".join(stdout_chunks), b"".join(stderr_chunks)


def zstd_compress_tar_to_file(
  tar_path: str,
  zst_path: str,
  thread_count: int,
  compress_level: int,
  *,
  tgz_archive_dir: str = "",
  yield_phase: str = "seal",
) -> None:
  """
  Compress ``tar_path`` to ``zst_path`` (caller manages temp/replace).
  
  When ``tgz_archive_dir`` is set, polls for ingest hot signals every 5s during
  the zstd subprocess and raises ``DayCloseYieldError`` cooperatively.
  
  Args:
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    thread_count (int): Integer value for thread count.
    compress_level (int): Integer value for compress level.
    tgz_archive_dir (str): String for tgz archive dir.
    yield_phase (str): String for yield phase.
  
  Returns:
    None
  
  Raises:
    Exception: Raised when ``zstd_compress_tar_to_file`` hits a ``Exception``
    failure path.
    ProgressIdleError: When zstd output size is idle for the stall window.
    subprocess.CalledProcessError: Raised when ``zstd_compress_tar_to_file``
    hits a ``subprocess.CalledProcessError`` failure path.
  
  Examples:
    >>> zstd_compress_tar_to_file("x", "x", 0, 0, "x", "x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
      DayCloseYieldError,
      check_day_close_yield_or_continue,
  )

  try:
    tar_bytes = os.path.getsize(tar_path)
  except OSError:
    tar_bytes = 0
  _advise_sequential_read(tar_path)
  cmd = [
      zstd_executable(),
      *_thread_args(thread_count),
      "-%d" % int(compress_level),
      "-q",
      "-o",
      zst_path,
      "--size-hint=%d" % int(tar_bytes),
      tar_path,
  ]
  if tgz_archive_dir:
    from hpcperfstats.dbload.lib.sync_timedb_progress_io import (
        ProgressIdleError,
        log_progress_sop,
        _kill_process_group,
    )
    import hpcperfstats.dbload.lib.conf_parser as cfg

    proc = _popen_zstd(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    last_poll = time.monotonic()
    last_size = 0
    try:
      last_size = int(os.path.getsize(zst_path)) if os.path.isfile(zst_path) else 0
    except OSError:
      last_size = 0
    last_progress = time.monotonic()
    idle_s = float(cfg.get_sync_ingest_stall_idle_s())
    stdout_acc = bytearray()
    stderr_acc = bytearray()
    try:
      while proc.poll() is None:
        out_chunk, err_chunk = drain_subprocess_pipes(proc, timeout_s=0.05)
        stdout_acc.extend(out_chunk)
        stderr_acc.extend(err_chunk)
        try:
          cur = int(os.path.getsize(zst_path)) if os.path.isfile(zst_path) else 0
        except OSError:
          cur = last_size
        now = time.monotonic()
        advancing = cur > last_size
        if advancing:
          last_size = cur
          last_progress = now
        log_progress_sop(
            stage="zstd_compress",
            path=zst_path,
            advancing=advancing,
            idle_s=now - last_progress,
            last_progress=last_progress,
            metric="bytes",
        )
        if idle_s > 0.0 and (now - last_progress) >= idle_s:
          _kill_process_group(proc)
          raise ProgressIdleError(
              "zstd compress idle stall path=%s idle_s=%.1f"
              % (zst_path, now - last_progress),
              idle_s=now - last_progress,
              path=zst_path,
          )
        try:
          last_poll, _ = check_day_close_yield_or_continue(
              tar_path,
              last_poll_monotonic=last_poll,
              tgz_archive_dir=tgz_archive_dir,
              phase=yield_phase,
          )
        except DayCloseYieldError:
          _kill_process_group(proc)
          drain_subprocess_pipes(proc, timeout_s=0.5)
          try:
            if os.path.isfile(zst_path):
              os.remove(zst_path)
          except OSError:
            pass
          raise
        time.sleep(0.25)
      out_chunk, err_chunk = drain_subprocess_pipes(proc, timeout_s=1.0)
      stdout_acc.extend(out_chunk)
      stderr_acc.extend(err_chunk)
      if proc.returncode != 0:
        stderr = bytes(stderr_acc)
        raise subprocess.CalledProcessError(
            proc.returncode,
            cmd,
            stderr=stderr.decode("utf-8", errors="replace"),
        )
    finally:
      if proc.stderr is not None:
        proc.stderr.close()
      if proc.stdout is not None:
        proc.stdout.close()
  else:
    # Non-cooperative callers (unit / one-shot): keep ``_run_zstd``. Production
    # seal passes ``tgz_archive_dir`` and uses progress + idle kill above.
    result = _run_zstd(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
      raise subprocess.CalledProcessError(
          result.returncode,
          result.args,
          stderr=result.stderr,
      )
  zstd_drop_page_cache_for_paths(tar_path, zst_path)
