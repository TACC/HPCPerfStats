"""Run zstd for daily archive compress/decompress (native .zst and --format=gzip)."""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
from collections.abc import Iterator
from typing import BinaryIO

from hpcperfstats.dbload.archive_compress import (
    ARCHIVE_ZSTD_LONG_FLAG,
    zstd_long_flags_for_bytes,
    zstd_use_long_for_path,
)
from hpcperfstats.print_utils import log_print

_GZIP_FORMAT = ("--format=gzip",)


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
  return ["-T%d" % max(1, int(thread_count))]


def _long_args_for_path(path: str, use_long: bool) -> list[str]:
  if use_long and zstd_use_long_for_path(path, True):
    return [ARCHIVE_ZSTD_LONG_FLAG]
  return []


def _long_args_for_bytes(byte_count: int, long_enabled: bool) -> list[str]:
  return zstd_long_flags_for_bytes(byte_count, long_enabled)


def _wait_decompress_proc(proc: subprocess.Popen, args: list) -> None:
  rc = proc.wait()
  if rc != 0:
    if os.name == "posix" and rc in (-signal.SIGPIPE, 128 + signal.SIGPIPE):
      return
    raise subprocess.CalledProcessError(rc, args)


@contextlib.contextmanager
def _decompress_stdout(
    cmd: list[str],
) -> Iterator[BinaryIO]:
  proc = subprocess.Popen(
      cmd,
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
  )
  assert proc.stdout is not None
  try:
    yield proc.stdout
  finally:
    proc.stdout.close()
    _wait_decompress_proc(proc, cmd)


def zstd_decompress_verbose(
    zst_path: str,
    thread_count: int,
    *,
    use_long: bool | None = None,
) -> subprocess.CompletedProcess:
  """In-place ``zstd -d`` on ``.zst``; log stdout/stderr."""
  if use_long is None:
    use_long = zstd_use_long_for_path(zst_path, True)
  cmd = [
      zstd_executable(),
      "-d",
      "-v",
      "-f",
      "--rm",
      *_thread_args(thread_count),
      *_long_args_for_path(zst_path, use_long),
      "-q",
      zst_path,
  ]
  result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      check=False,
  )
  if result.stdout:
    log_print(result.stdout)
  if result.stderr:
    log_print(result.stderr)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )
  return result


@contextlib.contextmanager
def zstd_decompress_stdout(
    zst_path: str,
    thread_count: int,
    *,
    use_long: bool | None = None,
) -> Iterator[BinaryIO]:
  if use_long is None:
    use_long = zstd_use_long_for_path(zst_path, True)
  cmd = [
      zstd_executable(),
      "-d",
      "-c",
      *_thread_args(thread_count),
      *_long_args_for_path(zst_path, use_long),
      "-q",
      zst_path,
  ]
  with _decompress_stdout(cmd) as stdout:
    yield stdout


def zstd_test(
    zst_path: str,
    thread_count: int,
    *,
    use_long: bool | None = None,
) -> subprocess.CompletedProcess:
  if use_long is None:
    use_long = zstd_use_long_for_path(zst_path, True)
  cmd = [
      zstd_executable(),
      "-t",
      *_thread_args(thread_count),
      *_long_args_for_path(zst_path, use_long),
      "-q",
      zst_path,
  ]
  result = subprocess.run(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        stderr=result.stderr,
    )
  return result


def zstd_gzip_decompress_verbose(
    gz_path: str,
    thread_count: int,
) -> subprocess.CompletedProcess:
  cmd = [
      zstd_executable(),
      "-d",
      *_GZIP_FORMAT,
      "-v",
      "-f",
      "--rm",
      *_thread_args(thread_count),
      "-q",
      gz_path,
  ]
  result = subprocess.run(cmd, capture_output=True, text=True, check=False)
  if result.stdout:
    log_print(result.stdout)
  if result.stderr:
    log_print(result.stderr)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )
  return result


@contextlib.contextmanager
def zstd_gzip_decompress_stdout(
    gz_path: str,
    thread_count: int,
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
  with _decompress_stdout(cmd) as stdout:
    yield stdout


def zstd_gzip_test(gz_path: str, thread_count: int) -> subprocess.CompletedProcess:
  cmd = [
      zstd_executable(),
      "-t",
      *_GZIP_FORMAT,
      *_thread_args(thread_count),
      "-q",
      gz_path,
  ]
  result = subprocess.run(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        stderr=result.stderr,
    )
  return result


def zstd_compress_tar_to_file(
    tar_path: str,
    zst_path: str,
    thread_count: int,
    compress_level: int,
    *,
    long_enabled: bool,
) -> None:
  """Compress ``tar_path`` to ``zst_path`` (caller manages temp/replace)."""
  try:
    tar_bytes = os.path.getsize(tar_path)
  except OSError:
    tar_bytes = 0
  cmd = [
      zstd_executable(),
      *_thread_args(thread_count),
      "-%d" % int(compress_level),
      "-q",
      "-o",
      zst_path,
      "--size-hint=%d" % int(tar_bytes),
      *_long_args_for_bytes(tar_bytes, long_enabled),
      tar_path,
  ]
  result = subprocess.run(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        stderr=result.stderr,
    )
