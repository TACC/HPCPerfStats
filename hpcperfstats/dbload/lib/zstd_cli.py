"""Run zstd for daily archive compress/decompress (native .zst and --format=gzip)."""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import BinaryIO

from hpcperfstats.dbload.lib.archive_compress import (
    DAILY_ARCHIVE_GZ_SUFFIX,
    DAILY_ARCHIVE_ZST_SUFFIX,
    detect_compressed_format,
)
from hpcperfstats.dbload.lib.file_locking import file_write_lock
from hpcperfstats.dbload.lib.print_utils import log_print

_GZIP_FORMAT = ("--format=gzip",)
_PRIORITY_TOOLS_WARNED = False


def _page_cache_hints_enabled() -> bool:
  if sys.platform != "linux":
    return False
  if not hasattr(os, "posix_fadvise"):
    return False
  from hpcperfstats.dbload.lib import conf_parser as cfg_mod

  return cfg_mod.get_archive_zstd_drop_page_cache()


def _advise_path(path: str, advice: int) -> None:
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
  if not _page_cache_hints_enabled():
    return
  _advise_path(path, os.POSIX_FADV_SEQUENTIAL)


def _advise_drop_cache(path: str) -> None:
  if not _page_cache_hints_enabled():
    return
  _advise_path(path, os.POSIX_FADV_DONTNEED)


def zstd_drop_page_cache_for_paths(*paths: str) -> None:
  """Drop Linux page cache for archive paths after one-shot zstd I/O."""
  if not _page_cache_hints_enabled():
    return
  seen: set[str] = set()
  for path in paths:
    if not path or path in seen:
      continue
    seen.add(path)
    _advise_drop_cache(path)


def zstd_executable() -> str:
  return shutil.which("zstd") or "/usr/bin/zstd"


def zstd_gzip_supported() -> bool:
  """True when the zstd binary reports gzip in supported formats."""
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
  # Combined ``-T#`` form: separate ``-T`` and ``N`` makes zstd treat ``N`` as a path.
  # ``0`` means zstd auto (all logical cores for this process).
  if int(thread_count) == 0:
    return ["-T0"]
  return ["-T%d" % max(1, int(thread_count))]


def _archive_zstd_priority_settings():
  from hpcperfstats.dbload.lib import conf_parser as cfg_mod

  return (
      cfg_mod.get_archive_zstd_nice(),
      cfg_mod.get_archive_zstd_ionice_class(),
      cfg_mod.get_archive_zstd_ionice_level(),
  )


def zstd_thread_cli_args(thread_count: int) -> list[str]:
  return _thread_args(thread_count)


def _wrap_zstd_cmd(cmd: list[str]) -> list[str]:
  """Prefix archive zstd with ionice/nice when configured and tools exist."""
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
  return _wrap_zstd_cmd(cmd)


def _maybe_wrap_zstd_cmd(cmd: list[str], *, apply_priority_wrap: bool) -> list[str]:
  if apply_priority_wrap:
    return _wrap_zstd_cmd(cmd)
  return list(cmd)


def _tar_list_executable() -> str:
  return shutil.which("tar") or "/bin/tar"


def _tar_readable_via_decompress_tar_pipe(
    decompress_cmd: list[str],
    tar_bin: str,
    *,
    input_path: str | None = None,
) -> bool:
  """Full list scan: ``decompress -c | tar tf -`` (both must exit 0)."""
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
        stderr=subprocess.PIPE,
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
  """Return True when ``zstd -d -c | tar tf -`` succeeds for a sealed daily archive."""
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


def _run_zstd(cmd: list[str], *, apply_priority_wrap: bool = True, **kwargs):
  return subprocess.run(
      _maybe_wrap_zstd_cmd(cmd, apply_priority_wrap=apply_priority_wrap),
      **kwargs,
  )


def _popen_zstd(
    cmd: list[str],
    *,
    apply_priority_wrap: bool = True,
    **kwargs,
) -> subprocess.Popen:
  return subprocess.Popen(
      _maybe_wrap_zstd_cmd(cmd, apply_priority_wrap=apply_priority_wrap),
      **kwargs,
  )


def _verify_uncompressed_tar_readable(tar_path: str) -> bool:
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
) -> bool:
  """Decompress to a verified sibling ``.tar``; unlink compressed only on success."""
  if not compressed_path or not os.path.isfile(compressed_path):
    return False
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_daily_tar_restore_in_progress,
      set_daily_tar_restore_in_progress,
  )

  day = calendar_date_from_daily_tar_path(tar_path or "")
  day_token = day.isoformat() if day is not None else ""
  if day_token:
    set_daily_tar_restore_in_progress(
        day_token,
        reason=restore_reason,
        caller=restore_caller,
    )
  try:
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
    try:
      with file_write_lock(tar_path):
        os.replace(tmp_path, tar_path)
    except OSError:
      try:
        if os.path.exists(tmp_path):
          os.remove(tmp_path)
      except OSError:
        pass
      return False
    zstd_drop_page_cache_for_paths(compressed_path, tar_path)
    if remove_compressed:
      try:
        with file_write_lock(compressed_path):
          if os.path.isfile(compressed_path):
            os.remove(compressed_path)
      except OSError:
        return False
    return True
  finally:
    if day_token:
      clear_daily_tar_restore_in_progress(
          day_token,
          ok=os.path.isfile(tar_path),
          reason=restore_reason,
      )


def _wait_decompress_proc(proc: subprocess.Popen, args: list) -> None:
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
  """Restore sibling ``.tar`` from ``.tar.zst`` using the safe decompress helper."""
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
  """Restore sibling ``.tar`` from legacy ``.tar.gz`` using the safe decompress helper."""
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


def zstd_gzip_test(gz_path: str, thread_count: int) -> subprocess.CompletedProcess:
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


def zstd_compress_tar_to_file(
    tar_path: str,
    zst_path: str,
    thread_count: int,
    compress_level: int,
    *,
    tgz_archive_dir: str = "",
    yield_phase: str = "seal",
) -> None:
  """Compress ``tar_path`` to ``zst_path`` (caller manages temp/replace).

  When ``tgz_archive_dir`` is set, polls for ingest hot signals every 5s during
  the zstd subprocess and raises ``DayCloseYieldError`` cooperatively.
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
    proc = _popen_zstd(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    last_poll = time.monotonic()
    try:
      while proc.poll() is None:
        try:
          last_poll, _ = check_day_close_yield_or_continue(
              tar_path,
              last_poll_monotonic=last_poll,
              tgz_archive_dir=tgz_archive_dir,
              phase=yield_phase,
          )
        except DayCloseYieldError:
          proc.terminate()
          try:
            proc.wait(timeout=5.0)
          except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
          try:
            if os.path.isfile(zst_path):
              os.remove(zst_path)
          except OSError:
            pass
          raise
        time.sleep(0.25)
      if proc.returncode != 0:
        stderr = proc.stderr.read() if proc.stderr is not None else b""
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
    result = _run_zstd(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
      raise subprocess.CalledProcessError(
          result.returncode,
          result.args,
          stderr=result.stderr,
      )
  zstd_drop_page_cache_for_paths(tar_path, zst_path)
